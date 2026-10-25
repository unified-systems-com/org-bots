"""Offline tests for scripts/next_batch.py: fake run history, no network.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import next_batch as nb  # noqa: E402


def hist(*labels: str) -> list[list[str]]:
    """Newest-first successful runs, each with one Renovate job named for its label."""
    return [[f"discover (tap-plugin topic)", f"renovate ({label})"] for label in labels]


class RotationTest(unittest.TestCase):
    def pick(self, history: list[list[str]], batch: str = "auto", only: str = "fleet") -> tuple[str, str]:
        return nb.choose(batch, only, lambda: history)

    def test_starts_at_one_with_no_history(self) -> None:
        self.assertEqual(self.pick([]), ("1/8", "auto 1/8"))

    def test_takes_the_slice_after_the_newest_successful_auto_run(self) -> None:
        self.assertEqual(self.pick(hist("auto 3/8", "auto 2/8")), ("4/8", "auto 4/8"))

    def test_wraps_after_the_last_slice(self) -> None:
        self.assertEqual(self.pick(hist("auto 8/8")), ("1/8", "auto 1/8"))

    def test_a_full_cycle_covers_every_slice_once(self) -> None:
        history: list[list[str]] = []
        seen = []
        for _ in range(nb.SIZE):
            picked, label = self.pick(history)
            seen.append(picked)
            history = hist(label) + history  # that run succeeded
        self.assertEqual(sorted(seen), sorted(f"{i}/8" for i in range(1, 9)))

    def test_a_gap_changes_nothing(self) -> None:
        # Only successful runs are in the history, so a month of failed or unapproved runs is
        # invisible: the next slice is still the one after the last that succeeded.
        self.assertEqual(self.pick(hist("auto 5/8")), ("6/8", "auto 6/8"))

    def test_manual_and_only_runs_do_not_move_the_rotation(self) -> None:
        self.assertEqual(self.pick(hist("slice 7/8", "all", "only gitlab-tap", "auto 2/8")), ("3/8", "auto 3/8"))

    def test_a_changed_slice_count_restarts(self) -> None:
        self.assertEqual(self.pick(hist("auto 3/6")), ("1/8", "auto 1/8"))

    def test_a_job_that_did_not_succeed_is_ignored(self) -> None:
        # api_history() keeps only jobs whose conclusion is success; a name alone is not enough.
        self.assertEqual(nb.last_auto_label([["renovate (auto 4/8)"]]), "auto 4/8")
        self.assertIsNone(nb.last_auto_label([[]]))


class JobPrefixTest(unittest.TestCase):
    def test_each_job_reads_only_its_own_label(self) -> None:
        both = [["renovate (auto 5/8)", "release-please (auto 2/8)"]]
        self.assertEqual(nb.last_auto_label(both), "auto 5/8")
        self.assertEqual(nb.last_auto_label(both, "release-please"), "auto 2/8")

    def test_release_please_rotation_ignores_renovate_runs(self) -> None:
        renovate_only = [["renovate (auto 6/8)"]]
        self.assertEqual(nb.choose("auto", "fleet", lambda: renovate_only, job="release-please"),
                         ("1/8", "auto 1/8"))

    def test_release_please_rotation_advances(self) -> None:
        h = [["discover (tap-plugin topic)", "release-please (auto 4/8)"]]
        self.assertEqual(nb.choose("auto", "fleet", lambda: h, job="release-please"), ("5/8", "auto 5/8"))

    def test_the_old_static_job_name_is_not_a_label(self) -> None:
        h = [["release-please (discovered repositories)"]]
        self.assertIsNone(nb.last_auto_label(h, "release-please"))

    def test_the_job_prefix_is_matched_literally(self) -> None:
        self.assertIsNone(nb.last_auto_label([["releaseXplease (auto 1/8)"]], "release.please"))

    def test_bad_workflow_or_job_is_refused(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nb.main(["--workflow", "../x.yml"]), 1)
            self.assertEqual(nb.main(["--job", "a b"]), 1)


class ExplicitTest(unittest.TestCase):
    def none(self) -> list[list[str]]:
        raise AssertionError("an explicit choice must not read the history")

    def test_all(self) -> None:
        self.assertEqual(nb.choose("all", "fleet", self.none), ("all", "all"))

    def test_named_slice(self) -> None:
        self.assertEqual(nb.choose("2/3", "all", self.none), ("2/3", "slice 2/3"))

    def test_one_repository_ignores_the_slice(self) -> None:
        self.assertEqual(nb.choose("auto", "gitlab-tap", self.none), ("all", "only gitlab-tap"))

    def test_malformed_batches_are_refused(self) -> None:
        for bad in ("0/8", "9/8", "1/0", "a/b", "1/8; rm", "1/8/9", "ALL"):
            with self.assertRaises(ValueError, msg=bad):
                nb.choose(bad, "fleet", self.none)

    def test_a_hostile_repository_name_is_refused(self) -> None:
        for bad in ("a\nlabel=auto 1/8", "a b", "a;b", "../x"):
            with self.assertRaises(ValueError, msg=bad):
                nb.choose("auto", bad, self.none)

    def test_labels_cannot_forge_an_auto_label(self) -> None:
        # An explicit slice is labelled `slice i/n`, never `auto i/n`, so it cannot move the rotation.
        _, label = nb.choose("5/8", "fleet", self.none)
        self.assertIsNone(nb.AUTO_LABEL_RE.match(label))


class OutputTest(unittest.TestCase):
    def test_writes_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            old = os.environ.get("GITHUB_OUTPUT")
            os.environ["GITHUB_OUTPUT"] = str(out)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    rc = nb.main(["--batch", "4/8", "--github-output"])
            finally:
                if old is None:
                    os.environ.pop("GITHUB_OUTPUT")
                else:
                    os.environ["GITHUB_OUTPUT"] = old
            self.assertEqual(rc, 0)
            self.assertEqual(out.read_text(), "batch=4/8\nlabel=slice 4/8\n")


if __name__ == "__main__":
    unittest.main()
