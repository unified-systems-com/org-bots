"""Offline tests for refresh.py and the vendored tap module: no network, stdlib only.

    python3 -m unittest discover -s renovate/boot-records -v
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import refresh  # noqa: E402

OLD = ("v0.1.0", "efa983a582eaef7d16e27daa50a68aeaef7c56de")
NEW = ("v0.2.0", "0123456789abcdef0123456789abcdef01234567")


def record(rev: str, commit: str) -> str:
    return (
        "{\n"
        '  "version": 1,\n'
        '  "install": {\n'
        '    "plugins": [\n'
        "      {\n"
        '        "slug": "git_core",\n'
        '        "enabled": true,\n'
        '        "source": {\n'
        '          "type": "git",\n'
        '          "url": "https://github.com/unified-systems-com/git-core-tap",\n'
        f'          "rev": "{rev}",\n'
        f'          "commit": "{commit}"\n'
        "        }\n"
        "      }\n"
        "    ]\n"
        "  }\n"
        "}\n"
    )


def canonical(text: str) -> str:
    data = json.loads(text)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


TOML = """[plugin]
slug = "demo"

[[boot.records]]
name = "ci"
description = "the CI record"
sha256 = "{digest}"

[other]
key = "value"
"""


class PluginRepo:
    """A throwaway plugin repository: tap_plugin/demo/{boot/ci.boot.json, tap-plugin.toml}."""

    def __init__(self, root: Path):
        self.root = root
        self.pkg = root / "tap_plugin" / "demo"
        (self.pkg / "boot").mkdir(parents=True)
        self.record = self.pkg / "boot" / "ci.boot.json"
        self.toml = self.pkg / "tap-plugin.toml"
        self.record.write_text(record(*OLD))
        self.toml.write_text(TOML.format(digest=canonical(record(*OLD))))

    def declared(self) -> str:
        line = next(ln for ln in self.toml.read_text().splitlines() if ln.startswith("sha256"))
        return line.split('"')[1]


def run(repo: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = refresh.main(["--repo", str(repo)])
    return code, out.getvalue(), err.getvalue()


class VendorPin(unittest.TestCase):
    def test_vendored_files_match_the_pin(self):
        self.assertEqual(refresh.verify_vendor(), [])

    def test_pin_names_a_full_commit_and_every_vendored_file(self):
        lock = json.loads((HERE / "tap-vendor.json").read_text())
        self.assertRegex(lock["commit"], r"^[0-9a-f]{40}$")
        on_disk = {str(p.relative_to(HERE)) for p in (HERE / "tap").rglob("*.py")}
        self.assertEqual(set(lock["files"]), on_disk)

    def test_an_edited_vendored_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "boot-records"
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
            target = copy / "tap" / "boot_records.py"
            target.write_text(target.read_text() + "\n# edited\n")
            self.assertEqual(len(refresh.verify_vendor(copy)), 1)


class Refresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = PluginRepo(Path(self.tmp.name) / "demo-tap")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_bumped_pin_gets_its_digest_refreshed(self):
        before = self.repo.declared()
        self.repo.record.write_text(record(*NEW))
        code, out, err = run(self.repo.root)
        self.assertEqual(code, 0, err)
        self.assertIn("refreshed tap_plugin/demo/tap-plugin.toml", out)
        self.assertNotEqual(self.repo.declared(), before)
        self.assertEqual(self.repo.declared(), canonical(record(*NEW)))

    def test_only_the_sha256_line_changes(self):
        old_lines = self.repo.toml.read_text().splitlines()
        self.repo.record.write_text(record(*NEW))
        run(self.repo.root)
        new_lines = self.repo.toml.read_text().splitlines()
        changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0][1].startswith('sha256 = "'))

    def test_the_record_itself_is_not_rewritten(self):
        self.repo.record.write_text(record(*NEW))
        run(self.repo.root)
        self.assertEqual(self.repo.record.read_text(), record(*NEW))

    def test_indentation_does_not_move_the_digest(self):
        reindented = json.dumps(json.loads(record(*OLD)), indent=4) + "\n"
        self.repo.record.write_text(reindented)
        code, out, _ = run(self.repo.root)
        self.assertEqual(code, 0)
        self.assertNotIn("refreshed", out)

    def test_nothing_to_do_changes_nothing(self):
        before = self.repo.toml.read_bytes()
        code, out, _ = run(self.repo.root)
        self.assertEqual(code, 0)
        self.assertNotIn("refreshed", out)
        self.assertEqual(self.repo.toml.read_bytes(), before)

    def test_a_record_with_no_manifest_entry_fails(self):
        (self.repo.pkg / "boot" / "extra.boot.json").write_text(record(*OLD))
        code, _, err = run(self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn("has no [[boot.records]] entry", err)

    def test_a_repository_without_boot_records_is_a_no_op(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        code, out, _ = run(empty)
        self.assertEqual(code, 0)
        self.assertIn("nothing to refresh", out)

    def test_the_environment_cannot_put_the_checkout_on_the_import_path(self):
        # Renovate runs the script with -I from the checkout's root. With PYTHONPATH naming
        # the checkout, a sitecustomize.py or a `tap` package there must not be imported.
        (self.repo.root / "sitecustomize.py").write_text("raise SystemExit('checkout sitecustomize imported')\n")
        shadow = self.repo.root / "tap"
        shadow.mkdir()
        (shadow / "__init__.py").write_text("raise SystemExit('checkout tap imported')\n")
        self.repo.record.write_text(record(*NEW))
        proc = subprocess.run(
            [sys.executable, "-I", str(HERE / "refresh.py")],
            cwd=self.repo.root,
            env={"PYTHONPATH": str(self.repo.root), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.repo.declared(), canonical(record(*NEW)))

if __name__ == "__main__":
    unittest.main()
