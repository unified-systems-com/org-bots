#!/usr/bin/env python3
"""Choose which slice of the fleet this Renovate or release-please run covers, from the run history.

    scripts/next_batch.py [--batch auto|all|<i>/<n>] [--only NAME] [--github-output]
                             [--workflow renovate.yml] [--job renovate]

Prints the slice as `<i>/<n>` (or `all`) and the job label the workflow puts in the Renovate
job's name. With --github-output it writes `batch=<slice>` and `label=<label>` to $GITHUB_OUTPUT.

--workflow and --job select whose history is read: each workflow's rotation has its own file and
its own job-name prefix (`release-please (auto <i>/<n>)`), so the two never interfere.

`auto` is a ROTATION THAT SURVIVES ABSENCE. The next slice is the one after the slice of the
newest successful `auto` run, read from that run's job name (`renovate (auto <i>/<n>)`), so:

  - a run that fails, is cancelled, or never gets approved moves nothing, and the same slice is
    tried again;
  - a pause of a day or of three months changes nothing: the next run takes the next slice;
  - it cannot alias the way `run number mod n` can (approving every 8th run would otherwise
    pick the same slice forever);
  - there is no state to lose: no artifact to expire, no variable to write (the workflow's
    token is read-only). The run history is the state.

With no earlier successful auto run it starts at slice 1. Runs that name their own slice
(`3/8`), `all`, or one repository (`only`) do not move the rotation: their labels do not match.
After n successful auto runs every repository has been covered once, and the cycle repeats for
as long as the schedule runs; a repository with nothing to update costs a no-op, so the
rotation is also how a long backlog gets worked through, n runs at a time.

Only the Python standard library is used. Needs a token that can read this repository's
Actions runs (`actions: read`), in $GITHUB_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from typing import Any, Callable

SIZE = 8  # slices: about three repositories each, at the fleet's present size
WORKFLOW = "renovate.yml"  # defaults: Renovate's workflow file and job-name prefix
JOB = "renovate"
FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
JOB_RE = re.compile(r"^[A-Za-z0-9._-]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
SLICE_RE = re.compile(r"^[1-9][0-9]*/[1-9][0-9]*$")
AUTO_LABEL_RE = re.compile(r"^auto ([1-9][0-9]*)/([1-9][0-9]*)$")
RUNS_LOOKED_AT = 30  # newest successful runs to search; far more than any gap of manual runs


def after(label: str | None, size: int = SIZE) -> str:
    """The slice after the one a past auto run covered (`auto i/n`), or 1/size when unknown."""
    m = AUTO_LABEL_RE.match(label or "")
    if not m or int(m.group(2)) != size or int(m.group(1)) > size:
        return f"1/{size}"
    return f"{int(m.group(1)) % size + 1}/{size}"


def last_auto_label(job_names_newest_first: list[list[str]], job: str = JOB) -> str | None:
    """The label of the newest successful run whose `<job> (...)` job was an auto run."""
    pattern = re.compile(rf"^{re.escape(job)} \((auto [^)]*)\)$")
    for names in job_names_newest_first:
        for name in names:
            m = pattern.match(name)
            if m:
                return m.group(1)
    return None


def choose(batch: str, only: str, history: Callable[[], list[list[str]]], size: int = SIZE,
           job: str = JOB) -> tuple[str, str]:
    """(slice for ORG_BOTS_BATCH, job label)."""
    only = (only or "fleet").strip()
    batch = (batch or "auto").strip()
    if only not in ("fleet", "all"):
        if not NAME_RE.match(only):  # the label goes into $GITHUB_OUTPUT: no newline, no surprises
            raise ValueError("only must be fleet, all or a plain repository name")
        return "all", f"only {only}"  # a one-repository run ignores the slice
    if batch == "auto":
        picked = after(last_auto_label(history(), job), size)
        return picked, f"auto {picked}"
    if batch == "all":
        return "all", "all"
    if SLICE_RE.match(batch):
        i, n = (int(x) for x in batch.split("/"))
        if i > n:
            raise ValueError(f"batch {batch}: the slice number is past the slice count")
        return batch, f"slice {batch}"
    raise ValueError("batch must be auto, all or <i>/<n>")


def _get(path: str) -> Any:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https URL)
        return json.load(resp)


def api_history(workflow: str = WORKFLOW) -> list[list[str]]:
    repo = os.environ["GITHUB_REPOSITORY"]
    runs = _get(f"repos/{repo}/actions/workflows/{workflow}/runs?status=success&per_page={RUNS_LOOKED_AT}")
    out: list[list[str]] = []
    for run in runs.get("workflow_runs", []):
        jobs = _get(f"repos/{repo}/actions/runs/{int(run['id'])}/jobs?per_page=100")
        out.append([j["name"] for j in jobs.get("jobs", []) if j.get("conclusion") == "success"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batch", default="auto")
    ap.add_argument("--only", default="fleet")
    ap.add_argument("--workflow", default=WORKFLOW, help="workflow file whose run history is the rotation's memory")
    ap.add_argument("--job", default=JOB, help="job-name prefix of the label, `<job> (auto i/n)`")
    ap.add_argument("--github-output", action="store_true", help="write batch= and label= to $GITHUB_OUTPUT")
    args = ap.parse_args(argv)
    try:
        if not FILE_RE.match(args.workflow) or not JOB_RE.match(args.job):
            raise ValueError("workflow must be a plain .yml/.yaml file name and job a plain name")
        batch, label = choose(args.batch, args.only, lambda: api_history(args.workflow), job=args.job)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"fleet slice: {batch} (job label: {args.job} ({label}))", file=sys.stderr)
    print(batch)
    if args.github_output:
        path = os.environ.get("GITHUB_OUTPUT")
        if not path:
            print("error: --github-output needs $GITHUB_OUTPUT", file=sys.stderr)
            return 1
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"batch={batch}\nlabel={label}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
