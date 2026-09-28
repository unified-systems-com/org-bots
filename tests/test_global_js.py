"""renovate/global.js against the discovered fleet it is handed (ORG_BOTS_FLEET).

Needs `node` on PATH (skipped without it). The pinned Renovate image has one:

    docker run --rm -v "$PWD:/w:ro" -w /w --entrypoint python3 <renovate image> \\
        -m unittest tests.test_global_js -v

(or any machine with node and python3: python3 -m unittest discover -s tests -v)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
LOAD = (
    "try { const c = require(process.argv[1]);"
    " console.log(JSON.stringify({ok: c.repositories.map((r) => [r.repository, r.prConcurrentLimit, Boolean(r.customManagers)])}));"
    " } catch (e) { console.log(JSON.stringify({error: e.message})); }"
)
FLEET = {"core": ["tap"], "fleet": ["dcom-tap", "tap-plugin-github-core"]}


@unittest.skipUnless(NODE, "node is not on PATH")
class GlobalJsTest(unittest.TestCase):
    def load(self, fleet: object, only: str = "all") -> dict[str, object]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "FORK_BOT_LOGIN": "tap-renovate-remote",
            "FORK_BOT_ID": "334543231",
            "ORG_BOTS_ONLY": only,
        }
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
            ["unified-systems-com/tap", 10, False],
            ["unified-systems-com/dcom-tap", None, True],
            ["unified-systems-com/tap-plugin-github-core", None, True],
        ])

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
        self.assertEqual(out["ok"], [["unified-systems-com/tap", 10, False], ["unified-systems-com/ok", None, True]])


if __name__ == "__main__":
    unittest.main()
