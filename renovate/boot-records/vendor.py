#!/usr/bin/env python3
"""Check or move the vendored copy of tap's boot-record derivation (tap-vendor.json).

    renovate/boot-records/vendor.py                  # check the pin against tap, and tap's main
    renovate/boot-records/vendor.py --update <sha>   # re-vendor from tap at <sha>, rewrite the pin

Runs on the maintainer's machine with their `gh` login, and weekly in CI (.github/workflows/boot-records-check.yml) with a read-only token; tap is public, so read access is enough.
It calls `gh` with an argument list, never a shell. Standard library only.

--check exits 0 when every vendored file is byte-identical to tap at the pinned commit, 1 when
one is not, and 3 when the pin is intact but one of the files differs on tap's main: the
derivation, or its docstrings, moved on, and the pin needs a look.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "tap-vendor.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def fetch(repository: str, path: str, ref: str) -> bytes:
    out = subprocess.run(
        ["gh", "api", f"repos/{repository}/contents/{path}?ref={ref}", "-H", "Accept: application/vnd.github.raw"],
        check=True,
        capture_output=True,
    )
    return out.stdout


def resolve(repository: str, ref: str) -> str:
    out = subprocess.run(
        ["gh", "api", f"repos/{repository}/commits/{ref}", "--jq", ".sha"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(lock: dict) -> int:
    repository, commit = lock["repository"], lock["commit"]
    status = 0
    for rel, expected in sorted(lock["files"].items()):
        local = sha256((HERE / rel).read_bytes())
        upstream = sha256(fetch(repository, rel, commit))
        if local != expected or upstream != expected:
            print(f"MISMATCH {rel}: pin {expected}, vendored {local}, {repository}@{commit[:12]} {upstream}")
            status = 1
        else:
            print(f"ok       {rel} == {repository}@{commit[:12]}")
    if status:
        return status
    main_sha = resolve(repository, "main")
    moved = [rel for rel, expected in sorted(lock["files"].items()) if sha256(fetch(repository, rel, main_sha)) != expected]
    if moved:
        print(f"{repository} main ({main_sha[:12]}) differs from the pin in: {', '.join(moved)}. Read the change, then --update.")
        return 3
    print(f"{repository} main ({main_sha[:12]}) carries the same files")
    return 0


def update(lock: dict, commit: str) -> int:
    if not SHA_RE.match(commit):
        print("--update takes a full 40-character commit sha", file=sys.stderr)
        return 1
    repository = lock["repository"]
    files: dict[str, str] = {}
    for rel in sorted(lock["files"]):
        data = fetch(repository, rel, commit)
        (HERE / rel).write_bytes(data)
        files[rel] = sha256(data)
        print(f"vendored {rel} from {repository}@{commit[:12]}")
    lock["commit"] = commit
    lock["files"] = files
    LOCK.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", metavar="COMMIT", help="re-vendor from tap at this full commit sha")
    args = parser.parse_args(argv)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return update(lock, args.update) if args.update else check(lock)


if __name__ == "__main__":
    sys.exit(main())
