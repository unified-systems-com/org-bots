# org-bots

This repository runs the unified-systems-com organisation's two bots from one small place, and
since fork mode it holds **no credential that can write to an org repository**:

| Bot | Runs as | Where | Does |
| --- | --- | --- | --- |
| Renovate | the fork bot (a machine user) | `.github/workflows/renovate.yml` | Opens dependency-update PRs from the bot's forks |
| release-please, stage 1 | the fork bot | `.github/workflows/release-please.yml` | Opens and refreshes each repository's release PR from the bot's fork |
| release-please, stage 2 | the maintainer, with their own `gh` login | `scripts/cut-release.sh` | Tags the merged release PR and publishes the GitHub Release |

The repository is **public** (since 2026-09-28). Nothing here is secret, and the controls are
designed to hold with the design known, not to depend on it being hidden. Merges to `main` need
one approving review from a code owner other than the author, including of the latest push (the
org ruleset `org-bots-two-person`, no bypass), and every run of the `bots` environment, which
holds the bot's token, needs criticalsec's approval with admin bypass off. No workflow here runs
on `pull_request`, so a fork's PR can trigger only GitHub's read-only CodeQL scan.

## What is in it

| Path | What it is |
| --- | --- |
| `.github/workflows/renovate.yml` | Renovate in fork mode. Pilot: manual dispatch only, one repository per run (`only`), or `fleet` (the default: every discovered repository, not tap) or `all`; the daily schedule returns when the pilot ends |
| `renovate/global.js` | Renovate's global config: tap (named) plus the discovered repositories, the bot identity (from environment variables), PR limits, fork mode, no onboarding, no dashboard |
| `renovate/preset.js` | The shared repository config every discovered repository gets, including tap's boot-record pin manager |
| `renovate/boot-records/` | The digest refresh Renovate runs after bumping an in-package boot record: `refresh.py`, tap's `tap.boot_records` vendored at a pinned commit (`tap/`, `tap-vendor.json`), `vendor.py` to check or move the pin, and its offline tests |
| `.github/workflows/release-please.yml` | release-please stage 1 (`release-pr --fork`) over the discovered repositories plus tap. Pilot: manual dispatch only |
| `scripts/discover_fleet.py` | Which repositories both bots and `approve_bot_runs.py` act on: see [The fleet](#the-fleet) |
| `scripts/cut-release.sh` | release-please stage 2 (`github-release`), run by the maintainer. Dry run by default |
| `scripts/approve_bot_runs.py` | Approves the fork bot's PR workflow runs that wait for "Approve and run", by fixed rules. Dry run by default |
| `tests/` | Offline tests for `approve_bot_runs.py`, `discover_fleet.py` and `renovate/global.js` (fixture JSON, no network; the `global.js` tests need `node` and are skipped without it): `python3 -m unittest discover -s tests` |
| `renovate/boot-records/test_refresh.py` | Offline tests for the digest refresh and the vendored pin: `python3 -m unittest discover -s renovate/boot-records` (Python 3.11 or later, for `tomllib`) |
| `release-please/package.json`, `package-lock.json` | Pin the release-please CLI and its whole dependency closure, for CI and for the script |
| `.github/workflows/fleet-conformance.yml` | Nightly repository-scope conformance sweep (`validate_plugin --repo`) over every plugin repository in the org, discovered by name with tap's `tap.plugin_identity`. Read-only `GITHUB_TOKEN`; no App key, no `bots` environment; reports in the job summary and goes red on a failed check |
| `.github/CODEOWNERS` | Every path needs the owner's review |

## The fleet

Both bots, and `scripts/approve_bot_runs.py`, act on the same set of repositories, and no file
lists them. `scripts/discover_fleet.py` finds them each time:

- every repository in `GET /orgs/unified-systems-com/repos` whose owner is `unified-systems-com`
  (checked per entry: a topic is a global string, so the owner is checked, not assumed from the
  URL), that carries the `tap-plugin` topic, and is neither archived nor disabled;
- plus `tap`, which is named rather than discovered (it is the host, and has its own Renovate
  config), whether or not it carries the topic.

It refuses to answer (and the run stops) when no repository carries the topic, when a tagged
repository's name is not a plain name, or when `org-bots` itself carries the topic. The org's
repository list is public, so the workflows' `discover` job holds only its read-only
`GITHUB_TOKEN`, and runs before, and apart from, the job that holds the bot's token.

```sh
scripts/discover_fleet.py        # prints {"core": ["tap"], "fleet": [...]}; the list goes to stderr
```

Renovate's own `autodiscover`/`autodiscoverTopics` cannot do this here: on GitHub, Renovate
autodiscovers from `GET /user/repos`, which lists the repositories the token's user owns or holds
a role on. The fork bot holds no role on any org repository, so that list is its own forks, which
carry no topics. The search API is not used either: its index lagged the topic by minutes when it
was first applied.

## How fork mode works

The fork bot is a GitHub **user** account that is not an org member and has no role on any org
repository. For each repository it works on, it forks the repository into its own account,
pushes its branch there, and opens the PR from the fork, exactly as an outside contributor would.
The target repository then treats that PR like any fork PR: its own checks run, a human reviews,
a human merges.

- **Renovate** does this natively: the workflow passes the bot's token as both the platform
  token and `RENOVATE_FORK_TOKEN`.
- **release-please** does it with `release-pr --fork`.
- Both use the same fork. An account can hold only one fork of a repository. Renovate names its
  fork `unified-systems-com-_-<repo>`, while release-please's fork code only works when the fork
  is named `<repo>`. So the release-please job renames the bot's fork to `<repo>` when needed.
  Renovate finds its fork by owner, not by name, so the rename does not disturb it.

### What a maintainer has to do by hand

- **"Approve and run" on fork PRs.** GitHub does not run a fork PR's workflows from a first-time
  contributor until someone with write access clicks *Approve and run workflows*. Depending on the
  repository's Actions setting, that can apply to every PR from an outside account, not only the
  first. Until checks run, the PR is not mergeable. Use `scripts/approve_bot_runs.py`, not the
  button (see "Approving the bot's runs").
- **Label each new release PR `autorelease: pending`.** Applying a label needs triage access
  ([GitHub docs, "Managing labels"](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels):
  "Anyone with triage access to a repository can apply and dismiss labels"), so the bot opens
  release PRs with `--skip-labeling`. release-please finds its own open release PR, and later the
  merged one, by that label alone (release-please 17.11.2, `build/src/manifest.js`,
  `findOpenReleasePullRequests` and `findMergedReleasePullRequests`). Without the label, a later
  run would push new commits to the branch but leave the old title and body, and the body is what
  stage 2 reads the version from. So the release-please job **refuses to refresh** an unlabelled
  release PR from the bot's fork and names it in the run's errors. `cut-release.sh` adds the label
  to an unlabelled merged release PR itself before tagging.

## Threat model

**What an attacker wants here:** a credential that can push to, or tag in, org repositories.
There is none here any more.

**Who holds write:**

| Credential | Held by | Can write to |
| --- | --- | --- |
| `FORK_BOT_TOKEN` | the `bots` environment of this repository | the fork bot's own forks only. On org repositories it can do what any GitHub user can: fork, and open PRs and issues |
| The maintainer's `gh` login | the maintainer's machine | whatever the maintainer can; used by `cut-release.sh` to label, tag and publish |
| `TAP_RENOVATE_PRIVATE_KEY`, `TAP_RELEASE_PLEASE_PRIVATE_KEY` | **deleted from the `bots` environment on 2026-09-28**; the Apps and tap's own copies remain until tap migrates | every org repository the two Apps are installed on. See "Retiring the App keys" |

**Where the bot token is.** `FORK_BOT_TOKEN` is a secret of the `bots` **environment**, never a
repository-level secret. The environment's deployment branch policy allows `main` only, so the
token reaches only a workflow already merged to `main`, and getting a workflow onto `main` takes a
PR the code owner reviewed. The bot's login and numeric id are the environment **variables**
`FORK_BOT_LOGIN` and `FORK_BOT_ID`: not sensitive, and read by `global.js` and the release-please
job so the account plugs in without a code change.

**What the token is.** A user token is not scoped per repository the way an App installation
token is. What bounds it is the account: it has no role on any org repository, so its write
reaches only its forks. The earlier fork-mode trial used a classic token with `public_repo` +
`workflow` and an expiry. `workflow` is needed because the bot's fork branches carry the
upstream's `.github/workflows` files. A fine-grained token is limited to repositories its owner
(or the owner's orgs) own, so, per GitHub's documentation, it cannot open the PR upstream; that
limit is not tested here.

**What can run with the token.** For Renovate, the Renovate container, pinned by image digest,
and inside it one post-upgrade command, `renovate/boot-records/refresh.py` (see "Boot-record
digests").
For release-please, the CLI, installed with `npm ci --ignore-scripts` from a lockfile that pins
every package by integrity hash, plus the runner's `gh` for the pre-checks. Every action is pinned
by full commit SHA, and every workflow starts from `permissions: {}` with a read-only
`GITHUB_TOKEN` (`contents: read`, to check this repository out).

**What a stolen bot token gets.** PRs, issues and comments on public org repositories as the bot,
and writes to the bot's own forks. Every such PR still needs "approve and run" to get checks, a
review, and a human merge. Revoke the token on the bot account and replace the secret.

**What the bots cannot do.** Neither bot merges anything or creates a tag. Renovate runs with
automerge off. A release happens only when a maintainer merges the release PR in the target
repository, through its own rulesets and checks, and then runs `cut-release.sh` with their own
credential. The org `tag-protection` ruleset prevents a tag's deletion or update.

## Who may change it

- **Code:** anything merged to `main` needs a PR with the code owner's review (`.github/CODEOWNERS`,
  enforced by the org `org-require-pr` ruleset).
- **The bot token, variables and the `bots` environment:** repository admins only, which in
  practice means the org owner. Keep the admin list to the minimum.
- **The fork bot account:** whoever holds its login. It must stay outside the org with no role on
  any org repository; giving it one gives this repository's token that write.

## Cutting a release

1. Merge the repository's release PR (it must carry `autorelease: pending`; the script adds it if
   it does not).
2. Dry run, and read what it would tag:

   ```sh
   scripts/cut-release.sh github-core-tap
   ```

3. Cut it:

   ```sh
   scripts/cut-release.sh github-core-tap --yes
   ```

   It labels the merged release PR if needed, dry-runs again and refuses unless the tag matches
   the repository's `.release-please-manifest.json`, runs `release-please github-release` with
   your `gh auth token`, then confirms the tag, checks the PR was relabelled
   `autorelease: tagged`, and lists the target's `release-sbom.yml` runs, which the tag push
   starts.

The CLI runs in a disposable `node:22-slim` container (pinned by digest) from this repository's
lockfile. Your token reaches the container only as an environment variable. The script never
writes it to disk and never prints it.

## Running a bot by hand

`scripts/run-as-maintainer.sh <repo> renovate|release [--yes]` runs the same pinned Renovate or
release-please (stage 1) against ONE repository with your own `gh` login, from your machine. It
is how tap is served since its App lanes were retired (George, 2026-09-29), and the stopgap for
any fleet repository while the fork bot is unavailable. Dry run by default.

- **renovate** needs the repository's own Renovate config (tap has `renovate.json5`), runs
  without fork mode, and commits as you with the subject suffixed `[via maintainer-run]`.
- **release** opens or refreshes the release PR (`release-please release-pr`, not `--fork`),
  labelled; cut the release afterwards with `scripts/cut-release.sh`, as always.
- **Sign-off:** the tools' commits carry no `Signed-off-by`, and tap's DCO check wants one on
  every commit. With `--yes`, each branch the tool wrote whose one commit is yours and unsigned is
  amended with `git commit -s` and a `No-issue:` trailer and force-pushed with a lease: your
  tooling applying your trailer at your command. The certification is still your review and
  merge.
- **Pacing, enforced by the script:** one repository per run; a `--yes` run refuses to start
  within 30 minutes of the previous one on the same machine (so a loop over the fleet cannot
  burst), starts after a random 0-3 minute delay, and waits a random 20-60 s between sign-off
  pushes. See "The ramp".

## Approving the bot's runs

```sh
scripts/approve_bot_runs.py                 # dry run over the whole fleet (see The fleet)
scripts/approve_bot_runs.py --repo zizmor-tap
scripts/approve_bot_runs.py --yes           # approve what passed, with your gh login
```

Approving a run executes the PR's code, and its workflow files, in the target repository's
Actions. So the decision is made only from structured API fields, never from PR titles, bodies,
comments, commit messages or branch names, which the PR's author writes. A run is approved only
when all of these hold, and otherwise it is left waiting with a one-line reason:

1. it is a `pull_request` run waiting for approval (`conclusion` `action_required`);
2. its actor and triggering actor are the fork bot **by numeric id** (the script's `BOT_ID`);
3. its head repository is a fork owned by the bot (by id) whose parent is the target;
4. exactly one open PR matches it, authored by the bot (by id, type `User`), from that fork and
   branch, at the run's head sha, into the default branch;
5. every changed file is on the script's `ALLOWED_PATHS`, none is removed, renamed or copied, no
   workflow file or composite action (`.github/actions/*/action.yml`) is added, and there are at
   most 50;
6. every changed **line** has a shape the bots produce, read from the file's diff. A path on the
   allowlist is not enough: without this, an added `run:` step or a `postinstall` script would
   pass. Each change replaces one line with one line, and:

   | Path | What may change |
   | --- | --- |
   | `.github/workflows/*.yml` / `*.yaml`, `.github/actions/*/action.yml` | a `uses:` ref, to a 40-hex commit sha; the rest of the line identical except a version comment (`# v7`, `# main`) that may be added or rewritten |
   | `**/*.boot.json` | the value of `"rev"` (a tag name) or `"commit"` (40 hex) |
   | `**/tap-plugin.toml` | the value of `sha256` (64 hex), only when the same PR also changes a `*.boot.json` in the same `tap_plugin/<slug>/` package; `plugin_version` in a release PR |
   | `Dockerfile` (root), `docker/postgres/Dockerfile` | the tag and digest of a `FROM` or `COPY --from=` image: same image, same stage, pinned by sha256 |
   | `pyproject.toml` | a dependency's version specifier (same name, extras, marker); the project `version` in a release PR |
   | `package.json` | a dependency's version, inside a dependency section the diff itself shows (never `scripts`) |
   | `.env` | `TAP_VERSION=` in a release PR |
   | `.release-please-manifest.json` | a value, to a version |
   | `CHANGELOG.md` | lines only added, in one place at the top, in a release PR |
   | `uv.lock` | in a release PR, one line only: the `version` of the `[[package]]` whose `name` is the project's own and whose `source = { virtual = "." }`, moved to the manifest's version |

   A **release PR** is recognised from the diff, never its title or branch: it changes
   `.release-please-manifest.json` by version moves only, and the other release files must move
   to a version the manifest names. For `uv.lock`, the `[[package]]` header, the `name` and the
   `source` must all be visible in the diff's own context lines, and the project name is read
   from `pyproject.toml`'s context under `[project]`; if any of it is not shown, it waits for a
   human. Any other `uv.lock` change, and `package-lock.json`, `renovate.json5` and
   `release-please-config.json`, are left for a human ("needs a human look"), as is any file
   whose diff GitHub does not return (large or binary);
7. every commit sha the PR pins **in one of our own repositories** is on that repository's
   default branch: a changed `uses: unified-systems-com/<repo>/...@<sha>` line (workflows and
   composite actions), and a changed boot-record `"commit"` (or a 40-hex `"rev"`) whose entry's
   `"url"`, read from the diff's context, is `https://github.com/unified-systems-com/<repo>`.
   This is the *impostor commit* check: GitHub resolves a sha that exists only in a fork through
   the parent's name, so `unified-systems-com/tap@<sha>` can run code that was never on tap.
   `GET repos/unified-systems-com/<repo>/compare/<sha>...<default branch>` must say `ahead` or
   `identical`; `behind`, `diverged`, a 404, any API error, or a boot `commit` whose `url` the
   diff does not show, leaves the run waiting. zizmor's `impostor-commit` audit accepts a sha on
   any branch or tag; this is stricter (default branch only) because every release tag in the
   fleet is on its default branch: all 94 tags of the 25 repositories then in the fleet were checked with
   the same compare call on 2026-09-27, and the only one off `main` was tap's
   `park/steampipe-tooling`, not a release. A boot record pinned to a tag's commit is therefore
   held to the same rule. Shas of third-party actions and records (any other owner) are not
   checked here;
8. the repository is in the fleet, as `scripts/discover_fleet.py` finds it when the script
   starts: tap, plus the `unified-systems-com` repositories carrying the `tap-plugin` topic. A
   `--repo` outside it is refused, and a discovery failure (no repository, `org-bots` tagged, an
   API error) stops the script before it checks anything.

The line shapes are those of the fleet's merged Renovate and release-please PRs; a shape not seen
there is refused. Widening `ALLOWED_PATHS`, `CONTENT_RULES` or any other rule is the code owner's
decision. The script uses only the
Python standard library and your `gh` login; it calls `gh` with an argument list, never a shell.

## Boot-record digests

A plugin's in-package boot record, `tap_plugin/<slug>/boot/<name>.boot.json`, is guarded by a
sha256 in the package's `tap_plugin/<slug>/tap-plugin.toml` (`[[boot.records]]`), and the plugin's
checks (`validate_plugin`, plugin-ci conformance) fail when the two disagree. Renovate's boot-record
manager bumps a pin's `rev` and `commit` together, which moves that digest. So the preset attaches
a `postUpgradeTasks` step to boot-record updates: after the bump, Renovate runs

```sh
python3 -I /github-action/boot-records/refresh.py
```

in its checkout of the repository, and commits the rewritten `tap-plugin.toml` with the record
(`fileFilters: tap_plugin/*/tap-plugin.toml`; nothing else the command touches is committed).

- **Same derivation as tap.** `refresh.py` does not compute digests itself. It imports
  `tap.boot_records`, copied byte for byte from tap at the commit named in
  `renovate/boot-records/tap-vendor.json`, and calls its `refresh()` and `check()` on a temporary
  root holding `plugins/<repo> -> <checkout>`, which is the layout that module discovers. It
  checks each vendored file against its sha256 in `tap-vendor.json` before importing it, and
  exits non-zero if any digest still disagrees afterwards, so Renovate reports an artifact error
  on the PR instead of committing a digest that is wrong.
- **No network, no install.** The module is standard library only, and the pinned Renovate image
  ships Python 3.12. The directory is mounted read-only into the container beside `preset.js`
  (`docker-volumes` in `renovate.yml`).
- **The allowlist.** `allowedCommands` in `renovate/global.js` is a global-only option; it allows
  exactly `^python3 -I /github-action/boot-records/refresh\.py$` and nothing else, so no repository's
  own config can run another command. Commands run without a shell
  (`allowShellExecutorForPostUpgradeCommands: false`), and Renovate passes a child command only a
  short list of basic environment variables, not its token. `-I` keeps `PYTHONPATH`, user site
  packages and the checkout itself off the import path.
- **Keeping the pin current.** `renovate/boot-records/vendor.py` (your `gh` login, read-only)
  compares the vendored files with tap at the pinned commit, and says when tap's `main` has moved
  them (exit 3). To move the pin, read the change in tap, then
  `renovate/boot-records/vendor.py --update <full commit sha>`; it rewrites the files and their
  sha256s in `tap-vendor.json` from tap itself, never by hand.
- **The PR this produces** changes a `"rev"`/`"commit"` pair per bumped dependency and one
  `sha256` line per record, which are shapes `approve_bot_runs.py` already accepts.

**Release PRs.** A plugin's CI record names the plugin itself (the "self entry") at its latest
release tag, and release-please moves that `rev` to the new tag through an `extra-files` entry in
the repository's `release-please-config.json`:

```json
{"type": "json", "path": "tap_plugin/<slug>/boot/ci.boot.json",
 "jsonpath": "$.install.plugins[?(@.slug=='<slug>')].source.rev"}
```

release-please keeps the tag's `v` (`"v0.2.1"` becomes `"v0.2.2"`, one line). The self entry carries
no `commit`: a record cannot name its own release commit (tap#865; tap#869 is the follow-up that
binds it at boot). The move changes the record's digest, so after `release-pr` the release-please
job clones the release branch from the bot's fork, runs the same `refresh.py` on it, and pushes one
more commit, as the bot and without `Signed-off-by`, when a digest changed. release-please rewrites
the branch only when the release notes change, and the job refreshes it again straight after.

tap's own deployment profiles (`boot/*.boot.json` at tap's root) are not in-package records and
carry no digest, and tap has its own renovate config, so none of this applies to tap.

## Adding a repository

Give the repository the `tap-plugin` topic (`gh repo edit unified-systems-com/<name>
--add-topic tap-plugin`; `new-plugin` applies it to a repository it creates). Nothing in this
repository changes. Removing the topic, or archiving the repository, takes it out.

- **Renovate:** it gets `renovate/preset.js`. A repository with a renovate config of its own
  would get both, so name it in `SELF_CONFIGURED` in `renovate/global.js` the way `tap` is named;
  a named entry wins over discovery. (No discovered repository has its own config today.)
- **release-please:** a discovered repository without `release-please-config.json` and
  `.release-please-manifest.json` at its root is skipped with a notice; add them, the way
  github-core-tap has them, plus the self-entry `extra-files` entry in "Release PRs"
  above when the plugin has a CI record, and the next run picks it up. A repository that still runs its
  own `release-please.yml` is skipped with a warning until that workflow is retired.

## The ramp

On 2026-09-28 the fork bot's first fleet-wide runs opened about 50 PRs and renamed 22 forks in
twenty minutes, and GitHub's anti-abuse flagged the two-day-old account: it and everything it
created went invisible to everyone else. So both jobs pace themselves:

- **Renovate** covers one slice of the fleet per run (`batch`, default `auto`: slice
  `(run number mod 8)+1` of 8, about three repositories). Eight consecutive runs cover the fleet
  once, on any schedule.
  `batch=all` removes the slicing; don't, on a young account.
- **release-please** opens at most `MAX_NEW_PRS` (3) new release PRs per run, sleeping a random
  `PAUSE_MIN`..`PAUSE_MAX` (120-300) seconds after each. Repositories past the cap are deferred to the next run with a
  notice. Refreshing an existing release PR is not capped.

- **Renovate** also starts each run after a random 0-10 minute delay, so scheduled runs never
  land on a fixed rhythm.

**The same rule applies to people.** Any fleet-wide change made by hand, from any account
(notgeorge included), goes one repository at a time with random gaps: open PRs 3-7 minutes
apart, merge them 1-3 minutes apart, pilot on one repository and stop the batch if it is not
green, and stop the batch on any failure to open a PR. The first hand-run fleet pin bump
(2026-09-28, 24 repositories) was done exactly this way and drew no attention.

After any bot run, check that one of its PRs resolves for someone other than the bot. A green
run means only that the API calls succeeded.

## Running a job by hand

```sh
gh workflow run renovate.yml -R unified-systems-com/org-bots            # the default, fleet: every discovered repo, not tap; add -f logLevel=debug to troubleshoot
gh workflow run renovate.yml -R unified-systems-com/org-bots -f only=gryphon-playground-tap   # another single fleet repo (must be discovered, or tap)
gh workflow run renovate.yml -R unified-systems-com/org-bots -f only=all  # tap and every discovered repository (an empty value falls back to the default)
gh workflow run release-please.yml -R unified-systems-com/org-bots
gh workflow run fleet-conformance.yml -R unified-systems-com/org-bots   # the conformance sweep, off-schedule
gh run list -R unified-systems-com/org-bots --limit 5
```

## Before the first run

- Create the fork bot account (outside the org), and a token on it.
- In this repository's `bots` environment: the secret `FORK_BOT_TOKEN`, and the variables
  `FORK_BOT_LOGIN` and `FORK_BOT_ID` (`gh api users/<login> --jq .id`). A run with either
  variable missing stops before touching any repository.

## Retiring the App keys

Once a fork-mode Renovate run and a fork-mode release PR have each landed and been merged, these
`bots` environment secrets are dead weight and can be deleted:

- `TAP_RENOVATE_PRIVATE_KEY`
- `TAP_RELEASE_PLEASE_PRIVATE_KEY`

Both were deleted on 2026-09-28. The Apps themselves stay while tap's own `renovate.yml` and
`release-please.yml` use them (tap's repository secrets `RENOVATE_APP_ID`,
`RENOVATE_APP_PRIVATE_KEY`, `TAP_RELEASE_PLEASE_APP_PRIVATE_KEY` and variable
`TAP_RELEASE_PLEASE_APP_ID`). A key that is only deleted here can still mint tokens wherever it is
held elsewhere: revoke it in the App's settings as well.

## tap

tap is in scope for both bots, named rather than discovered, for the same coverage as every other repository. Three things stand between
it and a working fork-mode run.

### Open decision: tap's PR checks and a user-account bot

tap's sign-off (DCO) and issue-link checks exempt only the identities approved in tap's
`tap/tap.pr-bots.json`, and an entry there must have GitHub account type `Bot`, taken from the
authenticated PR event. The fork bot is a user account (type `User`), so every PR it opens on tap
fails both checks. Renovate and release-please must **not** add a `Signed-off-by` trailer to get
past this: an automated system never certifies the DCO. How tap should treat a user-account bot
is a policy decision for the maintainer, not something this repository works around.

### tap's own lanes

tap still runs its own `renovate.yml` and `release-please.yml` with the App keys. The
release-please job here skips tap while tap's `release-please.yml` exists, so the two lanes cannot
race for tap's release PR. Renovate here would run beside tap's own Renovate, so do not dispatch
`only=tap` or `only=all` until tap's `renovate.yml` is retired.

### tap's uv.lock refresh

tap's `uv.lock` records tap's own version, so a version-only bump makes `uv lock --check` fail on
the release PR. Today tap's `lock-refresh` job pushes a lock refresh onto the in-repo release
branch with the App token. In fork mode the branch is in the bot's fork. The options:

1. **Let release-please bump the lock line itself (recommended).** A TOML extra-file in tap's
   `release-please-config.json`:

   ```json
   { "type": "toml", "path": "uv.lock", "jsonpath": "$.package[?(@.name.value==\"tap\")].version" }
   ```

   The release PR is then coherent when it is opened, and no job pushes anything. Checked against
   tap's `pyproject.toml` and `uv.lock` at main: release-please 17.11.2's TOML updater changes
   exactly the one `version` line, and `uv lock --check` passes after the pyproject bump plus that
   edit (and fails on the pyproject bump alone).
2. **Refresh the lock from here, as the bot, on its own fork branch.** Possible: the bot can push
   to its fork. But it runs `uv lock` on tap's code in a job that holds the bot token, and adds a
   third-party setup step next to it.
3. **A maintainer pushes the refresh.** release-please opens fork PRs with "allow edits by
   maintainers" on, so a maintainer can push to the branch. A check on tap's release PR would say
   when that is needed. Manual every release.

### Follow-up in tap

1. **Renovate:** delete tap's `.github/workflows/renovate.yml`; drop the `prConcurrentLimit`
   override for tap in `renovate/global.js` if the fleet limits suit it. tap keeps its own
   `renovate.json5`: a `local>unified-systems-com/org-bots` preset is not used: the global
   config loads its preset from disk, which needs no platform read.
2. **release-please:** add the uv.lock extra-file (option 1 above) and delete tap's
   `.github/workflows/release-please.yml`, including its `lock-refresh` job.
3. **The PR-check decision above.**
4. **Keys:** delete tap's `RENOVATE_APP_ID`, `RENOVATE_APP_PRIVATE_KEY`,
   `TAP_RELEASE_PLEASE_APP_PRIVATE_KEY` and the `TAP_RELEASE_PLEASE_APP_ID` variable.
