"""renovate/global.js against the discovered fleet it is handed (ORG_BOTS_FLEET), and the pep621
rules of renovate/preset.js as global.js hands them to a discovered repository.

Needs `node` on PATH (skipped without it). The pinned Renovate image has one:

    docker run --rm -v "$PWD:/w:ro" -w /w --entrypoint python3 <renovate image> \\
        -m unittest tests.test_global_js -v

(or any machine with node and python3: python3 -m unittest discover -s tests -v)
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
LOAD = (
    "try { const c = require(process.argv[1]);"
    " console.log(JSON.stringify({ok: c.repositories.map((r) => [r.repository, r.prConcurrentLimit, Boolean(r.customManagers)]),"
    " rules: (c.repositories.find((r) => r.customManagers) || {}).packageRules,"
    " managers: (c.repositories.find((r) => r.customManagers) || {}).customManagers}));"
    " } catch (e) { console.log(JSON.stringify({error: e.message})); }"
)
FLEET = {"core": ["tap"], "fleet": ["dcom-tap", "tap-plugin-github-core"]}


@unittest.skipUnless(NODE, "node is not on PATH")
class GlobalJsTest(unittest.TestCase):
    def load(self, fleet: object, only: str = "all",
             bot: dict[str, str] | None = None) -> dict[str, object]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "RENOVATE_BOT_LOGIN": "tap-renovate[bot]",
            "RENOVATE_BOT_ID": "353051615",
            "ORG_BOTS_ONLY": only,
        }
        env.update(bot or {})
        if fleet is not None:
            env["ORG_BOTS_FLEET"] = fleet if isinstance(fleet, str) else json.dumps(fleet)
        proc = subprocess.run([NODE, "-e", LOAD, str(ROOT / "renovate" / "global.js")], env=env,
                              capture_output=True, text=True, check=True, timeout=60)
        return json.loads(proc.stdout)

    def repos(self, fleet: object, only: str = "all") -> list[str]:
        out = self.load(fleet, only)
        self.assertIn("ok", out, out)
        return [r[0] for r in out["ok"]]  # type: ignore[union-attr]

    def refused(self, fleet: object, only: str = "all") -> str:
        out = self.load(fleet, only)
        self.assertIn("error", out, out)
        return str(out["error"])

    def test_all_is_tap_plus_discovered(self) -> None:
        out = self.load(FLEET)
        self.assertEqual(out["ok"], [
            ["unified-systems-com/dcom-tap", None, True],
            ["unified-systems-com/tap", None, False],
            ["unified-systems-com/tap-plugin-github-core", None, True],
        ])

    def test_bot_identity_required_and_checked(self) -> None:
        for bot in ({"RENOVATE_BOT_LOGIN": ""}, {"RENOVATE_BOT_ID": ""}, {"RENOVATE_BOT_ID": "0"},
                    {"RENOVATE_BOT_ID": "12 3"}, {"RENOVATE_BOT_LOGIN": "x y"},
                    {"RENOVATE_BOT_LOGIN": "tap-renovate[bot"}):
            with self.subTest(bot=bot):
                out = self.load(FLEET, bot=bot)
                self.assertIn("RENOVATE_BOT_LOGIN and RENOVATE_BOT_ID", str(out.get("error")))
        self.assertIn("ok", self.load(FLEET, bot={"RENOVATE_BOT_LOGIN": "tap-renovate"}))

    def test_no_fork_mode_in_the_file(self) -> None:
        text = (ROOT / "renovate" / "global.js").read_text()
        for needle in ("forkCreation", "FORK_BOT", "RENOVATE_FORK_TOKEN"):
            self.assertNotIn(needle, text)

    def test_no_list_in_the_file(self) -> None:
        text = (ROOT / "renovate" / "global.js").read_text()
        for name in ("dcom-tap", "zizmor-tap", "tap-plugin-github-core", "git-serious-tap"):
            self.assertNotIn(f'"{name}"', text)

    def test_only_narrows_to_one(self) -> None:
        self.assertEqual(self.repos(FLEET, "dcom-tap"), ["unified-systems-com/dcom-tap"])
        self.assertEqual(self.repos(FLEET, "tap"), ["unified-systems-com/tap"])

    def test_only_outside_discovery_refused(self) -> None:
        for name in ("org-bots", "git-serious.com", "someone/dcom-tap", "unified-systems-com/dcom-tap", "tap-plugin"):
            with self.subTest(name=name):
                self.assertIn("is not tap or a discovered repository", self.refused(FLEET, name))

    def test_fleet_missing_or_malformed_refused(self) -> None:
        for fleet in (None, "", "a,b", {"fleet": "a"}, {"core": ["tap"]}, {"fleet": []}):
            with self.subTest(fleet=fleet):
                self.assertIn("ORG_BOTS_FLEET", self.refused(fleet))

    def test_unusable_name_refused(self) -> None:
        for name in ("a/../b", "..", "", "a b", 7):
            with self.subTest(name=name):
                self.assertIn("not a repository name", self.refused({"fleet": ["ok", name]}))

    def test_org_bots_refused(self) -> None:
        self.assertIn("org-bots", self.refused({"fleet": ["ok", "org-bots"]}))

    def test_duplicate_refused(self) -> None:
        self.assertIn("twice", self.refused({"fleet": ["ok", "ok"]}))

    def test_named_entry_wins_over_discovery(self) -> None:
        out = self.load({"fleet": ["ok", "tap"]})
        self.assertEqual(out["ok"], [["unified-systems-com/ok", None, True], ["unified-systems-com/tap", None, False]])

    BIG = {"core": ["tap"], "fleet": [f"p{i}-tap" for i in range(1, 8)]}

    def test_fleet_run_is_the_whole_list(self) -> None:
        want = sorted(["unified-systems-com/tap"] + [f"unified-systems-com/p{i}-tap" for i in range(1, 8)])
        for only in ("fleet", "all"):
            with self.subTest(only=only):
                self.assertEqual(self.repos(self.BIG, only), want)

    def test_one_repo_run_is_that_repository(self) -> None:
        self.assertEqual(self.repos(self.BIG, "p5-tap"), ["unified-systems-com/p5-tap"])

    def test_no_pull_request_limits_are_set(self) -> None:
        # Renovate's own defaults apply: nothing here narrows them.
        text = (ROOT / "renovate" / "global.js").read_text()
        self.assertNotIn("prConcurrentLimit", text)
        self.assertNotIn("prHourlyLimit", text)


# The pep621 rules of renovate/preset.js. Plugins have no lock file, so a third-party floor
# never produces a PR (botocore 1.44.4 broke aws-core-tap's CI on 2026-11-16 that way). The
# preset pins third-party runtime dependencies exactly and groups boto3 with botocore; our own
# `*-tap` packages stay ranges.
#
# The name matching below restates Renovate's for the cases these rules use (44.x,
# lib/util/string-match.ts matchRegexOrGlobList, minimatch with nocase, on the pep621
# packageName, which lib/modules/datasource/pypi/common.ts normalizePythonDepName lowercases and
# collapses runs of `-_.` to `-`). It checks the rules' intent; it is not a second Renovate.


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def glob(name: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.startswith("!"):
        return not fnmatch.fnmatchcase(name.lower(), pattern[1:].lower())
    return fnmatch.fnmatchcase(name.lower(), pattern.lower())


def names_match(name: str, patterns: list[str]) -> bool:
    pos = [p for p in patterns if not p.startswith("!")]
    neg = [p for p in patterns if p.startswith("!")]
    if pos and not any(glob(name, p) for p in pos):
        return False
    return all(glob(name, p) for p in neg)


@unittest.skipUnless(NODE, "node is not on PATH")
class PresetPep621Test(unittest.TestCase):
    rules: list[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        # The rules as global.js hands them to a discovered repository (the preset, spread).
        out = GlobalJsTest().load({"fleet": ["dcom-tap"]})
        cls.rules = out["rules"]  # type: ignore[assignment]

    def applying(self, name: str, dep_type: str, manager: str = "pep621") -> list[int]:
        """Indexes of the rules that select this dependency, in order."""
        out = []
        for i, rule in enumerate(self.rules):
            if "matchManagers" in rule and manager not in rule["matchManagers"]:  # type: ignore[operator]
                continue
            if "matchDepTypes" in rule and dep_type not in rule["matchDepTypes"]:  # type: ignore[operator]
                continue
            if "matchPackageNames" in rule and not names_match(normalise(name), rule["matchPackageNames"]):  # type: ignore[arg-type]
                continue
            if set(rule) & {"matchFileNames", "matchDatasources", "matchDepNames"}:
                continue  # none of the pep621 rules use these; the rules that do are not ours here
            out.append(i)
        return out

    def merged(self, name: str, dep_type: str) -> dict[str, object]:
        """Later rules win, as in Renovate."""
        cfg: dict[str, object] = {}
        for i in self.applying(name, dep_type):
            cfg.update({k: v for k, v in self.rules[i].items() if not k.startswith("match")})
        return cfg

    def pin_rule(self) -> int:
        found = [i for i, r in enumerate(self.rules) if r.get("rangeStrategy") == "pin"]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_tap_locked_exception_follows_the_pin_rule(self) -> None:
        found = [i for i, r in enumerate(self.rules) if r.get("rangeStrategy") == "replace"]
        self.assertEqual(len(found), 1, found)
        self.assertGreater(found[0], self.pin_rule())
        self.assertEqual(self.rules[found[0]]["matchDepTypes"], self.rules[self.pin_rule()]["matchDepTypes"])

    def test_pin_rule_is_pep621_runtime_third_party(self) -> None:
        rule = self.rules[self.pin_rule()]
        self.assertEqual(rule["matchManagers"], ["pep621"])
        self.assertEqual(rule["matchDepTypes"], ["project.dependencies", "project.optional-dependencies"])
        # The validator refuses `*` beside other patterns; negations alone are the idiom.
        self.assertEqual(rule["matchPackageNames"], ["!*-tap", "!tap"])

    def test_third_party_runtime_deps_are_pinned(self) -> None:
        for name in ("boto3", "botocore", "sigstore", "pytenable", "zizmor"):
            for dep_type in ("project.dependencies", "project.optional-dependencies"):
                with self.subTest(name=name, dep_type=dep_type):
                    self.assertEqual(self.merged(name, dep_type).get("rangeStrategy"), "pin")

    def test_libraries_tap_locks_follow_tap(self) -> None:
        # tap#1220: a plugin pin would move tap's locked version at pre-boot's install.
        for name in ("requests", "nh3", "PyYAML", "pyyaml"):
            for dep_type in ("project.dependencies", "project.optional-dependencies"):
                with self.subTest(name=name, dep_type=dep_type):
                    self.assertEqual(self.merged(name, dep_type).get("rangeStrategy"), "replace")

    def test_our_own_packages_stay_ranges(self) -> None:
        for name in ("tap", "TAP", "computing-core-tap", "Computing_Core_Tap", "compliance.core.tap", "aws-core-tap",
                     "git-serious-double-tap-tap"):
            with self.subTest(name=name):
                self.assertNotIn("rangeStrategy", self.merged(name, "project.dependencies"))

    def test_dev_build_and_python_are_left_alone(self) -> None:
        for name, dep_type in (("pytest", "dependency-groups"), ("hatchling", "build-system.requires"),
                               ("python", "requires-python"), ("ruff", "tool.uv.dev-dependencies")):
            with self.subTest(name=name, dep_type=dep_type):
                self.assertNotIn("rangeStrategy", self.merged(name, dep_type))

    def test_aws_sdk_is_one_group_after_the_generic_one(self) -> None:
        generic = [i for i, r in enumerate(self.rules)
                   if r.get("matchManagers") == ["pep621"] and "groupName" in r and "matchPackageNames" not in r]
        aws = [i for i, r in enumerate(self.rules) if r.get("groupName") == "AWS SDK"]
        self.assertEqual(len(generic), 1, generic)
        self.assertEqual(len(aws), 1, aws)
        self.assertGreater(aws[0], generic[0])
        self.assertEqual(sorted(self.rules[aws[0]]["matchPackageNames"]), ["boto3", "botocore"])  # type: ignore[arg-type]
        for name in ("boto3", "botocore"):
            with self.subTest(name=name):
                self.assertEqual(self.merged(name, "project.dependencies").get("groupName"), "AWS SDK")
        self.assertEqual(self.merged("requests", "project.dependencies").get("groupName"),
                         self.rules[generic[0]]["groupName"])

    def test_nothing_automerges(self) -> None:
        for i in (self.pin_rule(), *[i for i, r in enumerate(self.rules) if r.get("groupName") == "AWS SDK"]):
            self.assertNotIn("automerge", self.rules[i])


# The TAP floor rules of renovate/preset.js: `requires_tap` in a plugin's manifest follows
# TAP's releases one MINOR at a time (ruling: coley Q17a/Q19, 2026-11-20).
@unittest.skipUnless(NODE, "node is not on PATH")
class PresetTapFloorTest(unittest.TestCase):
    rules: list[dict[str, object]]
    managers: list[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        out = GlobalJsTest().load({"fleet": ["dcom-tap"]})
        cls.rules = out["rules"]  # type: ignore[assignment]
        cls.managers = out["managers"]  # type: ignore[assignment]

    def manager(self) -> dict[str, object]:
        found = [m for m in self.managers if m.get("datasourceTemplate") == "github-releases"]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def floor_rules(self) -> list[tuple[int, dict[str, object]]]:
        return [(i, r) for i, r in enumerate(self.rules)
                if r.get("matchDatasources") == ["github-releases"] and r.get("matchDepNames") == ["unified-systems-com/tap"]]

    def test_only_the_plain_one_bound_floor_is_matched(self) -> None:
        (pattern,) = self.manager()["matchStrings"]  # type: ignore[misc]
        regex = re.compile(pattern.replace("(?<", "(?P<"))
        cases = {
            'plugin_version = "1.0.0"\nrequires_tap = ">=0.3.1"  # the floor\n': "0.3.1",
            'requires_tap=">= 0.4.0"\n': "0.4.0",
            'requires_tap = ">=0.3.1,<0.5"\n': None,
            'requires_tap = "==0.3.1"\n': None,
            '# requires_tap = ">=0.1.0"\n': None,
            'not_requires_tap = ">=0.1.0"\n': None,
        }
        for text, want in cases.items():
            found = regex.search(text)
            self.assertEqual(found.group("currentValue") if found else None, want, text)

    def test_it_reads_the_manifest_and_looks_up_tap_releases(self) -> None:
        manager = self.manager()
        (files,) = manager["managerFilePatterns"]  # type: ignore[misc]
        regex = re.compile(files.strip("/"))
        self.assertTrue(regex.search("tap_plugin/grype/tap-plugin.toml"))
        self.assertFalse(regex.search("tap_plugin/grype/boot/tap-plugin.toml"))
        self.assertFalse(regex.search("docs/tap-plugin.toml"))
        self.assertEqual(manager["depNameTemplate"], "unified-systems-com/tap")

    def test_patch_releases_never_move_the_floor(self) -> None:
        off = [r for _, r in self.floor_rules() if r.get("enabled") is False]
        self.assertEqual([r.get("matchUpdateTypes") for r in off], [["patch"]])

    def test_the_floor_has_its_own_group_after_the_boot_record_group(self) -> None:
        boot = next(i for i, r in enumerate(self.rules) if r.get("groupName") == "plugin pins (boot records)")
        mine = [i for i, r in self.floor_rules() if r.get("groupName") == "tap floor (requires_tap)"]
        self.assertEqual(len(mine), 1)
        self.assertGreater(mine[0], boot)  # later rules win

    def test_the_floor_never_automerges(self) -> None:
        self.assertFalse(any(r.get("automerge") for _, r in self.floor_rules()))



if __name__ == "__main__":
    unittest.main()
