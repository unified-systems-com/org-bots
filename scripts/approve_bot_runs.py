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
     workflow file or composite action is added, and it changes at most MAX_FILES files;
  6. every changed LINE has a shape the bots produce, read from each file's diff (the files
     API `patch`). A file whose diff GitHub omits (large or binary) is refused. Each change
     must replace one line with one line, and per path:
       .github/workflows/*.yml|yaml  a `uses:` line whose ref moves to a 40-hex sha, all else
       .github/actions/*/action.yml  on the line identical but a version comment (`# v7`,
                                     `# main`) that may be added or rewritten;
       **/*.boot.json                the value of a `"rev"` (tag name) or `"commit"` (40-hex);
       **/tap-plugin.toml            the value of `sha256` (64 hex), only when the same PR also
                                     changes a `*.boot.json` inside the same
                                     `tap_plugin/<slug>/` package (the record's digest moves
                                     with the record); or `plugin_version` in a
                                     release-please PR;
       Dockerfile,                   the tag and digest of a `FROM` or `COPY --from=` image,
       docker/postgres/Dockerfile    same image and stage, new digest a sha256;
       pyproject.toml                a dependency string's version specifier (same name,
                                     extras and marker), or the project `version` in a
                                     release-please PR;
       package.json                  a dependency's version, in a dependency section the diff
                                     itself shows (so never `scripts`);
       .env                          `TAP_VERSION=`, in a release-please PR;
       .release-please-manifest.json a value moving to a version;
       CHANGELOG.md                  lines only added, in one hunk at the top, in a
                                     release-please PR;
       uv.lock                       in a release-please PR only, and only one line: the
                                     `version` of the `[[package]]` whose `name` is the
                                     project's own (read from pyproject.toml's diff context) and
                                     whose `source = { virtual = "." }`, moved to the manifest
                                     version. The diff's own context must show the block's
                                     `[[package]]`, `name` and `source` lines, or it is refused.
     A release-please PR is recognised from the diff alone: it changes the release manifest
     and only by version moves; the other release files must move to a manifest version.
     Any other allowed path (package-lock.json, renovate.json5, release-please-config.json),
     and any other uv.lock change, is "needs a human look": a lock file cannot be read line by
     line, and the bots have never changed the other two;
  7. every commit sha the PR pins in one of our own repositories is on that repository's
     default branch: a changed `uses: unified-systems-com/<repo>/...@<sha>` line, and a changed
     boot-record `"commit"` (or 40-hex `"rev"`) whose entry's `"url"`, read from the diff's
     context, is `https://github.com/unified-systems-com/<repo>`. GitHub serves a commit that
     exists only in a fork as if it were in the parent (an "impostor commit"), so a sha alone
     says nothing about whose code it is. `compare/<sha>...<default branch>` must say `ahead`
     or `identical`; anything else, a 404, any API error, or a boot `commit` whose `url` the
     diff does not show, is refused. Commits pinned to a release tag are held to the same rule:
     every release tag in the fleet was on its default branch when this was written
     (2026-09-27). Shas in third-party repositories (any other owner) are out of scope here;
  8. the target repository is in the fleet, as scripts/discover_fleet.py finds it when this
     script starts: tap, plus every unified-systems-com repository carrying the `tap-plugin`
     topic (owner-checked, archived and disabled ones dropped). org-bots is never in it; if it
     carries the topic, discovery fails and nothing is approved.

Widening ALLOWED_PATHS or CONTENT_RULES, or any other rule here, is the maintainer's decision.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover_fleet  # noqa: E402  (this directory; stdlib only)

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
    ".github/actions/*/action.yml",  # composite actions: `uses:` pins, like workflows
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
    "docker/postgres/Dockerfile",
    "renovate.json5",
)
ALLOWED_FILE_STATUSES = ("modified", "changed", "added")
MAX_FILES = 50

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PER_PAGE = 100


# --------------------------------------------------------------------------------------------
# The fleet: scripts/discover_fleet.py, the same discovery Renovate and release-please use.


