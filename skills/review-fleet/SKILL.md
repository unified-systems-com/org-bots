---
name: review-fleet
description: Review the state of the unified-systems-com plugin fleet against a core version — which nightlies are green on core main, which plugins have unreleased work or a stale release PR, which records pin what, and whether a core tag actually published images. Use before saying "the fleet is ready", before and after a core release, after a breaking core change, or when asked "what is left across the fleet". Read-only until its last section; it reports, and merges or cuts only what the maintainer has approved.
---

# Review the fleet

A fleet review answers one question with evidence a reader can check: **against this core, which
plugins work, which do not, and what is waiting on whom.** It is a sweep, then a diagnosis of each
red, then a report. It is not a fix-everything pass.

Every claim in the report names how it is known: read (a file or a log), ran (a command and its
result), or inferred. A sweep older than the last core merge is not evidence about today.

## Why this exists

During the tap 0.4.0 cut (2026-11) the fleet was called ready on a sweep two days old. Core had
merged a stricter rule in between, three plugins were red against it, and the release went out
with them red. The same week a core tag was cut and announced while its image publish run had
failed, so no image of that version existed. Both were visible in one command each. Nobody had
run them.

## 1. Sweep: one pass, every repo

The fleet is every repository in the org carrying the `tap-plugin` topic
(`scripts/discover_fleet.py` is the same list the bots use). For each, collect four facts:

```sh
O=unified-systems-com
for r in $(gh api --paginate "search/repositories?q=org:$O+topic:tap-plugin&per_page=100" \
           -q '.items[] | select(.archived|not) | .name' | sort); do
  n=$(gh run list -R $O/$r --workflow nightly.yml --branch main --limit 1 \
        --json conclusion,createdAt -q '.[0] | "\(.conclusion // "running") \(.createdAt[5:10])"')
  rel=$(gh release list -R $O/$r --limit 1 --json tagName -q '.[0].tagName')
  ahead=""; [ -n "$rel" ] && ahead=$(gh api repos/$O/$r/compare/$rel...main -q .ahead_by)
  prs=$(gh pr list -R $O/$r --state open --json number,author \
        -q '[.[] | "#\(.number)\(if .author.is_bot then "b" else "" end)"] | join(",")')
  printf "%-30s nightly:%-16s rel:%-8s +%-3s prs:%s\n" $r "${n:-none}" "${rel:-none}" "$ahead" "$prs"
done
```

Read the table for:

- **A nightly that is not `success`**, and its date. `cancelled` is a time limit, not a pass.
- **`+N` after a release**: work on main that no release carries.
- **A bot PR (`b`)**: a release PR. Check its `updatedAt`; one older than the last merge to main
  is stale and needs the bot to refresh it before anyone reads its diff.
- **No release at all** on a repo that others pin.

The nightly is the plugin against **core main**. The PR lane is the plugin against the core floor
it declares. They answer different questions; a green PR lane says nothing about core main.

## 2. For each red nightly, find out which of three things it is

Pull the failing log and group the errors before reading any code:

```sh
gh run view <id> -R $O/<repo> --log-failed | awk -F'\t' '{print $NF}' | cut -c30- > red.log
grep -E "^(FAILED|ERROR) " red.log | sed 's/ - .*//' | awk -F'::' '{print $1}' | sort | uniq -c
grep -E "^E  " red.log | sort | uniq -c | sort -rn | head
gh run list -R $O/<repo> --workflow nightly.yml --branch main --limit 6 --json conclusion
```

Then compare dates: when did it turn red, what merged to core main that day
(`git log origin/main --since=<date>`), and what merged to the plugin that day.

- **Core changed under it.** The plugin's main did not move; core's did. This is fleet fallout
  and belongs to whoever made the core change, or to the review.
- **The plugin's own new work.** The red starts the day the plugin merged something. It belongs
  to the session working there. Do not edit a repo another session is merging into; say so.
- **A limit, not a failure.** The job ran out of time. Read the log for how far it got.

A test that fails is not yet a product that fails. Say which you have shown. "Its own seed bundle
does not import" is a product failure. "Its test fixture collides with core's test fixture" is not.

Things that have been the cause before, worth a first look:

- The plugin's dimension bundle is declared under a name core does not read. Core reads the
  `[grift]` entry named exactly `dimension`.
- The plugin's tests reach into a core module behind `except ImportError`, and core removed the
  module. The guard then takes the old-core path silently and the tests assert against nothing.
- The plugin's CI record pins a dependency release from before that dependency declared
  something core now requires.

## 3. Check what a core release actually published

A git tag and a GitHub release page appear whether or not images were published. After any core
tag:

```sh
gh run list -R $O/tap --workflow publish-release-tags.yml --limit 1 --json conclusion,headBranch
gh run view <id> -R $O/tap --json jobs -q '.jobs[] | "\(.conclusion) \(.name)"'
```

The version exists for an operator only when the `retag … as v<version>` jobs succeeded. If the
CVE gate failed, read which package and which fixed version, then read the Dockerfile's notes on
that package's pin **before** proposing a bump. Some pins are held one revision back on purpose,
and the notes say why.

## 4. Check the upgrade path on real data, not only a fresh boot

CI boots an empty database. A migration that cannot run on an existing one passes CI every time.
When a core change rewrites stored data or a plugin squashes migrations:

- Restore a real instance's dump into a scratch database. Never the live one.
- The dump is real data. Use it only where the maintainer has said it may be held, on a machine
  that already holds it. Do not copy it to CI, a shared host, a pull request or a log, and do not
  paste rows from it into a report: report counts. Drop every scratch copy when the test is done,
  and say in the report which copies still exist.
- Install every plugin at its **release tag**, not at main.
- Run exactly what the standard start-up runs: one plain `manage.py migrate`. Migrating apps one
  at a time by hand proves a different thing.
- Count what should be unchanged before and after, and run `migrate --check`.

A squashed migration must ship beside the migrations it replaces until every install is past it.

## 5. Check which records still pin the old world

After plugins re-release, three kinds of record can still name the old versions: each plugin's
own `ci` record, product and demo records, and Codespace records. A fix to the `ci` record does
not touch the others. A demo record that pins a pre-change plugin release boots fine until the
step that needs the change, then aborts.

## 6. Report

One row per repo that is not simply green and released. For each: the fact, how it is known, the
date of the evidence, and who owns the next step. Separate what is red from what is merely
unreleased. Name anything you did not check.

Do not write "the fleet is green" unless the sweep in section 1 ran after the last merge to core
main.

## 7. Acting, only on what is approved

This skill ends at the report. Merging plugin PRs, cutting releases and opening core PRs follow
the repository's own rules and the maintainer's standing instructions. When they apply:

- Read the AI review on every PR before merging it, release PRs included, and answer it in
  writing. A release PR with no review posted is a question for the maintainer, not a pass.
- Refresh a stale release PR through the release bot before reading it.
- Cut with `scripts/cut-release.sh`: dry run first, read that it shows exactly one release for
  the merged PR, then `--yes`. Then check the repo's `release-sbom` run.
- Never merge past a branch rule. If a rule blocks you, report which rule.

## What this skill is not

Not a conformance audit of plugin code (`tap-conformance-audit` in tap does that), not the
release procedure (`cut-release`), and not a gate: nothing enforces any of it.
