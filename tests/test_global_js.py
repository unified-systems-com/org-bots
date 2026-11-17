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
    " rules: (c.repositories.find((r) => r.customManagers) || {}).packageRules}));"
    " } catch (e) { console.log(JSON.stringify({error: e.message})); }"
)
FLEET = {"core": ["tap"], "fleet": ["dcom-tap", "tap-plugin-github-core"]}


@unittest.skipUnless(NODE, "node is not on PATH")
class GlobalJsTest(unittest.TestCase):
    def load(self, fleet: object, only: str = "all", batch: str | None = None,
             bot: dict[str, str] | None = None) -> dict[str, object]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "RENOVATE_BOT_LOGIN": "tap-renovate[bot]",
            "RENOVATE_BOT_ID": "353051615",
            "ORG_BOTS_ONLY": only,
        }
        env.update(bot or {})
        if batch is not None:
            env["ORG_BOTS_BATCH"] = batch
        if fleet is not None:
            env["ORG_BOTS_FLEET"] = fleet if isinstance(fleet, str) else json.dumps(fleet)
        proc = subprocess.run([NODE, "-e", LOAD, str(ROOT / "renovate" / "global.js")], env=env,
                              capture_output=True, text=True, check=True, timeout=60)
        return json.loads(proc.stdout)

    def repos(self, fleet: object, only: str = "all", batch: str | None = None) -> list[str]:
        out = self.load(fleet, only, batch)
        self.assertIn("ok", out, out)
        return [r[0] for r in out["ok"]]  # type: ignore[union-attr]

    def refused(self, fleet: object, only: str = "all", batch: str | None = None) -> str:
        out = self.load(fleet, only, batch)
        self.assertIn("error", out, out)
        return str(out["error"])

    def test_all_is_tap_plus_discovered(self) -> None:
        out = self.load(FLEET)
        self.assertEqual(out["ok"], [
            ["unified-systems-com/dcom-tap", None, True],
            ["unified-systems-com/tap", 10, False],
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
        self.assertEqual(out["ok"], [["unified-systems-com/ok", None, True], ["unified-systems-com/tap", 10, False]])

    # The ramp (2026-09-28: a whole-fleet burst got the bot account flagged).
    BIG = {"core": ["tap"], "fleet": [f"p{i}-tap" for i in range(1, 8)]}

    def test_batches_partition_the_fleet(self) -> None:
        seen: list[str] = []
        for i in range(1, 4):
            part = self.repos(self.BIG, "fleet", f"{i}/3")
            self.assertLessEqual(len(part), 3, part)
            seen += part
        self.assertEqual(sorted(seen), sorted(["unified-systems-com/tap"] + [f"unified-systems-com/p{i}-tap" for i in range(1, 8)]))

    def test_one_repo_run_ignores_the_batch(self) -> None:
        self.assertEqual(self.repos(self.BIG, "p5-tap", "1/8"), ["unified-systems-com/p5-tap"])

    def test_unset_or_all_batch_is_the_whole_list(self) -> None:
        self.assertEqual(len(self.repos(self.BIG, "fleet")), 8)
        self.assertEqual(len(self.repos(self.BIG, "fleet", "all")), 8)

    def test_malformed_batch_refused(self) -> None:
        for batch in ("0/3", "4/3", "1", "a/b", "1/0", "-1/3"):
            with self.subTest(batch=batch):
                self.assertIn("ORG_BOTS_BATCH", self.refused(self.BIG, "fleet", batch))


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

    def test_pin_rule_is_pep621_runtime_third_party(self) -> None:
        rule = self.rules[self.pin_rule()]
        self.assertEqual(rule["matchManagers"], ["pep621"])
        self.assertEqual(rule["matchDepTypes"], ["project.dependencies", "project.optional-dependencies"])
        # The validator refuses `*` beside other patterns; negations alone are the idiom.
        self.assertEqual(rule["matchPackageNames"], ["!*-tap"])

    def test_third_party_runtime_deps_are_pinned(self) -> None:
        for name in ("boto3", "botocore", "PyYAML", "nh3", "sigstore", "requests"):
            for dep_type in ("project.dependencies", "project.optional-dependencies"):
                with self.subTest(name=name, dep_type=dep_type):
                    self.assertEqual(self.merged(name, dep_type).get("rangeStrategy"), "pin")

    def test_our_own_packages_stay_ranges(self) -> None:
        for name in ("computing-core-tap", "Computing_Core_Tap", "compliance.core.tap", "aws-core-tap",
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


if __name__ == "__main__":
    unittest.main()