def fleet_names(get_page: Any) -> list[str]:
    """Every repository in scope (discovered, then tap). Raises discover_fleet.DiscoveryError."""
    return discover_fleet.discover(get_page).names()


def org_repos_page(page: int) -> Any:
    """One page of `GET /orgs/{ORG}/repos`, through the caller's `gh` login."""
    try:
        return gh_get(f"orgs/{ORG}/repos", type="all", sort="full_name", per_page=str(discover_fleet.PER_PAGE), page=str(int(page)))
    except GhError as e:
        raise discover_fleet.DiscoveryError(str(e)) from e


# --------------------------------------------------------------------------------------------
# The decision.


class Api(Protocol):
    """What the decision needs from GitHub. `GhApi` below; a fixture-backed fake in the tests."""

    def repo(self, full_name: str) -> dict[str, Any]: ...
    def repo_by_id(self, repo_id: int) -> dict[str, Any]: ...
    def pull(self, full_name: str, number: int) -> dict[str, Any]: ...
    def open_pulls_from(self, full_name: str, head_owner: str, head_branch: str) -> list[dict[str, Any]]: ...
    def pull_files(self, full_name: str, number: int) -> list[dict[str, Any]]: ...
    def compare_status(self, full_name: str, base: str, head: str) -> str: ...


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
        if status == "added" and name.startswith(".github/actions/"):
            return f"adds a composite action: {name}"
    return check_content(files)


# --------------------------------------------------------------------------------------------
# What the changed lines say. Every rule below was read off the diffs of merged Renovate and
# release-please PRs in the fleet; a line shape not seen there is refused. The diff is the
# files API `patch` field, which GitHub omits for a large or binary diff: then the file is
# refused, never guessed at.

SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
GIT_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")

RELEASE_MANIFEST = ".release-please-manifest.json"

_HUNK_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class Change:
    """One changed line: a removed line and the added line that replaces it, in hunk order."""

    old: str
    new: str
    before: list[str]  # the hunk's old-side lines above this change, nearest last


def parse_patch(patch: str) -> list[tuple[int, list[tuple[str, str]]]] | None:
    """Split a unified diff into (old start line, [(tag, text)]) hunks; None if malformed."""
    hunks: list[tuple[int, list[tuple[str, str]]]] = []
    for line in patch.split("\n"):
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                return None
            hunks.append((int(m.group(1)), []))
        elif not hunks:
            return None
        elif line.startswith("\\"):
            continue  # "\ No newline at end of file"
        elif line[:1] in (" ", "-", "+"):
            hunks[-1][1].append((line[0], line[1:]))
        elif line == "":
            hunks[-1][1].append((" ", ""))
        else:
            return None
    return hunks or None


def changes(patch: str) -> list[Change] | str:
    """Pair each removed line with its replacement. Every run of changed lines must be N
    removals followed by N additions; anything else (a line only added, or only removed) is
    not a value move, and the reason is returned instead."""
    hunks = parse_patch(patch)
    if hunks is None:
        return "unreadable diff"
    out: list[Change] = []
    for _, lines in hunks:
        old_side: list[str] = []
        i = 0
        while i < len(lines):
            if lines[i][0] == " ":
                old_side.append(lines[i][1])
                i += 1
                continue
            removed, added = [], []
            while i < len(lines) and lines[i][0] == "-":
                removed.append(lines[i][1])
                i += 1
            while i < len(lines) and lines[i][0] == "+":
                added.append(lines[i][1])
                i += 1
            if i < len(lines) and lines[i][0] == "-":
                return "removed and added lines interleave"
            if len(removed) != len(added):
                return f"{len(removed)} lines removed but {len(added)} added in one place"
            for old, new in zip(removed, added):
                out.append(Change(old, new, list(old_side)))
                old_side.append(old)
    if not out:
        return "no changed lines"
    return out


@dataclass
class Context:
    """Facts about the whole PR that a single file's rule needs."""

    release_versions: frozenset[str]  # new versions in the release-please manifest; empty if not a release PR
    boot_packages: frozenset[str] = frozenset()  # `.../tap_plugin/<slug>` dirs holding a changed *.boot.json
    project_name: str | None = None  # [project] name, from pyproject.toml's diff context in a release PR


