// Shared Renovate repository config for the unified-systems-com fleet.
//
// renovate/global.js spreads this object into every listed repository that has no renovate
// config of its own. It is tap's renovate.json5 (tap@3e4467ab), minus the two things that
// are tap's own business:
//   - the dependency dashboard (config:recommended turns it on; global.js forces it off), and
//   - the lockfile-maintenance exemption from the PR limits (tap#625: tap's uv.lock only
//     moves through that branch, and wolfi-base's daily digest used to starve it).
//
// Why a module inside the global config, not a `local>unified-systems-com/org-bots:...`
// preset: Renovate resolves a `local>` preset through the platform with the run's token.
// The run's token is now the fork bot's, and the fork bot has no access to this private
// repository, so the preset lookup would fail on every repository. Loaded from disk here,
// the config needs no platform read at all.
module.exports = {
  extends: [
    "config:recommended",
    // Every `uses:` becomes an immutable commit-SHA pin (`@<sha> # vX.Y.Z`), kept current
    // by reviewable PRs. A moved upstream tag is then a no-op here (the 2026-03-19
    // trivy-action compromise shipped through moved tags).
    "helpers:pinGitHubActionDigests",
  ],
  // `custom.regex` must be listed for customManagers to run at all: an enabledManagers
  // list without it silently disables them (tap#635).
  enabledManagers: ["dockerfile", "github-actions", "npm", "pep621", "custom.regex"],
  labels: ["renovate"],
  automerge: false,
  osvVulnerabilityAlerts: true,
  vulnerabilityAlerts: {
    labels: ["renovate", "security"],
  },
  // No schedules while PRs are merged by hand: a schedule window only defers PR creation
  // where nobody sees it (tap's 2026-08-09 first run: four green runs, zero PRs).
  packageRules: [
    {
      // Our own published images: never chase our own tags.
      matchPackageNames: ["ghcr.io/unified-systems-com/**"],
      enabled: false,
    },
    {
      // aquasecurity's org IP allow list rejects App-token API lookups from Actions
      // runners (github.com/orgs/community/discussions/178332), so every run would WARN
      // "no-result" here. The pin is a hand-verified commit SHA. Revisit quarterly.
      matchPackageNames: ["aquasecurity/trivy-action"],
      enabled: false,
    },
    {
      // wolfi-base rotates its digest daily: one rolling PR carries Chainguard's patches.
      matchManagers: ["dockerfile"],
      groupName: "base-image digests",
    },
    {
      matchManagers: ["github-actions"],
      groupName: "github-actions",
    },
    {
      matchManagers: ["pep621"],
      groupName: "python dependencies (uv.lock)",
    },
    {
      matchManagers: ["npm"],
      groupName: "browser libraries (package-lock.json)",
    },
    {
      // One PR per plugin-pin bump, ahead of the daily digest churn (tap#625).
      matchManagers: ["custom.regex"],
      groupName: "plugin pins (boot records)",
      prPriority: 5,
    },
    {
      // Reusable-workflow pins into tap and unified-ai-review belong to the custom manager
      // below. The built-in github-actions manager also extracts them: it skips a bare or
      // dated-comment pin (unversioned-reference) but tracks a `@<sha> # main` pin through
      // github-digest, which would open a second PR editing the same line. Off here, so
      // each pin has one owner. RE2, which Renovate compiles matchStrings with, has no
      // lookahead, so a matchStrings negative case cannot express this.
      matchManagers: ["github-actions"],
      matchDepTypes: ["workflow"],
      matchPackageNames: ["unified-systems-com/tap", "unified-systems-com/unified-ai-review"],
      enabled: false,
    },
    {
      // Later rules win: these replace the boot-record groupName above for the
      // reusable-workflow pins, one PR per upstream repository.
      matchManagers: ["custom.regex"],
      matchDatasources: ["git-refs"],
      matchDepNames: ["unified-systems-com/tap"],
      groupName: "tap reusable workflows",
    },
    {
      matchManagers: ["custom.regex"],
      matchDatasources: ["git-refs"],
      matchDepNames: ["unified-systems-com/unified-ai-review"],
      groupName: "unified-ai-review reusable workflows",
    },
  ],
  // Boot-record plugin pins (tap#635): a `rev` (the tag a human reads) and a `commit` (what
  // actually installs, tap#493) that must move together in one edit, or `tap.git_pin
  // --check` fails and the boot raises a moved-tag Flaw. Lookup returns the PEELED commit
  // of an annotated tag (verified on Renovate 44.103.1 in the tap#635 spike).
  //
  // Matches only entries that carry a `commit`. Plugin and product boot records today pin
  // `url` + `rev` alone, so this manager finds nothing in them.
  customManagers: [
    {
      customType: "regex",
      managerFilePatterns: ["/boot/.*\\.boot\\.json$/"],
      matchStrings: [
        "\"url\": \"https://github\\.com/(?<packageName>[^\"]+)\",\\s*\"rev\": \"(?<currentValue>[^\"]+)\",\\s*\"commit\": \"(?<currentDigest>[0-9a-f]{40})\"",
      ],
      autoReplaceStringTemplate:
        "\"url\": \"https://github.com/{{{packageName}}}\",\n          \"rev\": \"{{{newValue}}}\",\n          \"commit\": \"{{{newDigest}}}\"",
      datasourceTemplate: "github-tags",
      versioningTemplate: "semver",
    },
    // Reusable-workflow pins (`uses: unified-systems-com/tap/.github/workflows/<f>.yml@<sha>`,
    // and the same into unified-ai-review) follow the head of that repository's `main`.
    // A pin is a bare 40-character SHA; any trailing comment is optional and never read.
    // The match stops at the SHA, so the update rewrites the SHA alone and leaves a
    // trailing comment as it was.
    {
      customType: "regex",
      managerFilePatterns: ["/^\\.github/workflows/[^/]+\\.ya?ml$/"],
      matchStrings: [
        "uses:\\s*[\"']?unified-systems-com/(?<repo>tap|unified-ai-review)/\\.github/workflows/[^@\\s\"']+\\.ya?ml@(?<currentDigest>[0-9a-f]{40})\\b",
      ],
      depNameTemplate: "unified-systems-com/{{{repo}}}",
      packageNameTemplate: "https://github.com/unified-systems-com/{{{repo}}}",
      currentValueTemplate: "main",
      datasourceTemplate: "git-refs",
    },
  ],
  lockFileMaintenance: {
    enabled: true,
    // config:recommended's built-in Monday-4am window would defer these PRs invisibly.
    schedule: ["at any time"],
  },
};
