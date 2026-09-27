// Renovate GLOBAL (self-hosted) configuration for the unified-systems-com fleet.
//
// This file is read by .github/workflows/renovate.yml. It says WHICH repositories the fork
// bot works on and the bot-wide rules. It does not say how each repository's dependencies
// are grouped or labelled: that is repository config, which comes from
//   - the repository's own renovate.json5, when it has one (today only tap does), or
//   - renovate/preset.js in this repo, which every other listed repository gets below.
//
// Fork mode: Renovate runs as a machine user that has no role on any org repository. It
// forks each repository into its own account, pushes update branches there, and opens PRs
// from the fork, as an outside contributor would. The workflow passes the bot's token as
// both the platform token and RENOVATE_FORK_TOKEN.
//
// CommonJS rather than JSON5 so the bot identity comes from the environment (below) and the
// shared repository config can be loaded from disk (renovate/preset.js explains why).

const ORG = "unified-systems-com";

// The shared repository config, loaded from disk (see renovate/preset.js for why).
const FLEET_PRESET = require("./preset.js");

// tap carries its own renovate.json5, with the same boot-record custom manager as
// preset.js. It is listed WITHOUT the shared config: applying both would register that
// manager twice.
//
// Open policy decision: tap's sign-off (DCO) and issue-link checks exempt only the approved
// identities in tap's tap/tap.pr-bots.json, and those require GitHub account type `Bot`
// (a GitHub App). The fork bot is a user account, so its PRs to tap fail both checks until
// that rule changes. Renovate must NOT add a Signed-off-by trailer to get past it: an
// automated system never certifies the DCO. tap stays listed because it gets the same
// coverage as every other repository; see README, "Open decision: tap's PR checks".
const SELF_CONFIGURED = [
  {
    repository: `${ORG}/tap`,
    // tap's current job runs with Renovate's default concurrent limit (10). Keep it, so
    // moving tap onto this job changes who runs Renovate and nothing else. Its hourly
    // limit is already the default 2, and its lockfile-maintenance branch is exempt from
    // both limits by tap's own config.
    prConcurrentLimit: 10,
  },
];

// Plugin repositories, then the two products. Chosen from `gh repo list unified-systems-com`
// on 2026-09-26. Deliberately absent: git-serious.com (the website), .github (org community
// files), git-serious-fixtures (known-answer fixtures: an "update" would falsify them),
// tap-dev-hooks and tap-build-dependencies (supply-chain surfaces that pin by hand),
// unified-ai-review and unified-ai-review-prompts (not TAP plugins), the archived
// tap-plugin-aws-secrets-source, and this repository.
const FLEET = [
  // plugins
  "tap-plugin-administrivia",
  "tap-plugin-aws-core",
  "tap-plugin-compliance-core",
  "tap-plugin-computing-core",
  "tap-plugin-fedramp-20x-ksi",
  "tap-plugin-github-core",
  "tap-plugin-grid-fixtures",
  "tap-plugin-gryphon-playground",
  "tap-plugin-identity-core",
  "tap-plugin-roscale",
  "tap-plugin-sigstore-core",
  "dcom-tap",
  "deployment-environment-tap",
  "duo-tap",
  "git-core-tap",
  "git-serious-double-tap",
  "gitlab-tap",
  "gruntwork-tap",
  "okta-tap",
  "project-management-core-tap",
  "teleport-tap",
  "zizmor-tap",
  // products
  "git-serious-tap",
  "tap-plugin-samsite",
];

const ALL = [
  ...SELF_CONFIGURED,
  ...FLEET.map((name) => ({
    repository: `${ORG}/${name}`,
    ...FLEET_PRESET,
  })),
];

// Pilot: ORG_BOTS_ONLY=<repository name> narrows a run to ONE repository from the list
// above. The token is a user token, so it is not scoped per repository: this list, and
// autodiscover off, are what decide which repositories get PRs. A name
// that is not in the list is an error, never a way to reach an unlisted repository.
// "all" (or nothing) means every listed repository. A workflow_dispatch string input that
// is left empty is replaced by its default, so an empty value cannot be used to ask for "all".
const RAW = (process.env.ORG_BOTS_ONLY || "").trim();
const ONLY = RAW === "all" ? "" : RAW;
const repositories = ONLY ? ALL.filter((r) => r.repository === `${ORG}/${ONLY}`) : ALL;
if (ONLY && repositories.length !== 1) {
  throw new Error(`ORG_BOTS_ONLY=${ONLY} is not one of the listed repositories`);
}

// The fork bot's identity, from the `bots` environment's VARIABLES (not secrets: neither
// is sensitive). Set both when the account exists:
//   FORK_BOT_LOGIN  the account's login
//   FORK_BOT_ID     its numeric id: `gh api users/<login> --jq .id`
// Commits are then authored by the bot's own noreply address, never by Renovate's
// Mend-owned default. A missing or malformed value stops the run before any repository is
// touched, rather than committing under the wrong name.
const LOGIN = (process.env.FORK_BOT_LOGIN || "").trim();
const ID = (process.env.FORK_BOT_ID || "").trim();
if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(LOGIN) || !/^[1-9][0-9]*$/.test(ID)) {
  throw new Error("FORK_BOT_LOGIN and FORK_BOT_ID must be set to the fork bot's login and numeric id");
}

module.exports = {
  platform: "github",
  // Never enumerate what the token can see: this list is the only thing that decides which
  // repositories get PRs.
  autodiscover: false,
  repositories,

  username: LOGIN,
  gitAuthor: `${LOGIN} <${ID}+${LOGIN}@users.noreply.github.com>`,

  // Fork mode (the token itself arrives as RENOVATE_FORK_TOKEN). The fork is created on
  // first use and reused after that.
  forkCreation: true,

  // No onboarding PRs, and a repository with no renovate config is still processed
  // (with the preset above).
  onboarding: false,
  requireConfig: "optional",

  // Pilot limits, per repository. A repository's own config can set its own.
  prConcurrentLimit: 2,
  prHourlyLimit: 2,

  // PR-only: a human merges every update.
  automerge: false,

  // No dependency-dashboard issues anywhere, tap included. `force` wins over repository
  // config, so tap's own renovate.json5 cannot turn it back on. An outside account's
  // issue can also be refused (issues off, or interaction limits), so the run would not
  // depend on it anyway.
  force: {
    dependencyDashboard: false,
  },
};