# A `uses:` line: everything up to the `@` must stay byte-identical (indent, list dash, quote,
# owner/repo/path), so only the ref and a version comment after it can move.
_USES_RE = re.compile(
    r"""^(?P<pre>\s*(?:-\s+)?uses:\s*["']?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s"']+)?)"""
    r"""@(?P<ref>[^\s"'#]+)(?P<quote>["']?)(?P<rest>.*)$"""
)
# The comment Renovate writes after a pin: `# v7`, `# v4.2.0`, `# 0.9.1`, `# main`. It may be
# added (pinning `@v7` gives `@<sha> # v7`), rewritten (`# v2` to `# v3`) or kept; whatever
# follows it must not change.
_VERSION_COMMENT_RE = re.compile(r"^\s+#\s*(?:v?\d[0-9A-Za-z.+-]*|main)(?=\s|$)")


def _drop_version_comment(rest: str) -> str:
    return _VERSION_COMMENT_RE.sub("", rest, count=1)


def rule_workflow(path: str, c: Change, ctx: Context) -> str | None:
    old, new = _USES_RE.match(c.old), _USES_RE.match(c.new)
    if not old or not new:
        return "a changed line is not a `uses:` line"
    if old["pre"] != new["pre"] or old["quote"] != new["quote"]:
        return "a `uses:` line changes more than its ref"
    if not SHA40.fullmatch(new["ref"]):
        return "a `uses:` ref is not moved to a 40-hex commit sha"
    if old["rest"] != new["rest"] and _drop_version_comment(old["rest"]) != _drop_version_comment(new["rest"]):
        return "a `uses:` line's trailing text changes beyond a version comment"
    return None


_JSON_STR_RE = re.compile(r'^(?P<indent>\s*)"(?P<key>[^"\\]+)": "(?P<value>[^"\\]*)"(?P<comma>,?)$')


def _json_value_move(c: Change) -> tuple[str, str, str] | str:
    """(key, old value, new value) for a one-line `"key": "value"` change, else a reason."""
    old, new = _JSON_STR_RE.match(c.old), _JSON_STR_RE.match(c.new)
    if not old or not new:
        return "a changed line is not a `\"key\": \"value\"` line"
    if (old["indent"], old["key"], old["comma"]) != (new["indent"], new["key"], new["comma"]):
        return "a changed line changes more than its value"
    return old["key"], old["value"], new["value"]


def rule_boot_record(path: str, c: Change, ctx: Context) -> str | None:
    move = _json_value_move(c)
    if isinstance(move, str):
        return move
    key, _, value = move
    if key == "commit":
        return None if SHA40.fullmatch(value) else "a boot-record `commit` is not a 40-hex sha"
    if key == "rev":
        return None if GIT_TAG.fullmatch(value) else "a boot-record `rev` is not a tag name"
    return f"a boot-record `{printable(key, 30)}` changes (only `rev` and `commit` may)"


def _release_version(value: str, ctx: Context, what: str) -> str | None:
    if not ctx.release_versions:
        return f"{what} changes outside a release-please PR"
    if value not in ctx.release_versions:
        return f"{what} does not match the release manifest"
    return None


_TOML_STR_RE = re.compile(r'^(?P<indent>\s*)(?P<key>[A-Za-z0-9_-]+) = "(?P<value>[^"\\]*)"(?P<rest>.*)$')


def rule_plugin_manifest(path: str, c: Change, ctx: Context) -> str | None:
    old, new = _TOML_STR_RE.match(c.old), _TOML_STR_RE.match(c.new)
    if not old or not new or (old["indent"], old["key"], old["rest"]) != (new["indent"], new["key"], new["rest"]):
        return "a changed line is not a value-only `key = \"value\"` move"
    if new["key"] == "sha256" and not new["indent"]:
        if not SHA256.fullmatch(new["value"]):
            return "a `sha256` is not 64 hex"
        # The digest is of an in-package boot record; it only moves when that record does.
        package = _plugin_package(path)
        if package is None or package not in ctx.boot_packages:
            return "a `sha256` changes with no boot record changed in the same tap_plugin/<slug>/ package"
        return None
    if new["key"] == "plugin_version" and not new["indent"]:
        return _release_version(new["value"], ctx, "`plugin_version`")
    return f"`{printable(new['key'], 30)}` changes (only `sha256` and `plugin_version` may)"


