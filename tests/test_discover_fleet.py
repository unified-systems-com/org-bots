"""Offline tests for scripts/discover_fleet.py: a fake org repository list, no network.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover_fleet as df  # noqa: E402


def repo(name: str, topics: tuple[str, ...] = ("tap-plugin",), owner: str = "unified-systems-com", **kw: Any) -> dict[str, Any]:
    return {"name": name, "full_name": f"{owner}/{name}", "owner": {"login": owner}, "topics": list(topics),
            "archived": False, "disabled": False, **kw}


class SelectTest(unittest.TestCase):
    def test_topic_selects_and_sorts(self) -> None:
        got = df.select_fleet([repo("zizmor-tap"), repo("dcom-tap"), repo("git-serious.com", topics=()), repo("other", topics=("python",))])
        self.assertEqual(got.fleet, ("dcom-tap", "zizmor-tap"))
        self.assertEqual(got.core, ("tap",))
        self.assertEqual(got.names(), ["dcom-tap", "zizmor-tap", "tap"])

    def test_topic_among_others(self) -> None:
        self.assertEqual(df.select_fleet([repo("a", topics=("python", "tap-plugin"))]).fleet, ("a",))

    def test_similar_topic_is_not_the_topic(self) -> None:
        with self.assertRaises(df.DiscoveryError):
            df.select_fleet([repo("a", topics=("tap-plugins", "tap", "TAP-PLUGIN"))])

    def test_foreign_owner_never_selected(self) -> None:
        got = df.select_fleet([repo("a"), repo("evil-tap", owner="someone-else")])
        self.assertEqual(got.fleet, ("a",))

    def test_owner_case_insensitive(self) -> None:
        self.assertEqual(df.select_fleet([repo("a", owner="Unified-Systems-Com")]).fleet, ("a",))

    def test_owner_missing_never_selected(self) -> None:
        r = repo("b")
        del r["owner"]
        self.assertEqual(df.select_fleet([repo("a"), r]).fleet, ("a",))

    def test_archived_and_disabled_dropped(self) -> None:
        got = df.select_fleet([repo("a"), repo("old", archived=True), repo("off", disabled=True)])
        self.assertEqual(got.fleet, ("a",))

    def test_missing_archived_flag_dropped(self) -> None:
        r = repo("b")
        del r["archived"]
        self.assertEqual(df.select_fleet([repo("a"), r]).fleet, ("a",))

    def test_org_bots_tagged_is_an_error(self) -> None:
        with self.assertRaisesRegex(df.DiscoveryError, "org-bots"):
            df.select_fleet([repo("a"), repo("org-bots")])

    def test_org_bots_untagged_is_fine(self) -> None:
        self.assertEqual(df.select_fleet([repo("a"), repo("org-bots", topics=())]).fleet, ("a",))

    def test_tap_is_named_not_discovered(self) -> None:
        tagged = df.select_fleet([repo("a"), repo("tap")])
        untagged = df.select_fleet([repo("a"), repo("tap", topics=())])
        self.assertEqual(tagged, untagged)
        self.assertEqual(tagged.names(), ["a", "tap"])

    def test_zero_repos_is_an_error(self) -> None:
        for repos in ([], [repo("a", topics=())], [repo("tap")], [repo("x", owner="elsewhere")]):
            with self.subTest(repos=repos), self.assertRaisesRegex(df.DiscoveryError, "no unified-systems-com repository"):
                df.select_fleet(repos)

    def test_unusable_name_is_an_error(self) -> None:
        for name in ("a/../b", "..", "a b", "", "a\nb"):
            with self.subTest(name=name), self.assertRaises(df.DiscoveryError):
                df.select_fleet([repo("ok"), repo(name)])

    def test_full_name_mismatch_is_an_error(self) -> None:
        with self.assertRaises(df.DiscoveryError):
            df.select_fleet([repo("a", full_name="unified-systems-com/b")])

    def test_malformed_input(self) -> None:
        for bad in ({"a": 1}, [1], ["a"]):
            with self.subTest(bad=bad), self.assertRaises(df.DiscoveryError):
                df.select_fleet(bad)  # type: ignore[arg-type]


class PagesTest(unittest.TestCase):
    def test_paginates_until_short_page(self) -> None:
        repos = [repo(f"r{i:03}") for i in range(df.PER_PAGE + 5)]
        asked: list[int] = []

        def get_page(n: int) -> list[dict[str, Any]]:
            asked.append(n)
            return repos[(n - 1) * df.PER_PAGE : n * df.PER_PAGE]

        self.assertEqual(len(df.discover(get_page).fleet), df.PER_PAGE + 5)
        self.assertEqual(asked, [1, 2])

    def test_non_list_page_is_an_error(self) -> None:
        with self.assertRaises(df.DiscoveryError):
            df.fetch_org_repos(lambda n: {"message": "Bad credentials"})

    def test_endless_pages_are_an_error(self) -> None:
        with self.assertRaises(df.DiscoveryError):
            df.fetch_org_repos(lambda n: [repo(f"r{n}")] * df.PER_PAGE)


class CliTest(unittest.TestCase):
    def test_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.write_text("")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                 mock.patch.object(df, "http_page", lambda n: [repo("a"), repo("b", topics=())]), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(df.main(["--github-output", "fleet"]), 0)
            self.assertEqual(json.loads(stdout.getvalue()), {"core": ["tap"], "fleet": ["a"]})
            self.assertEqual(out.read_text(), 'fleet={"core":["tap"],"fleet":["a"]}\n')

    def test_discovery_error_exits_nonzero(self) -> None:
        with mock.patch.object(df, "http_page", lambda n: []), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(df.main([]), 1)
        self.assertEqual(stdout.getvalue(), "")

    def test_bad_output_name(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(df.main(["--github-output", "a=b"]), 2)


if __name__ == "__main__":
    unittest.main()
