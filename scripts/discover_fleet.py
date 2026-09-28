#!/usr/bin/env python3
"""Discover the fleet: the unified-systems-com repositories carrying the `tap-plugin` topic.

    scripts/discover_fleet.py [--github-output NAME]

Prints one JSON object, {"core": ["tap"], "fleet": [<name>, ...]}, and a summary on stderr.
With --github-output NAME it also writes `NAME=<that JSON>` to the file named by
$GITHUB_OUTPUT, for a later workflow job to read.

This is the one place that decides which repositories org-bots' automation touches. Renovate
(renovate/global.js), release-please (.github/workflows/release-please.yml) and
scripts/approve_bot_runs.py all read its answer; none of them keeps a list of its own.

A repository is in the fleet when ALL of these hold, read from `GET /orgs/unified-systems-com/repos`:
  - its owner is unified-systems-com. The endpoint only returns the org's repositories, but
    the check is made on each entry anyway: a topic is a global, public string, and the owner
    qualifier must be a checked fact, not a side effect of which URL was called;
  - it carries the `tap-plugin` topic (the repository declaring itself a TAP plugin or product);
  - it is neither archived nor disabled;
  - its name is a plain repository name.
A tagged repository whose name is not plain, or `org-bots` carrying the topic, stops discovery
with an error rather than being skipped: either one means the topic is on a repository it must
never be on, which a human needs to see. No repository at all is an error too, never "nothing
to do".

`tap` is never discovered: it is named here (CORE), whether or not it carries the topic. It is
the host, not one member of a growing fleet, and it carries its own Renovate config.

Why not Renovate's own `autodiscover` + `autodiscoverTopics`: on GitHub, Renovate autodiscovers
from `GET /user/repos` (the repositories the token's USER owns, collaborates on, or reaches as
an org member). The fork bot has no role on any org repository, so that list is the bot's own
forks, which carry no topics. Autodiscovery would find nothing, or, without the topic filter,
the forks themselves. The org's repository list is public, so this needs no credential beyond
a workflow's read-only GITHUB_TOKEN (or none at all).

Why not the search API: its index lags. Right after the topic was applied to the fleet it
returned 22 of 24 repositories; the org repository list returned all 24.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

ORG = "unified-systems-com"
TOPIC = "tap-plugin"
# Named, never discovered (req-cicd-fleet-bot-discovery-core-pinned).
CORE = ("tap",)
# Never part of the fleet, even if tagged: the repository that runs the automation.
REFUSED = ("org-bots",)

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PER_PAGE = 100
MAX_PAGES = 50
API = "https://api.github.com"


class DiscoveryError(Exception):
    pass


@dataclass(frozen=True)
class Fleet:
    core: tuple[str, ...]
    fleet: tuple[str, ...]

    def names(self) -> list[str]:
        """Every repository in scope: the discovered fleet, then core."""
        return [*self.fleet, *self.core]

    def to_json(self) -> str:
        return json.dumps({"core": list(self.core), "fleet": list(self.fleet)}, separators=(",", ":"))


def plain_name(name: Any) -> bool:
    return isinstance(name, str) and bool(NAME_RE.match(name)) and name not in (".", "..")


def _owner(repo: dict[str, Any]) -> str:
    owner = repo.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else None
    return login if isinstance(login, str) else ""


def select_fleet(repos: list[dict[str, Any]]) -> Fleet:
    """The fleet, from the entries of `GET /orgs/{ORG}/repos`. Raises DiscoveryError."""
    if not isinstance(repos, list):
        raise DiscoveryError("org repository list is not a list")
    fleet: list[str] = []
    for repo in repos:
        if not isinstance(repo, dict):
            raise DiscoveryError("org repository list has a non-object entry")
        topics = repo.get("topics")
        if not isinstance(topics, list) or TOPIC not in topics:
            continue
        # GitHub owner names are case-insensitive.
        if _owner(repo).lower() != ORG.lower():
            continue
        if repo.get("archived") is not False or repo.get("disabled") is not False:
            continue
        name = repo.get("name")
        if not plain_name(name):
            raise DiscoveryError(f"a repository carrying the {TOPIC} topic has an unusable name: {str(name)[:80]!r}")
        if str(repo.get("full_name", "")).lower() != f"{ORG}/{name}".lower():
            raise DiscoveryError(f"{name}: full_name does not match {ORG}/{name}")
        if name in REFUSED:
            raise DiscoveryError(f"{ORG}/{name} carries the {TOPIC} topic; it must never be in the fleet. Remove the topic.")
        if name in CORE or name in fleet:
            continue
        fleet.append(name)
    if not fleet:
        raise DiscoveryError(f"no {ORG} repository carries the {TOPIC} topic")
    return Fleet(core=CORE, fleet=tuple(sorted(fleet)))


def fetch_org_repos(get_page: Callable[[int], Any]) -> list[dict[str, Any]]:
    """Every page of `GET /orgs/{ORG}/repos`. get_page(n) returns page n's parsed JSON."""
    out: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        items = get_page(page)
        if not isinstance(items, list):
            raise DiscoveryError(f"unexpected response for page {page} of the org repository list")
        out.extend(items)
        if len(items) < PER_PAGE:
            return out
    raise DiscoveryError(f"the org repository list has more than {MAX_PAGES} pages")


def http_page(page: int) -> Any:
    """One page, over HTTPS. Uses GITHUB_TOKEN or GH_TOKEN when set; the list is public."""
    url = f"{API}/orgs/{ORG}/repos?type=all&sort=full_name&per_page={PER_PAGE}&page={int(page)}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https URL)
            return json.load(resp)
    except (OSError, ValueError) as e:
        raise DiscoveryError(f"listing {ORG}'s repositories: {e}") from e


def discover(get_page: Callable[[int], Any]) -> Fleet:
    return select_fleet(fetch_org_repos(get_page))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--github-output", metavar="NAME", help="also write NAME=<json> to $GITHUB_OUTPUT")
    args = ap.parse_args(argv)
    if args.github_output is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.github_output):
        print("error: --github-output takes an output name", file=sys.stderr)
        return 2
    try:
        found = discover(http_page)
    except DiscoveryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(found.fleet)} repositories carry the {TOPIC} topic, plus core: {', '.join(found.core)}", file=sys.stderr)
    for name in found.names():
        print(f"  {ORG}/{name}", file=sys.stderr)
    out = found.to_json()
    print(out)
    if args.github_output is not None:
        path = os.environ.get("GITHUB_OUTPUT")
        if not path:
            print("error: --github-output needs $GITHUB_OUTPUT", file=sys.stderr)
            return 2
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{args.github_output}={out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