def _plugin_package(path: str) -> str | None:
    """The `.../tap_plugin/<slug>` directory a file sits in (at any depth), or None."""
    parts = path.split("/")
    for i in range(len(parts) - 2, 0, -1):
        if parts[i - 1] == "tap_plugin":
            return "/".join(parts[: i + 1])
    return None


# Root Dockerfile (and tap's docker/postgres/Dockerfile). Image names are lower-case per the OCI distribution spec; a registry port
# or a `--platform` flag was never seen in a bot PR, so it is refused.
_IMAGE = r"(?P<image>[a-z0-9][a-z0-9._/-]*)(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]*))?(?:@sha256:(?P<digest>[^\s]*))?"
_FROM_RE = re.compile(r"^FROM " + _IMAGE + r"(?P<rest>(?: AS [A-Za-z0-9_.-]+)?)$")
_COPY_FROM_RE = re.compile(r"^COPY --from=" + _IMAGE + r"(?P<rest> .+)$")


def rule_dockerfile(path: str, c: Change, ctx: Context) -> str | None:
    for form in (_FROM_RE, _COPY_FROM_RE):
        old, new = form.match(c.old), form.match(c.new)
        if old and new:
            break
    else:
        return "a changed line is not a `FROM` or `COPY --from=` image line"
    if (old["image"], old["rest"]) != (new["image"], new["rest"]):
        return "a Dockerfile line changes its image, stage name or arguments"
    if not new["digest"] or not SHA256.fullmatch(new["digest"]):
        return "a Dockerfile image is not pinned by a sha256 digest"
    return None


_ENV_RE = re.compile(r"^TAP_VERSION=(?P<value>[^\s#]+)(?P<rest>.*)$")


def rule_env(path: str, c: Change, ctx: Context) -> str | None:
    old, new = _ENV_RE.match(c.old), _ENV_RE.match(c.new)
    if not old or not new or old["rest"] != new["rest"]:
        return "a changed `.env` line is not a value-only `TAP_VERSION=` move"
    return _release_version(new["value"], ctx, "`TAP_VERSION`")


# pyproject.toml: a PEP 508 dependency string, one per line, where only the version
# specifier moves; or the project's own `version = "..."` in a release-please PR.
_PEP508_RE = re.compile(
    r'^(?P<indent>\s*)"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]*\])?)'
    r'(?P<spec>[^";]*)(?P<marker>;[^"]*)?"(?P<rest>,?(?:\s+#.*)?)$'
)
_PEP440_CLAUSE = r"\s*(?:===|==|!=|<=|>=|~=|<|>)\s*[0-9][0-9A-Za-z.*+!-]*\s*"
_PEP440_SPEC_RE = re.compile(_PEP440_CLAUSE + r"(?:," + _PEP440_CLAUSE + r")*")


def rule_pyproject(path: str, c: Change, ctx: Context) -> str | None:
    old, new = _TOML_STR_RE.match(c.old), _TOML_STR_RE.match(c.new)
    if old and new and new["key"] == "version" and not new["indent"]:
        if (old["key"], old["indent"], old["rest"]) != (new["key"], new["indent"], new["rest"]):
            return "the project `version` line changes more than its value"
        return _release_version(new["value"], ctx, "the project `version`")
    old, new = _PEP508_RE.match(c.old), _PEP508_RE.match(c.new)
    if not old or not new:
        return "a changed pyproject line is not a dependency string or the project `version`"
    if (old["indent"], old["name"], old["marker"], old["rest"]) != (new["indent"], new["name"], new["marker"], new["rest"]):
        return "a dependency line changes more than its version specifier"
    if not _PEP440_SPEC_RE.fullmatch(new["spec"]):
        return "a dependency's new specifier is not a version specifier"
    return None


