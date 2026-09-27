# org-bots

This repository runs the unified-systems-com organisation's two write-capable bots from one small
place:

| Bot | GitHub App | Workflow | Does |
| --- | --- | --- | --- |
| Renovate | `tap-renovate` | `.github/workflows/renovate.yml` | Opens dependency-update PRs across the fleet |
| release-please | `tap-release-please` | `.github/workflows/release-please.yml` | Opens release PRs, and tags and publishes a release once a human merges one |

Both Apps are installed org-wide. Their private keys are the most powerful credentials the org's
CI holds. Before this repository existed, they sat in tap's repository secrets. There, every tap
workflow, and every maintainer of tap, was one step from them. They now live here, and nowhere
else.

The repository is **private**. This is a deliberate exception to the org's public-by-default rule.
Nothing here is secret, but a private repository has a smaller audience that can open PRs,
comment, or run workflows against it.

## What is in it

| Path | What it is |
| --- | --- |
| `.github/workflows/renovate.yml` | Renovate run. Pilot: manual dispatch only, one repository per run (`only`, default `tap-plugin-github-core`); the daily schedule returns when the pilot ends |
| `renovate/global.js` | Renovate's global config: the explicit repository list, the bot identity, PR limits, and no onboarding |
| `renovate/token-scope.js` | Derives the Renovate token's repository scope from `global.js` |
| `default.json5` | The shared repository preset (`local>unified-systems-com/org-bots:default.json5`), including tap's boot-record pin manager |
| `.github/workflows/release-please.yml` | release-please over `RELEASE_REPOS`. Pilot: manual dispatch only; the hourly schedule returns when the pilot ends |
| `release-please/package.json`, `package-lock.json` | Pin the release-please CLI and its whole dependency closure |
| `.github/workflows/fleet-conformance.yml` | Nightly repository-scope conformance sweep (`validate_plugin --repo`) over every plugin repository in the org, discovered by name with tap's `tap.plugin_identity`. Read-only `GITHUB_TOKEN`; no App key, no `bots` environment; reports in the job summary and goes red on a failed check |
| `.github/CODEOWNERS` | Every path needs the owner's review |

## Threat model

**What an attacker wants here:** a token that can push to, and open PRs on, every repository in
the org. It could also edit workflow files, in Renovate's case, or create release tags, which
release-please does.

**Where the keys are.** Each key is a secret of the `bots` **environment**, never a
repository-level secret:

- `TAP_RENOVATE_PRIVATE_KEY`
- `TAP_RELEASE_PLEASE_PRIVATE_KEY`

The environment's deployment branch policy allows `main` only. A job that names `bots` from any
other branch or tag gets no secrets, so a key reaches only a workflow already merged to `main`.
Getting a workflow onto `main` takes a PR that the code owner reviewed.

**What a run holds.** Each run mints an installation token that lasts about an hour. The token is
scoped to three things:

- **the repositories that job works on.** For Renovate, the list comes from the same
  `renovate/global.js` that Renovate reads, so the two cannot drift apart. For release-please,
  it is `RELEASE_REPOS`.
- **the permissions that job needs.** For Renovate, that is contents, pull-requests, workflows
  and issues write, plus checks, statuses and vulnerability-alerts read. For release-please, it
  is contents and pull-requests write.
- **the workflow's `GITHUB_TOKEN`,** which is read-only (`contents: read`, to check this repository
  out). Every workflow starts from `permissions: {}`.

**What can run with the token.** For Renovate, that is the Renovate container, pinned by image
digest. For release-please, it is the CLI, installed with `npm ci --ignore-scripts` from a lockfile
that pins every package by integrity hash. Every action is pinned by full commit SHA.

**What the bots cannot do.** Neither bot merges anything. Renovate runs with automerge off.
release-please only proposes a release PR, and a release happens when a human merges that PR in the
target repository, through that repository's own rulesets and checks. Tags are created by the App
token. The org `tag-protection` ruleset then prevents their deletion or update.

**What is outside this repo's control.** Both Apps are installed on *all* repositories. A leaked key
can mint a token for any of them, whatever these workflows scope it to. The scoping limits what a
compromised *run* can reach. It does not limit what a stolen *key* can reach. Treat a leaked key as
an org-wide incident: revoke it in the App's settings first.

## Who may change it

- **Code:** anything merged to `main` needs a PR with the code owner's review (`.github/CODEOWNERS`,
  enforced by the org `org-require-pr` ruleset).
- **Keys and the `bots` environment:** repository admins only, which in practice means the org
  owner. Keep the admin list to the minimum.
- **The App definitions and installations** (permissions, which repositories): the org owner, in
  the org's GitHub App settings. Widening an App's permissions widens what every token here can
  be asked for.

## Adding a repository

- **Renovate:** add the repository name to `FLEET` in `renovate/global.js`. It gets
  `default.json5` unless it carries a renovate config of its own; then list it the way `tap` is
  listed.
- **release-please:** first give the repository its own `release-please-config.json` and
  `.release-please-manifest.json`, the way tap-plugin-github-core has them. Then append its name
  to `RELEASE_REPOS` in `release-please.yml`.

## Running a job by hand

```sh
gh workflow run renovate.yml -R unified-systems-com/org-bots            # pilot: github-core only; add -f logLevel=debug to troubleshoot
gh workflow run renovate.yml -R unified-systems-com/org-bots -f only=tap-plugin-gryphon-playground   # another single listed repo
gh workflow run renovate.yml -R unified-systems-com/org-bots -f only=all  # every listed repository (an empty value falls back to the default)
gh workflow run release-please.yml -R unified-systems-com/org-bots
gh workflow run fleet-conformance.yml -R unified-systems-com/org-bots   # the conformance sweep, off-schedule
gh run list -R unified-systems-com/org-bots --limit 5
```

## Follow-up in tap

tap still runs its own `renovate.yml` and `release-please.yml` with the keys in its repository
secrets. Those move here once these jobs have proven themselves:

1. **Renovate:**
   - delete tap's `.github/workflows/renovate.yml`;
   - reduce tap's `renovate.json5` to `extends: ["local>unified-systems-com/org-bots:default.json5",
     ":dependencyDashboard"]` plus its lockfile-maintenance limit exemption;
   - drop the `prConcurrentLimit` override for tap in `renovate/global.js`.
2. **release-please:**
   - add tap to `RELEASE_REPOS`, with its own `lock-refresh` step;
   - delete tap's `.github/workflows/release-please.yml`.
3. **Keys:** delete tap's `RENOVATE_APP_ID`, `RENOVATE_APP_PRIVATE_KEY`,
   `TAP_RELEASE_PLEASE_APP_PRIVATE_KEY` and the `TAP_RELEASE_PLEASE_APP_ID` variable.
