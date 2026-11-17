"""The pep621 rules of renovate/preset.js, as a discovered repository receives them through global.js.

Plugins have no lock file, so a third-party floor never produces a PR (botocore 1.44.4 broke
aws-core-tap's CI on 2026-11-16 that way). The preset pins third-party runtime dependencies
exactly and groups boto3 with botocore; our own `*-tap` packages stay ranges.

Needs `node` on PATH (skipped without it), like tests/test_global_js.py.

The name matching below restates Renovate's for the cases these rules use (44.x,
lib/util/string-match.ts matchRegexOrGlobList, minimatch with nocase, on the pep621 packageName,
which lib/modules/datasource/pypi/common.ts normalizePythonDepName lowercases and collapses
runs of `-_.` to `-`). It is a check of the rules' intent, not a second Renovate.
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
    "const c = require(process.argv[1]);"
    " const r = c.repositories.find((x) => x.repository === 'unified-systems-com/dcom-tap');"
    " console.log(JSON.stringify(r.packageRules));"
)


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
        env = {
            "PATH": os.environ.get("PATH", ""),
            "RENOVATE_BOT_LOGIN": "tap-renovate[bot]",
            "RENOVATE_BOT_ID": "353051615",
            "ORG_BOTS_ONLY": "all",
            "ORG_BOTS_FLEET": json.dumps({"fleet": ["dcom-tap"]}),
        }
        proc = subprocess.run([NODE, "-e", LOAD, str(ROOT / "renovate" / "global.js")], env=env,
                              capture_output=True, text=True, check=True, timeout=60)
        cls.rules = json.loads(proc.stdout)

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