# package.json: only a dependency's version, and only inside one of these sections. The
# section is read from the diff's own context: the nearest line above the change that is
# indented less than it must open one of these objects. If the hunk does not show it, the
# file is refused. So `"scripts"` (or anything else) can never change.
_NPM_DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_NPM_VERSION_RE = re.compile(r"[~^]?" + SEMVER.pattern)
_JSON_OPENER_RE = re.compile(r'^(?P<indent>\s*)"(?P<key>[^"\\]+)": \{$')


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def rule_package_json(path: str, c: Change, ctx: Context) -> str | None:
    move = _json_value_move(c)
    if isinstance(move, str):
        return move
    _, _, value = move
    if not _NPM_VERSION_RE.fullmatch(value):
        return "a package.json value is not moved to a version"
    depth = _indent(c.old)
    for above in reversed(c.before):
        if above.strip() and _indent(above) < depth:
            m = _JSON_OPENER_RE.match(above)
            if m and m["key"] in _NPM_DEP_SECTIONS and _indent(above) == 2:
                return None
            return "a package.json change is outside the dependency sections"
    return "a package.json change's section is not visible in the diff"


def rule_release_manifest(path: str, c: Change, ctx: Context) -> str | None:
    move = _json_value_move(c)
    if isinstance(move, str):
        return move
    return None if SEMVER.fullmatch(move[2]) else "a release manifest value is not a version"


# uv.lock records the project's own version, in the `[[package]]` block whose source is the
# project itself, so release-please moves that one line. That is the only uv.lock change read
# here; every other one is "needs a human look". The block's lines come from the diff's own
# context (unchanged lines, so the base branch's), never from anything the PR adds.
_UV_VERSION_RE = re.compile(r'^version = "(?P<value>[^"\\]*)"$')
_UV_NAME_RE = re.compile(r'^name = "(?P<value>[^"\\]*)"$')
_UV_VIRTUAL_SOURCE = 'source = { virtual = "." }'


def _normalized(name: str) -> str:
    """PEP 503 name normalisation, which uv.lock uses for package names."""
    return re.sub(r"[-_.]+", "-", name).lower()


def check_uv_lock(patch: str, ctx: Context) -> str | None:
    """Only the project's own `version` line, moved to the release manifest's version."""
    if not ctx.release_versions:
        return "needs a human look: uv.lock (outside a release-please PR)"
    hunks = parse_patch(patch)
    if hunks is None or len(hunks) != 1:
        return "needs a human look: uv.lock (not one version line)"
    lines = hunks[0][1]
    changed = [i for i, (tag, _) in enumerate(lines) if tag != " "]
    if len(changed) != 2 or lines[changed[0]][0] != "-" or lines[changed[1]][0] != "+" or changed[1] != changed[0] + 1:
        return "needs a human look: uv.lock (not one version line)"
    old, new = (_UV_VERSION_RE.match(lines[i][1]) for i in changed)
    if not old or not new:
        return "needs a human look: uv.lock (the changed line is not a `version`)"
    # The block, from its `[[package]]` header down to the change, must name the project...
    above = [text for _, text in lines[: changed[0]]]
    header = next((i for i in range(len(above) - 1, -1, -1) if above[i].startswith("[")), None)
    if header is None or above[header] != "[[package]]":
        return "needs a human look: uv.lock (the `[[package]]` block is not visible in the diff)"
    names = [m["value"] for m in map(_UV_NAME_RE.match, above[header + 1 :]) if m]
    if ctx.project_name is None:
        return "needs a human look: uv.lock (the project name is not visible in pyproject.toml's diff)"
    if names != [_normalized(ctx.project_name)] and names != [ctx.project_name]:
        return "needs a human look: uv.lock (the version is not the project's own package)"
    # ...and, below it and before the block ends, be the project itself.
    below = []
    for _, text in lines[changed[1] + 1 :]:
        if not text.strip() or text.startswith("["):
            break
        below.append(text)
    if _UV_VIRTUAL_SOURCE not in below:
        return "needs a human look: uv.lock (the package's `source = { virtual = \".\" }` is not visible in the diff)"
    return _release_version(new["value"], ctx, "uv.lock's project `version`")


