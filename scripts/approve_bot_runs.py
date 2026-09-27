#!/usr/bin/env python3
"""Approve fork-bot PR workflow runs that wait for "Approve and run", across the fleet.

    scripts/approve_bot_runs.py [--repo NAME ...] [--dry-run | --yes]

Default is --dry-run: print, per waiting run, what would be approved and why, and change
nothing. --yes approves the runs that pass every check below, with YOUR `gh` login.

Why this exists: the fork bot is an outside user account, so GitHub holds the workflow runs of
its PRs until someone with write access approves them. Approving means running the PR's code
(and its workflow files) in our Actions. The decision must never rest on anyone, human or model,
reading PR titles, bodies, comments, commit messages or branch names: those are text the PR's
author controls, and a prompt-injection or a look-alike PR lives there. So every check here is
made from structured API fields only (numeric ids, booleans, shas, file paths), and a run that
fails any of them is left waiting, with a one-line reason.

A run is approved only if ALL of these hold:
  1. it is a `pull_request` run waiting for approval (conclusion `action_required`);
  2. its actor AND triggering actor are the fork bot, by numeric id (a login can be renamed and
     re-registered; an id cannot);
  3. its head repository is a fork, owned by the fork bot (by id), whose parent is the target;
  4. exactly one open PR matches it, authored by the fork bot (by id, account type `User`),
     from that head repository and branch, at the run's head sha, into the default branch;
  5. every file the PR changes is on ALLOWED_PATHS, none is removed, renamed or copied, no
     workflow file is added, and it changes at most MAX_FILES files;
  6. the target repository is listed in renovate/global.js (FLEET, plus SELF_CONFIGURED).

Widening ALLOWED_PATHS, or any other rule here, is the maintainer's decision.

Only the Python standard library is used. `gh` is called with an argument list, never through a
shell, and every value taken from API output is passed as its own argument or query field.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ORG = "unified-systems-com"

# The fork bot: the machine user Renovate and release-please run as (org-bots' `bots`
# environment variables FORK_BOT_LOGIN / FORK_BOT_ID). Every identity check uses the numeric
# id; the login is only used to build the `head=<owner>:<branch>` PR query, whose results are
# then checked by id. Verify with: gh api users/tap-renovate-remote --jq '{id,type}'
BOT_ID = 334543231
BOT_LOGIN = "tap-renovate-remote"
BOT_TYPE = "User"

# Paths Renovate and release-please legitimately change in a fleet repository. Glob patterns
# over the full path: `*` matches within one path segment, `**/` matches any number of leading
# directories (including none). A name with no directory matches only at the repository root.
ALLOWED_PATHS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "**/*.boot.json",  # boot-record digests
    "**/tap-plugin.toml",
    "CHANGELOG.md",
    ".release-please-manifest.json",
    "release-please-config.json",
    ".env",  # tap's release-please extra-file
    "Dockerfile",
    "renovate.json5",
)
ALLOWED_FILE_STATUSES = ("modified", "changed", "added")
MAX_FILES = 50

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PER_PAGE = 100


# --------------------------------------------------------------------------------------------
# The fleet list, parsed from renovate/global.js without executing it.


class FleetParseError(Exception):
    pass


def _array_block(text: str, const: str) -> str:
    m = re.search(r"^const " + re.escape(const) + r" = \[(.*?)^\];", text, re.S | re.M)
    if not m:
        raise FleetParseError(f"renovate/global.js: no `const {const} = [ ... ];` block")
    # Drop // comments so a commented-out name is not read as listed.
    return re.sub(r"//[^\n]*", "", m.group(1))


def parse_fleet(text: str) -> list[str]:
    """Return the repository names listed in renovate/global.js: FLEET, then SELF_CONFIGURED."""
    fleet = re.findall(r'"([^"\n]*)"', _array_block(text, "FLEET"))
    self_conf = re.findall(r"repository:\s*`\$\{ORG\}/([^`\n]*)`", _array_block(text, "SELF_CONFIGURED"))
    if not fleet:
        raise FleetParseError("renovate/global.js: parsed zero repositories from FLEET")
    names = []
    for n in fleet + self_conf:
        if not NAME_RE.match(n):
            raise FleetParseError(f"renovate/global.js: not a repository name: {n!r}")
        if n not in names:
            names.append(n)
    return names


# --------------------------------------------------------------------------------------------
# The decision.


class Api(Protocol):
    """What the decision needs from GitHub. `GhApi` below; a fixture-backed fake in the tests."""

    def repo(self, full_name: str) -> dict[str, Any]: ...
    def repo_by_id(self, repo_id: int) -> dict[str, Any]: ...
    def pull(self, full_name: str, number: int) -> dict[str, Any]: ...
    def open_pulls_from(self, full_name: str, head_owner: str, head_branch: str) -> list[dict[str, Any]]: ...
    def pull_files(self, full_name: str, number: int) -> list[dict[str, Any]]: ...


@dataclass
class Decision:
    approve: bool
    reason: str
    pr: int | None = None


def _id(obj: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _glob_re(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += r"(?:[^/]+/)*"
            i += 3
        elif pattern[i] == "*":
            out += r"[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


_ALLOWED_RES = tuple(_glob_re(p) for p in ALLOWED_PATHS)


def path_allowed(path: str) -> bool:
    return any(r.match(path) for r in _ALLOWED_RES)


def check_files(files: list[dict[str, Any]], expected_count: Any) -> str | None:
    """Return why the changed files disqualify the PR, or None if they are all allowed."""
    if len(files) > MAX_FILES:
        return f"{len(files)} changed files (max {MAX_FILES})"
    if not isinstance(expected_count, int) or expected_count != len(files):
        return f"listed {len(files)} files but the PR reports changed_files={expected_count}"
    if not files:
        return "the PR changes no files"
    for f in files:
        name, status = f.get("filename"), f.get("status")
        if not isinstance(name, str):
            return "a changed file has no filename"
        if status not in ALLOWED_FILE_STATUSES:
            return f"file status {status!r} not allowed: {name}"
        if not path_allowed(name):
            return f"file not on the allowlist: {name}"
        if status == "added" and name.startswith(".github/workflows/"):
            return f"adds a workflow file: {name}"
    return None


def decide(target: str, run: dict[str, Any], api: Api) -> Decision:
    """Decide one run in `target` (`owner/name`). Structured fields only; fails closed."""
    # 1. A pull_request run waiting for approval. GitHub reports such a run as
    #    status=completed, conclusion=action_required.
    if run.get("event") != "pull_request":
        return Decision(False, f"event is {run.get('event')!r}, not pull_request")
    if run.get("conclusion") != "action_required" or run.get("status") not in ("completed", "action_required"):
        return Decision(False, f"not waiting for approval (status={run.get('status')!r}, conclusion={run.get('conclusion')!r})")
    if _id(run, "repository", "full_name") != target:
        return Decision(False, "run does not belong to the target repository")

    # 2. Actor and triggering actor are the bot, by id.
    if _id(run, "actor", "id") != BOT_ID:
        return Decision(False, f"actor id {_id(run, 'actor', 'id')} is not the fork bot")
    if _id(run, "triggering_actor", "id") != BOT_ID:
        return Decision(False, f"triggering actor id {_id(run, 'triggering_actor', 'id')} is not the fork bot")

    # 3. Head repository: a fork owned by the bot, whose parent is the target.
    head = run.get("head_repository")
    head_id = _id(head, "id")
    if not isinstance(head_id, int):
        return Decision(False, "run has no head repository")
    if _id(head, "owner", "id") != BOT_ID:
        return Decision(False, "head repository is not owned by the fork bot")
    if head.get("fork") is not True:
        return Decision(False, "head repository is not a fork")
    head_repo = api.repo_by_id(head_id)
    if head_repo.get("id") != head_id or _id(head_repo, "owner", "id") != BOT_ID or head_repo.get("fork") is not True:
        return Decision(False, "head repository, fetched, is not the bot's fork")
    if _id(head_repo, "parent", "full_name") != target:
        return Decision(False, "head repository is not a fork of the target")

    head_sha, head_branch = run.get("head_sha"), run.get("head_branch")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        return Decision(False, "run has no head sha")
    if not isinstance(head_branch, str) or not head_branch:
        return Decision(False, "run has no head branch")

    # 4. Exactly one open PR for it.
    linked = run.get("pull_requests") or []
    if linked:
        numbers = {p.get("number") for p in linked if _id(p, "base", "repo", "id") == _id(run, "repository", "id")}
        pulls = [api.pull(target, n) for n in numbers if isinstance(n, int)]
        pulls = [p for p in pulls if p.get("state") == "open"]
    else:
        pulls = api.open_pulls_from(target, BOT_LOGIN, head_branch)
    if len(pulls) != 1:
        return Decision(False, f"{len(pulls)} open PRs match the run, need exactly 1")
    pr = pulls[0]
    number = pr.get("number")
    if not isinstance(number, int):
        return Decision(False, "matched PR has no number")
    if pr.get("state") != "open":
        return Decision(False, "PR is not open", number)
    if _id(pr, "user", "id") != BOT_ID:
        return Decision(False, f"PR author id {_id(pr, 'user', 'id')} is not the fork bot", number)
    if _id(pr, "user", "type") != BOT_TYPE:
        return Decision(False, f"PR author type {_id(pr, 'user', 'type')!r} is not {BOT_TYPE!r}", number)
    if _id(pr, "head", "sha") != head_sha:
        return Decision(False, "PR head sha differs from the run's head sha", number)
    if _id(pr, "head", "repo", "id") != head_id:
        return Decision(False, "PR head repository differs from the run's", number)
    if _id(pr, "head", "ref") != head_branch:
        return Decision(False, "PR head branch differs from the run's", number)
    if _id(pr, "base", "repo", "full_name") != target:
        return Decision(False, "PR base repository is not the target", number)
    default_branch = api.repo(target).get("default_branch")
    if not default_branch or _id(pr, "base", "ref") != default_branch:
        return Decision(False, "PR base is not the default branch", number)

    # 5. Changed files.
    why = check_files(api.pull_files(target, number), pr.get("changed_files"))
    if why:
        return Decision(False, why, number)

    return Decision(True, "all checks pass", number)


# --------------------------------------------------------------------------------------------
# GitHub, through the caller's `gh` login.


class GhError(Exception):
    pass


def gh(*args: str) -> Any:
    proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise GhError(f"gh api {args[-1] if args else ''}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def gh_get(path: str, **params: str) -> Any:
    fields = [x for k, v in params.items() for x in ("-f", f"{k}={v}")]
    return gh("-X", "GET", path, *fields)


def gh_pages(path: str, key: str | None = None, **params: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        body = gh_get(path, per_page=str(PER_PAGE), page=str(page), **params)
        items = body.get(key, []) if key else body
        if not isinstance(items, list):
            raise GhError(f"unexpected response from {path}")
        out.extend(items)
        if len(items) < PER_PAGE:
            return out
        page += 1


class GhApi:
    def repo(self, full_name: str) -> dict[str, Any]:
        return gh_get(f"repos/{full_name}")

    def repo_by_id(self, repo_id: int) -> dict[str, Any]:
        return gh_get(f"repositories/{int(repo_id)}")

    def pull(self, full_name: str, number: int) -> dict[str, Any]:
        return gh_get(f"repos/{full_name}/pulls/{int(number)}")

    def open_pulls_from(self, full_name: str, head_owner: str, head_branch: str) -> list[dict[str, Any]]:
        # The branch goes in as a query field, which gh encodes; it never builds the path.
        found = gh_pages(f"repos/{full_name}/pulls", state="open", head=f"{head_owner}:{head_branch}")
        # The list form lacks changed_files; fetch each full PR.
        return [self.pull(full_name, p["number"]) for p in found if isinstance(p.get("number"), int)]

    def pull_files(self, full_name: str, number: int) -> list[dict[str, Any]]:
        return gh_pages(f"repos/{full_name}/pulls/{int(number)}/files")

    def waiting_runs(self, full_name: str) -> list[dict[str, Any]]:
        return gh_pages(f"repos/{full_name}/actions/runs", "workflow_runs", event="pull_request", status="action_required")

    def approve(self, full_name: str, run_id: int) -> None:
        gh("-X", "POST", f"repos/{full_name}/actions/runs/{int(run_id)}/approve")


# --------------------------------------------------------------------------------------------


def printable(s: Any, width: int = 40) -> str:
    """API text for display only: printable ASCII, truncated."""
    s = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(s))
    return s if len(s) <= width else s[: width - 1] + "~"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", action="append", default=[], help="a listed repository name (repeatable); default: all listed")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="yes", action="store_false", help="print what would be approved (default)")
    mode.add_argument("--yes", dest="yes", action="store_true", help="approve the runs that pass every check")
    # Explicit: argparse would otherwise take store_false's implied default (True) for `yes`.
    ap.set_defaults(yes=False)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    global_js = Path(__file__).resolve().parent.parent / "renovate" / "global.js"
    try:
        fleet = parse_fleet(global_js.read_text())
    except (OSError, FleetParseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    targets = fleet
    if args.repo:
        names = [r.removeprefix(f"{ORG}/") for r in args.repo]
        unknown = [n for n in names if n not in fleet]
        if unknown:
            print(f"error: not listed in renovate/global.js: {', '.join(unknown)}", file=sys.stderr)
            return 2
        targets = names

    api = GhApi()
    print(f"== {len(targets)} repositories, {'APPROVING' if args.yes else 'dry run'}")
    rows: list[tuple[str, str, str, str, str]] = []
    errors = 0
    for name in targets:
        full = f"{ORG}/{name}"
        try:
            runs = api.waiting_runs(full)
        except GhError as e:
            print(f"{name}: ERROR listing runs: {e}")
            rows.append((name, "-", "-", "ERROR", "could not list runs"))
            errors += 1
            continue
        for run in runs:
            run_id = run.get("id")
            if not isinstance(run_id, int):
                continue
            try:
                d = decide(full, run, api)
            except (GhError, KeyError, TypeError, ValueError) as e:
                d = Decision(False, f"error while checking: {printable(e, 120)}")
                errors += 1
            verdict = "SKIP"
            if d.approve:
                verdict = "WOULD APPROVE"
                if args.yes:
                    try:
                        api.approve(full, run_id)
                        verdict = "APPROVED"
                    except GhError as e:
                        verdict, d.reason = "ERROR", f"approve failed: {printable(e, 120)}"
                        errors += 1
            pr = f"#{d.pr}" if d.pr else "-"
            print(f"{name} run {run_id} ({printable(run.get('name'), 30)}) {pr}: {verdict}: {d.reason}")
            rows.append((name, str(run_id), pr, verdict, d.reason))

    print()
    if not rows:
        print("No runs waiting for approval in the listed repositories.")
    else:
        head = ("repository", "run", "PR", "verdict", "reason")
        widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(4)]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "  {}"
        print(fmt.format(*head))
        for r in rows:
            print(fmt.format(*r))
    counts = {v: sum(1 for r in rows if r[3] == v) for v in ("APPROVED", "WOULD APPROVE", "SKIP", "ERROR")}
    print("summary: " + ", ".join(f"{k.lower()} {v}" for k, v in counts.items()))
    if not args.yes and counts["WOULD APPROVE"]:
        print("dry run only: nothing approved. Re-run with --yes to approve.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
