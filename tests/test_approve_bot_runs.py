"""Offline tests for scripts/approve_bot_runs.py: fixture JSON, no network, stdlib only.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import approve_bot_runs as abr  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "approve_bot_runs_valid.json").read_text())


class FakeApi:
    """Serves the fixture; fails the test on any request the fixture does not describe."""

    def __init__(self, fx: dict[str, Any]):
        self.fx = fx

    def repo(self, full_name: str) -> dict[str, Any]:
        assert full_name == self.fx["target"], full_name
        return self.fx["target_repo"]

    def repo_by_id(self, repo_id: int) -> dict[str, Any]:
        assert repo_id == self.fx["head_repo"]["id"], repo_id
        return self.fx["head_repo"]

    def pull(self, full_name: str, number: int) -> dict[str, Any]:
        return next(p for p in self.fx["pulls"] if p["number"] == number)

    def open_pulls_from(self, full_name: str, head_owner: str, head_branch: str) -> list[dict[str, Any]]:
        assert head_owner == abr.BOT_LOGIN
        return [p for p in self.fx["pulls"] if p["head"]["ref"] == head_branch]

    def pull_files(self, full_name: str, number: int) -> list[dict[str, Any]]:
        return self.fx["files"]


def decide(fx: dict[str, Any]) -> abr.Decision:
    return abr.decide(fx["target"], fx["run"], FakeApi(fx))


class DecideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = copy.deepcopy(FIXTURE)

    def assertSkip(self, fragment: str) -> None:
        d = decide(self.fx)
        self.assertFalse(d.approve, d.reason)
        self.assertIn(fragment, d.reason)

    def test_valid_bot_run_approves(self) -> None:
        d = decide(self.fx)
        self.assertTrue(d.approve, d.reason)
        self.assertEqual(d.pr, 190)

    def test_linked_pull_requests_path_approves(self) -> None:
        self.fx["run"]["pull_requests"] = [{"number": 190, "base": {"repo": {"id": 1000}}}]
        self.assertTrue(decide(self.fx).approve)

    def test_bot_actor_but_pr_author_is_someone_else(self) -> None:
        self.fx["pulls"][0]["user"] = {"login": "mallory", "id": 42, "type": "User"}
        self.assertSkip("PR author id 42")

    def test_pr_author_bot_login_but_type_bot(self) -> None:
        self.fx["pulls"][0]["user"]["type"] = "Bot"
        self.assertSkip("PR author type")

    def test_head_repo_not_a_fork_of_target(self) -> None:
        self.fx["head_repo"]["parent"] = {"id": 9, "full_name": "unified-systems-com/tap"}
        self.assertSkip("not a fork of the target")

    def test_head_repo_not_a_fork_at_all(self) -> None:
        self.fx["run"]["head_repository"]["fork"] = False
        self.assertSkip("not a fork")

    def test_head_sha_mismatch(self) -> None:
        self.fx["pulls"][0]["head"]["sha"] = "b" * 40
        self.assertSkip("head sha differs")

    def test_disallowed_file(self) -> None:
        self.fx["files"].append({"filename": "scripts/evil.sh", "status": "modified"})
        self.fx["pulls"][0]["changed_files"] = 4
        self.assertSkip("not on the allowlist: scripts/evil.sh")

    def test_login_match_with_wrong_id(self) -> None:
        self.fx["run"]["actor"] = {"login": abr.BOT_LOGIN, "id": 1, "type": "User"}
        self.assertSkip("actor id 1")

    def test_triggering_actor_not_bot(self) -> None:
        self.fx["run"]["triggering_actor"]["id"] = 286052
        self.assertSkip("triggering actor")

    def test_head_repo_owner_login_match_wrong_id(self) -> None:
        self.fx["run"]["head_repository"]["owner"]["id"] = 7
        self.assertSkip("not owned by the fork bot")

    def test_not_waiting_for_approval(self) -> None:
        self.fx["run"]["conclusion"] = "success"
        self.assertSkip("not waiting for approval")

    def test_event_not_pull_request(self) -> None:
        self.fx["run"]["event"] = "pull_request_target"
        self.assertSkip("not pull_request")

    def test_base_not_default_branch(self) -> None:
        self.fx["pulls"][0]["base"]["ref"] = "release"
        self.assertSkip("not the default branch")

    def test_zero_or_two_open_prs(self) -> None:
        self.fx["pulls"] = []
        self.assertSkip("0 open PRs")
        self.fx = copy.deepcopy(FIXTURE)
        self.fx["pulls"].append(copy.deepcopy(self.fx["pulls"][0]) | {"number": 191})
        self.assertSkip("2 open PRs")

    def test_removed_or_renamed_file(self) -> None:
        self.fx["files"][0]["status"] = "removed"
        self.assertSkip("status 'removed'")
        self.fx["files"][0].update(status="renamed", previous_filename="scripts/x.sh")
        self.assertSkip("status 'renamed'")

    def test_added_workflow_file(self) -> None:
        self.fx["files"][0]["status"] = "added"
        self.assertSkip("adds a workflow file")

    def test_nested_workflow_dir_not_allowed(self) -> None:
        self.fx["files"][0]["filename"] = ".github/workflows/sub/ci.yml"
        self.assertSkip("not on the allowlist")

    def test_too_many_files(self) -> None:
        self.fx["files"] = [{"filename": f"boot/{i}.boot.json", "status": "modified"} for i in range(51)]
        self.fx["pulls"][0]["changed_files"] = 51
        self.assertSkip("51 changed files")

    def test_file_list_shorter_than_changed_files(self) -> None:
        self.fx["pulls"][0]["changed_files"] = 4
        self.assertSkip("changed_files=4")


class AllowlistTest(unittest.TestCase):
    def test_paths(self) -> None:
        ok = [".github/workflows/ci.yml", ".github/workflows/a.yaml", "pyproject.toml", "uv.lock",
              "package.json", "package-lock.json", "boot/dev.boot.json", "x.boot.json",
              "tap-plugin.toml", "pkg/tap-plugin.toml", "CHANGELOG.md", ".release-please-manifest.json",
              "release-please-config.json", ".env", "Dockerfile", "renovate.json5"]
        bad = ["scripts/evil.sh", ".github/workflows/x/ci.yml", ".github/actions/a/action.yml",
               "sub/pyproject.toml", "docker/Dockerfile", ".envrc", "uv.lock.bak", "src/CHANGELOG.md"]
        for p in ok:
            self.assertTrue(abr.path_allowed(p), p)
        for p in bad:
            self.assertFalse(abr.path_allowed(p), p)


class ArgsTest(unittest.TestCase):
    def test_default_is_dry_run(self) -> None:
        self.assertFalse(abr.parse_args([]).yes)
        self.assertFalse(abr.parse_args(["--repo", "tap"]).yes)
        self.assertFalse(abr.parse_args(["--dry-run"]).yes)
        self.assertTrue(abr.parse_args(["--yes"]).yes)


class FleetParseTest(unittest.TestCase):
    def test_real_global_js(self) -> None:
        names = abr.parse_fleet((ROOT / "renovate" / "global.js").read_text())
        self.assertIn("tap", names)
        self.assertIn("tap-plugin-github-core", names)
        self.assertGreater(len(names), 10)

    def test_commented_name_not_listed(self) -> None:
        text = 'const SELF_CONFIGURED = [\n];\nconst FLEET = [\n  "a",\n  // "b",\n];\n'
        self.assertEqual(abr.parse_fleet(text), ["a"])

    def test_zero_repos_fails(self) -> None:
        text = "const SELF_CONFIGURED = [\n  { repository: `${ORG}/tap` },\n];\nconst FLEET = [\n  // none\n];\n"
        with self.assertRaisesRegex(abr.FleetParseError, "zero"):
            abr.parse_fleet(text)

    def test_missing_block_fails(self) -> None:
        with self.assertRaises(abr.FleetParseError):
            abr.parse_fleet("module.exports = {};\n")

    def test_unsafe_name_fails(self) -> None:
        text = 'const SELF_CONFIGURED = [\n];\nconst FLEET = [\n  "a/../b",\n];\n'
        with self.assertRaises(abr.FleetParseError):
            abr.parse_fleet(text)


if __name__ == "__main__":
    unittest.main()