# Content rules by path: (glob, rule). A path on ALLOWED_PATHS with no rule here
# (package-lock.json, renovate.json5, release-please-config.json) is never approved by script;
# CHANGELOG.md and uv.lock are read whole-diff, below.
CONTENT_RULES = (
    (".github/workflows/*.yml", rule_workflow),
    (".github/workflows/*.yaml", rule_workflow),
    (".github/actions/*/action.yml", rule_workflow),
    ("**/*.boot.json", rule_boot_record),
    ("**/tap-plugin.toml", rule_plugin_manifest),
    ("Dockerfile", rule_dockerfile),
    ("docker/postgres/Dockerfile", rule_dockerfile),
    (".env", rule_env),
    ("pyproject.toml", rule_pyproject),
    ("package.json", rule_package_json),
    (RELEASE_MANIFEST, rule_release_manifest),
)
_CONTENT_RES = tuple((_glob_re(p), rule) for p, rule in CONTENT_RULES)


def _rule_for(path: str) -> Any:
    return next((rule for r, rule in _CONTENT_RES if r.match(path)), None)


def check_changelog(patch: str, ctx: Context) -> str | None:
    """release-please prepends one section: a single hunk at the top, lines only added."""
    if not ctx.release_versions:
        return "CHANGELOG.md changes outside a release-please PR"
    hunks = parse_patch(patch)
    if hunks is None or len(hunks) != 1:
        return "CHANGELOG.md is not changed in exactly one place"
    start, lines = hunks[0]
    if start > 1:
        return "CHANGELOG.md is changed below its top"
    if any(tag == "-" for tag, _ in lines):
        return "CHANGELOG.md has removed lines"
    return None


def _release_context(files: list[dict[str, Any]]) -> Context | str:
    """A release-please PR is one whose file set includes the release manifest and whose
    manifest diff is a pure version move; its new versions are what the other release files
    may move to. Decided from the diff alone, never from the PR's title or branch."""
    boot_packages = frozenset(
        pkg for f in files
        if isinstance(f.get("filename"), str) and f["filename"].endswith(".boot.json")
        and (pkg := _plugin_package(f["filename"])) is not None
    )
    manifest = next((f for f in files if f.get("filename") == RELEASE_MANIFEST), None)
    if manifest is None:
        return Context(frozenset(), boot_packages)
    patch = manifest.get("patch")
    if not isinstance(patch, str):
        return f"no diff from GitHub for {RELEASE_MANIFEST} (large or binary), needs a human look"
    moves = changes(patch)
    if isinstance(moves, str):
        return f"{RELEASE_MANIFEST}: {moves}"
    versions = set()
    for c in moves:
        why = rule_release_manifest(RELEASE_MANIFEST, c, Context(frozenset()))
        if why:
            return f"{RELEASE_MANIFEST}: {why}"
        versions.add(_JSON_STR_RE.match(c.new)["value"])  # type: ignore[index]
    return Context(frozenset(versions), boot_packages, _project_name(files))


_TOML_HEADER_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")


def _project_name(files: list[dict[str, Any]]) -> str | None:
    """The `[project]` name, read from pyproject.toml's diff context above its `version`
    move; None when the diff does not show both the `[project]` header and the name."""
    f = next((f for f in files if f.get("filename") == "pyproject.toml"), None)
    patch = f.get("patch") if f else None
    moves = changes(patch) if isinstance(patch, str) else "no diff"
    if isinstance(moves, str):
        return None
    for c in moves:
        m = _TOML_STR_RE.match(c.new)
        if not m or m["key"] != "version" or m["indent"]:
            continue
        name = None
        for above in reversed(c.before):
            h = _TOML_HEADER_RE.match(above)
            if h:
                return name if h["name"] == "project" else None
            n = _TOML_STR_RE.match(above)
            if n and n["key"] == "name" and not n["indent"] and name is None:
                name = n["value"]
        return None
    return None


