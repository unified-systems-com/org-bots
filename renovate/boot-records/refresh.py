"""Refresh a plugin repository's boot-record digests after Renovate bumps a plugin pin.

Renovate runs this as a postUpgradeTask (renovate/preset.js), inside its container, with the
working directory at the checkout of the repository it is updating:

    python3 -I /github-action/boot-records/refresh.py

An in-package boot record (`tap_plugin/<slug>/boot/<name>.boot.json`) is integrity-guarded by a
sha256 declared in the package's `tap_plugin/<slug>/tap-plugin.toml` (`[[boot.records]]`). A
Renovate edit to the record's `rev` and `commit` moves that digest, and the plugin's own checks
fail until the toml says so. This script rewrites the declared digests and then checks them.

The derivation is tap's own `tap.boot_records`, vendored beside this file at the commit named in
`tap-vendor.json`, never reimplemented here. This script verifies each vendored file against its
pinned sha256 before importing it.

`tap.boot_records.discover()` looks for packages at `plugins/*/tap_plugin/*/boot` under a root.
A plugin repository has its package at its own root (`tap_plugin/<slug>/`), so the script builds
a temporary root holding one symlink, `plugins/<repository dir> -> <repository>`, and passes that
root to the module's `refresh()` and `check()`. Writes go through the symlink to the checkout.

Exit status: 0 when every declared digest matches its record afterwards, 1 otherwise (Renovate
then reports an artifact error on the PR instead of committing a digest that does not match),
2 when the vendored module does not match its pin.

Standard library only. Run with `-I` so neither the environment nor the checkout's own files can
put a different `tap` package on the import path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "tap-vendor.json"


def verify_vendor(here: Path = HERE) -> list[str]:
    """Return the vendored files whose sha256 does not match tap-vendor.json (empty: all match)."""
    lock = json.loads((here / "tap-vendor.json").read_text(encoding="utf-8"))
    bad: list[str] = []
    for rel, expected in sorted(lock["files"].items()):
        path = here / rel
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        if actual != expected:
            bad.append(f"{rel}: expected sha256 {expected}, found {actual}")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh in-package boot-record digests (tap.boot_records).")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="plugin repository checkout (default: the working directory, which is where Renovate runs it)",
    )
    args = parser.parse_args(argv)
    repo: Path = args.repo.resolve()

    bad = verify_vendor()
    if bad:
        for line in bad:
            print(f"vendored tap module does not match tap-vendor.json: {line}", file=sys.stderr)
        return 2

    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from tap import boot_records

    if not any(p.is_dir() for p in repo.glob("tap_plugin/*/boot")):
        print(f"no tap_plugin/<slug>/boot directory in {repo}: nothing to refresh")
        return 0

    with tempfile.TemporaryDirectory(prefix="boot-records-root-") as tmp:
        root = Path(tmp)
        (root / "plugins").mkdir()
        (root / "plugins" / repo.name).symlink_to(repo, target_is_directory=True)

        for path in boot_records.refresh(root):
            print(f"refreshed {path.relative_to(root / 'plugins' / repo.name)}")
        problems = boot_records.check(root)

    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} boot-record integrity problem(s) remain after the refresh", file=sys.stderr)
        return 1
    print("boot records: all digests match, manifests coherent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
