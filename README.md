# org-bots

This repository runs the unified-systems-com organisation's two bots from one small place, and
since fork mode it holds **no credential that can write to an org repository**:

| Bot | Runs as | Where | Does |
| --- | --- | --- | --- |
| Renovate | the fork bot (a machine user) | `.github/workflows/renovate.yml` | Opens dependency-update PRs from the bot's forks |
| release-please, stage 1 | the fork bot | `.github/workflows/release-please.yml` | Opens and refreshes each repository's release PR from the bot's fork |
| release-please, stage 2 | the maintainer, with their own `gh` login | `scripts/cut-release.sh` | Tags the merged release PR and publishes the GitHub Release |

The repository is **private**. This is a deliberate exception to the org's public-by-default rule.
Nothing here is secret, but a private repository has a smaller audience that can open PRs,
comment, or run workflows against it.

## What is in it

| Path | What it is |
| --- | --- |
| `.github/workflows/renovate.yml` | Renovate in fork mode. Pilot: manual dispatch only, one repository per run (`only`, default `tap-plugin-github-core`); the daily schedule returns when the pilot ends |
| `renovate/global.js` | Renovate's global config: the explicit repository list, the bot identity (from environment variables), PR limits, fork mode, no onboarding, no dashboard |
| `renovate/preset.js` | The shared repository config every listed repository without its own gets, including tap's boot-record pin manager |
| `.github/workflows/release-please.yml` | release-please stage 1 (`release-pr --fork`) over `RELEASE_REPOS`. Pilot: manual dispatch only |
| `scripts/cut-release.sh` | release-please stage 2 (`github-release`), run by the maintainer. Dry run by default |
| `release-please/package.json`, `package-lock.json` | Pin the release-please CLI and its whole dependency closure, for CI and for the script |
| `.github/CODEOWNERS` | Every path needs the owner's review |

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
  first. Until checks run, the PR is not mergeable.
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
| `TAP_RENOVATE_PRIVATE_KEY`, `TAP_RELEASE_PLEASE_PRIVATE_KEY` | the `bots` environment, **no longer read by any workflow here** | every org repository the two Apps are installed on. See "Retiring the App keys" |

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

**What can run with the token.** For Renovate, the Renovate container, pinned by image digest.
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
   scripts/cut-release.sh tap-plugin-github-core
   ```

3. Cut it:

   ```sh
   scripts/cut-release.sh tap-plugin-github-core --yes
   ```

   It labels the merged release PR if needed, dry-runs again and refuses unless the tag matches
   the repository's `.release-please-manifest.json`, runs `release-please github-release` with
   your `gh auth token`, then confirms the tag, checks the PR was relabelled
   `autorelease: tagged`, and lists the target's `release-sbom.yml` runs, which the tag push
   starts.

The CLI runs in a disposable `node:22-slim` container (pinned by digest) from this repository's
lockfile. Your token reaches the container only as an environment variable. The script never
writes it to disk and never prints it.

## Adding a repository

- **Renovate:** add the repository name to `FLEET` in `renovate/global.js`. It gets
  `renovate/preset.js` unless it carries a renovate config of its own; then list it the way `tap`
  is listed.
- **release-please:** first give the repository its own `release-please-config.json` and
  `.release-please-manifest.json`, the way tap-plugin-github-core has them. Then append its name
  to `RELEASE_REPOS` in `release-please.yml`. A repository that still runs its own
  `release-please.yml` is skipped with a warning until that workflow is retired.

## Running a job by hand

```sh
gh workflow run renovate.yml -R unified-systems-com/org-bots            # pilot: github-core only; add -f logLevel=debug to troubleshoot
gh workflow run renovate.yml -R unified-systems-com/org-bots -f only=tap-plugin-gryphon-playground   # another single listed repo
gh workflow run renovate.yml -R unified-systems-com/org-bots -f only=all  # every listed repository (an empty value falls back to the default)
gh workflow run release-please.yml -R unified-systems-com/org-bots
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

They are not deleted by this change. The Apps themselves stay while tap's own `renovate.yml` and
`release-please.yml` use them (tap's repository secrets `RENOVATE_APP_ID`,
`RENOVATE_APP_PRIVATE_KEY`, `TAP_RELEASE_PLEASE_APP_PRIVATE_KEY` and variable
`TAP_RELEASE_PLEASE_APP_ID`). A key that is only deleted here can still mint tokens wherever it is
held elsewhere: revoke it in the App's settings as well.

## tap

tap is in both lists, for the same coverage as every other repository. Three things stand between
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
   `renovate.json5`: a `local>unified-systems-com/org-bots` preset would not resolve, because the
   bot cannot read this private repository.
2. **release-please:** add the uv.lock extra-file (option 1 above) and delete tap's
   `.github/workflows/release-please.yml`, including its `lock-refresh` job.
3. **The PR-check decision above.**
4. **Keys:** delete tap's `RENOVATE_APP_ID`, `RENOVATE_APP_PRIVATE_KEY`,
   `TAP_RELEASE_PLEASE_APP_PRIVATE_KEY` and the `TAP_RELEASE_PLEASE_APP_ID` variable.
