// Renovate GLOBAL (self-hosted) configuration for the unified-systems-com fleet.
//
// This file is read by .github/workflows/renovate.yml. It says WHICH repositories the
// tap-renovate App works on and the bot-wide rules. It does not say how each repository's
// dependencies are grouped or labelled: that is repository config, which comes from
//   - the repository's own renovate.json5, when it has one (today only tap does), or
//   - default.json5 in this repo, which every other listed repository extends below.
//
// CommonJS rather than JSON5 for one reason: the workflow reads `repositories` from this
// same module to scope the App token, so the list the token covers and the list Renovate
// works on cannot drift apart.

const ORG = "unified-systems-com";

// The shared preset. `local>` resolves on the platform Renovate is running against, with
// the App token, which is why the workflow's token also covers this (private) repo.
const FLEET_PRESET = `local>${ORG}/org-bots:default.json5`;

// tap carries its own renovate.json5, with the same boot-record custom manager as
// default.json5. It is listed WITHOUT the preset: extending both would register that
// manager twice. tap moves onto the preset in the follow-up that retires its own
// renovate.yml (see README, "Follow-up in tap").
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

module.exports = {
  platform: "github",
  // Never enumerate the installation: the App is installed org-wide, and this list is the
  // only thing that decides which repositories get PRs.
  autodiscover: false,
  repositories: [
    ...SELF_CONFIGURED,
    ...FLEET.map((name) => ({
      repository: `${ORG}/${name}`,
      extends: [FLEET_PRESET],
      // The preset extends config:recommended, which turns the dashboard on; a key set
      // here beats the presets it extends.
      dependencyDashboard: false,
    })),
  ],

  // Commits are authored by the App's own bot account (user id from
  // `gh api users/tap-renovate%5Bbot%5D`), never by Renovate's Mend-owned default address.
  username: "tap-renovate[bot]",
  gitAuthor: "tap-renovate[bot] <315114127+tap-renovate[bot]@users.noreply.github.com>",

  // No onboarding PRs, and a repository with no renovate config is still processed
  // (with the preset above).
  onboarding: false,
  requireConfig: "optional",

  // Pilot limits, per repository. A repository's own config can set its own.
  prConcurrentLimit: 2,
  prHourlyLimit: 2,

  // PR-only: a human merges every update.
  automerge: false,

  // No dependency-dashboard issues across the fleet during the pilot. tap's own config
  // turns its dashboard back on, as it has today.
  dependencyDashboard: false,
};