def check_content(files: list[dict[str, Any]]) -> str | None:
    """Return why a changed line disqualifies the PR, or None if every line is a bot's."""
    ctx = _release_context(files)
    if isinstance(ctx, str):
        return ctx
    for f in files:
        name, patch = f["filename"], f.get("patch")
        rule = _rule_for(name)
        if rule is None and name not in ("CHANGELOG.md", "uv.lock"):
            return f"needs a human look: {name}"
        if not isinstance(patch, str) or not patch:
            return f"no diff from GitHub for {name} (large or binary), needs a human look"
        if name == "CHANGELOG.md":
            why = check_changelog(patch, ctx)
        elif name == "uv.lock":
            why = check_uv_lock(patch, ctx)
            if why:
                return why
        else:
            moves = changes(patch)
            why = moves if isinstance(moves, str) else next(
                (w for w in (rule(name, c, ctx) for c in moves) if w), None
            )
        if why:
            return f"{name}: {why}"
    return None


# --------------------------------------------------------------------------------------------
# Impostor commits. GitHub stores a repository and its forks in one object network and
# resolves any sha in that network through the parent's name, so `unified-systems-com/tap@<sha>`
# runs a commit that may exist only in somebody's fork. The fork bot's PR is itself from a
# fork. So every sha the PR pins in one of our repositories must be on that repository's
# default branch. zizmor's `impostor-commit` audit (after Chainguard's clank) accepts a sha on
# ANY branch or tag, using the same compare API; this is stricter, default branch only, because
# every release tag in the fleet was on its default branch (checked with this compare call for
# all 94 tags of the 25 repositories then in the fleet on 2026-09-27; the one exception, tap's
# `park/steampipe-tooling`, is not a release tag). Shas of third-party actions and records
# (any other owner) are not checked here.

_GITHUB_URL_RE = re.compile(r"^https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _ours(owner: str) -> bool:
    return owner.lower() == ORG.lower()  # GitHub owner names are case-insensitive


def _boot_url(c: Change) -> str | None:
    """The `"url"` of the boot-record entry a changed line sits in, from the lines above it
    in the same JSON object as shown by the diff; None if the diff does not show it."""
    depth = _indent(c.old)
    for above in reversed(c.before):
        if not above.strip():
            continue
        if _indent(above) < depth:
            return None  # reached the object's opening line
        m = _JSON_STR_RE.match(above)
        if m and m["key"] == "url" and _indent(above) == depth:
            return m["value"]
    return None


def org_pins(files: list[dict[str, Any]]) -> list[tuple[str, str, str]] | str:
    """(repository name, sha, file) for every sha the PR's changed lines pin in one of our
    own repositories. Run after check_content, so every changed line has a known shape."""
    pins: list[tuple[str, str, str]] = []
    for f in files:
        name, patch = f["filename"], f.get("patch")
        rule = _rule_for(name)
        if rule not in (rule_workflow, rule_boot_record) or not isinstance(patch, str):
            continue
        moves = changes(patch)
        if isinstance(moves, str):
            return f"{name}: {moves}"
        for c in moves:
            if rule is rule_workflow:
                m = _USES_RE.match(c.new)
                target = m["pre"].split("uses:", 1)[1].strip().lstrip("\"'") if m else ""
                owner, _, rest = target.partition("/")
                if m and _ours(owner):
                    pins.append((rest.split("/", 1)[0], m["ref"], name))
                continue
            move = _json_value_move(c)
            if isinstance(move, str) or move[0] not in ("commit", "rev") or not SHA40.fullmatch(move[2]):
                continue
            url = _boot_url(c)
            if url is None:
                return f"{name}: a boot-record `{move[0]}` moves but the diff does not show its entry's `url`"
            u = _GITHUB_URL_RE.match(url)
            if u is None:
                if url.lower().startswith(f"https://github.com/{ORG.lower()}/"):
                    return f"{name}: a boot-record `url` is not a plain repository URL"
                continue  # not one of ours: out of scope
            if _ours(u["owner"]):
                pins.append((u["repo"], move[2], name))
    return pins


def check_pins(files: list[dict[str, Any]], api: Api) -> str | None:
    """Return why a pinned sha is not on its repository's default branch, or None."""
    pins = org_pins(files)
    if isinstance(pins, str):
        return pins
    defaults: dict[str, str] = {}
    for repo, sha, name in dict.fromkeys(pins):
        where = f"{name}: {ORG}/{printable(repo, 40)}@{sha[:12]}"
        if not NAME_RE.match(repo) or not SHA40.fullmatch(sha):
            return f"{where}: not a repository name and commit sha"
        full = f"{ORG}/{repo}"
        try:
            if repo not in defaults:
                defaults[repo] = api.repo(full).get("default_branch") or ""
            branch = defaults[repo]
            if not _BRANCH_RE.match(branch) or ".." in branch:
                return f"{where}: the repository's default branch is unreadable"
            status = api.compare_status(full, sha, branch)
        except GhError as e:
            return f"{where}: could not verify the commit is on the default branch ({printable(e, 60)})"
        if status not in ("ahead", "identical"):
            return f"{where}: commit is not on {printable(branch, 30)} (compare says {printable(status, 20)}), possible impostor commit"
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

    # 5, 6. Changed files and lines.
    files = api.pull_files(target, number)
    why = check_files(files, pr.get("changed_files"))
    if why:
        return Decision(False, why, number)

    # 7. Every sha pinned in one of our repositories is on its default branch.
    why = check_pins(files, api)
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
    def __init__(self) -> None:
        self._repos: dict[str, dict[str, Any]] = {}
        self._compares: dict[tuple[str, str, str], str] = {}

    def repo(self, full_name: str) -> dict[str, Any]:
        if full_name not in self._repos:
            self._repos[full_name] = gh_get(f"repos/{full_name}")
        return self._repos[full_name]

    def compare_status(self, full_name: str, base: str, head: str) -> str:
        """`status` of `compare/<base>...<head>`: `ahead` or `identical` when base is an
        ancestor of head. A 404 (no common history) raises GhError."""
        key = (full_name, base, head)
        if key not in self._compares:
            body = gh("-X", "GET", f"repos/{full_name}/compare/{base}...{head}", "-f", "per_page=1", "--jq", "{status: .status}")
            status = body.get("status") if isinstance(body, dict) else None
            if not isinstance(status, str):
                raise GhError(f"compare {base[:12]}...{head}: no status")
            self._compares[key] = status
        return self._compares[key]

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
    ap.add_argument("--repo", action="append", default=[], help="a fleet repository name (repeatable); default: the whole fleet")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="yes", action="store_false", help="print what would be approved (default)")
    mode.add_argument("--yes", dest="yes", action="store_true", help="approve the runs that pass every check")
    # Explicit: argparse would otherwise take store_false's implied default (True) for `yes`.
    ap.set_defaults(yes=False)
    return ap.parse_args(argv)


def select_targets(fleet: list[str], requested: list[str]) -> list[str] | str:
    """The repositories to check: the whole fleet, or the requested ones if every one is in it.
    A name outside the fleet is an error message, never a way to reach another repository."""
    if not requested:
        return fleet
    names = [r.removeprefix(f"{ORG}/") for r in requested]
    unknown = [n for n in names if n not in fleet]
    if unknown:
        return f"not in the fleet (tap, or a {ORG} repository carrying the {discover_fleet.TOPIC} topic): {', '.join(printable(n) for n in unknown)}"
    return list(dict.fromkeys(names))


def main(argv: list[str] | None = None, get_page: Any = None, api: Any = None) -> int:
    args = parse_args(argv)

    try:
        fleet = fleet_names(get_page or org_repos_page)
    except discover_fleet.DiscoveryError as e:
        print(f"error: fleet discovery: {e}", file=sys.stderr)
        return 2
    targets = select_targets(fleet, args.repo)
    if isinstance(targets, str):
        print(f"error: {targets}", file=sys.stderr)
        return 2

    api = api or GhApi()
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
        print("No runs waiting for approval in the fleet.")
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
