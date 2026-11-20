// Shared Renovate repository config for the unified-systems-com fleet.
//
// renovate/global.js spreads this object into every listed repository that has no renovate
// config of its own. It is tap's renovate.json5 (tap@3e4467ab), minus the two things that
// are tap's own business:
//   - the dependency dashboard (config:recommended turns it on; global.js forces it off), and
//   - the lockfile-maintenance exemption from the PR limits (tap#625: tap's uv.lock only
//     moves through that branch, and wolfi-base's daily digest used to starve it).
// It adds two rules that are the fleet's own: exact pins for third-party pep621 runtime
// dependencies (tap's uv.lock already pins every version it installs) and the AWS SDK group
// (tap has no boto3).
//
// Why a module inside the global config, not a `local>unified-systems-com/org-bots:...`
// preset: Renovate resolves a `local>` preset through the platform with the run's token.
// The run's token is the fork bot's. Loaded from disk here, the config needs no platform read at
// all, so the preset never depends on what that token can see (this repository was private when
// the choice was made; it is public now, and the reason still holds).
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
      // A plugin has no lock file, so a floor (`boto3>=1.43.9`) is never "out of date" and
      // never gets a PR, while every fresh install resolves whatever is newest that day. That
      // is how botocore 1.44.4 broke aws-core-tap's CI on 2026-11-16 with no change of ours.
      // Ruling (aws-build Q72a, 2026-11-17): "we shouldn't get hit by floating library
      // changes, they should be intentionally updated after we've run tests." So third-party
      // runtime dependencies are pinned exactly: Renovate first opens a PR turning each range
      // into `==<current>`, then a PR per new release. Every bump is a PR that runs CI, and
      // automerge stays off, so nothing moves until a human merges it green.
      //
      // Scope:
      //   - matchPackageNames: our own packages (`computing-core-tap>=0.5.0,<0.11`) are
      //     ranges on purpose and are never pinned. The pep621 manager normalises packageName
      //     per PEP 503 (lowercase, runs of `-_.` to `-`), and globs match case-insensitively,
      //     so `!*-tap` also covers `Computing_Core_Tap`. Every fleet package name ends in `-tap`.
      //     `!tap` is the core distribution, should a plugin ever declare it: PyPI's `tap` is
      //     someone else's project, so a lookup would pin a stranger's package. A list of only
      //     negations matches every name the negations allow; `["*", "!*-tap"]` reads the same
      //     but the config validator rejects `*` beside other patterns.
      //   - matchDepTypes: what installs into tap alongside the plugin. Not
      //     `dependency-groups` (dev tools; plugin CI tests under tap's own uv.lock and never
      //     installs them), `build-system.requires` (the build backend, not a library the
      //     plugin runs), or `requires-python` (the interpreter, packageName `python`).
      matchManagers: ["pep621"],
      matchDepTypes: ["project.dependencies", "project.optional-dependencies"],
      matchPackageNames: ["!*-tap", "!tap"],
      rangeStrategy: "pin",
    },
    {
      // Except the libraries tap itself locks: a plugin installs into tap's environment, and
      // pre-boot's per-plugin `uv pip install` keeps an installed version that satisfies the
      // plugin's range but replaces it to satisfy a pin. So a plugin pin that Renovate moves
      // apart from tap's uv.lock silently moves tap's locked version (tap#1220). Left as
      // ranges, these follow tap's lock, which tap's own Renovate PRs move under tap's CI:
      // pinned in effect, by the one lock that can see the whole environment. A PR still
      // comes when a release falls outside the range (`replace`).
      //
      // Hand-kept: the third-party runtime libraries tap's uv.lock pins that a fleet plugin
      // also declares (2026-11-17: requests 2.34.2 for anchore, nh3 0.3.7 for stackarmor-vdr,
      // PyYAML 6.0.3 for github-core). PyYAML is locked only through tap's dev group, which
      // the entrypoint's `uv sync --all-packages` installs by default; an install without dev
      // groups would leave github-core's PyYAML floating. A plugin adding another library
      // tap locks adds it here.
      matchManagers: ["pep621"],
      matchDepTypes: ["project.dependencies", "project.optional-dependencies"],
      matchPackageNames: ["requests", "nh3", "pyyaml"],
      rangeStrategy: "replace",
    },
    {
      // boto3 X requires botocore >= X (within X's minor), so with both pinned exactly, a
      // boto3 bump and a botocore bump in separate PRs each fail resolution on their own. One
      // PR moves both. After the pep621 group above, because later rules win.
      matchManagers: ["pep621"],
      matchPackageNames: ["boto3", "botocore"],
      groupName: "AWS SDK",
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
      // An in-package boot record (tap_plugin/<slug>/boot/<name>.boot.json) is guarded by a
      // sha256 in its package's tap-plugin.toml ([[boot.records]]), and the plugin's checks
      // fail when the two disagree. So after bumping a record, rewrite that digest with tap's
      // own derivation (tap.boot_records, vendored at a pinned commit in renovate/boot-records/)
      // and commit the toml with the record. The command is allowed, exactly, by
      // `allowedCommands` in renovate/global.js; any other command is refused there.
      matchManagers: ["custom.regex"],
      matchFileNames: ["tap_plugin/*/boot/*.boot.json"],
      postUpgradeTasks: {
        commands: ["python3 -I /github-action/boot-records/refresh.py"],
        fileFilters: ["tap_plugin/*/tap-plugin.toml"],
      },
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
      // A reusable-workflow call on the branch itself (`...yml@main`) has no digest yet. The
      // custom manager below extracts it with currentValue `main` and no currentDigest, and
      // pinDigests turns that into a pinDigest update to the head of main, so the call is
      // pinned by the same PR that later keeps it current.
      matchManagers: ["custom.regex"],
      matchDatasources: ["git-refs"],
      matchDepNames: ["unified-systems-com/tap", "unified-systems-com/unified-ai-review"],
      pinDigests: true,
    },
    {
      // Later rules win: these replace the boot-record groupName above for the
      // reusable-workflow pins, one PR per upstream repository. The package rules run
      // again after the update-type config is merged, so they also replace pinDigest's
      // default "Pin Dependencies" group.
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
    {
      // The TAP floor (`requires_tap` in tap-plugin.toml, the third custom manager below) is
      // the core version a plugin's PR checks run against, so it is also the baseline the
      // plugin is proved on. Ruling (coley Q17a/Q19, 2026-11-20): the baseline is reset when
      // TAP's MINOR version moves, and the fleet is cut over together. So a new minor opens
      // one PR per plugin, raising the floor to the newest release of that minor; the PR's
      // own checks then run on that core, and a red one names a plugin with work to do.
      // Its own group and branch, after the boot-record group above (later rules win), so a
      // floor move is never bundled with plugin pins.
      matchManagers: ["custom.regex"],
      matchDatasources: ["github-releases"],
      matchDepNames: ["unified-systems-com/tap"],
      groupName: "tap floor (requires_tap)",
    },
    {
      // Patch releases never move the floor: a plugin tested on 0.4.2 is not re-floored for
      // 0.4.3. (Before 1.0 a breaking change goes in the minor, which Renovate also reports
      // as `minor`; `major` stays enabled for 1.0.)
      matchManagers: ["custom.regex"],
      matchDatasources: ["github-releases"],
      matchDepNames: ["unified-systems-com/tap"],
      matchUpdateTypes: ["patch"],
      enabled: false,
    },
  ],
  // Boot-record plugin pins (tap#635): a `rev` (the tag a human reads) and a `commit` (what
  // actually installs, tap#493) that must move together in one edit, or `tap.git_pin
  // --check` fails and the boot raises a moved-tag Flaw. Lookup returns the PEELED commit
  // of an annotated tag (verified on Renovate 44.103.1 in the tap#635 spike).
  //
  // Matches only entries that carry a `commit`. Since 2026-09-27 every fleet repository's
  // in-package CI record (tap_plugin/<slug>/boot/ci.boot.json) pins `commit`, as do some
  // entries of the product records; entries that pin `url` + `rev` alone are not matched.
  // Every such record's digest is refreshed by the postUpgradeTasks rule above.
  //
  // No autoReplaceStringTemplate: Renovate then edits the matched text in place, replacing
  // the old `rev` value and the old commit, so the record keeps its own indentation and line
  // breaks. (The digest is over canonicalized JSON, so layout would not move it anyway.)
  customManagers: [
    {
      customType: "regex",
      managerFilePatterns: ["/boot/.*\\.boot\\.json$/"],
      matchStrings: [
        "\"url\": \"https://github\\.com/(?<packageName>[^\"]+)\",\\s*\"rev\": \"(?<currentValue>[^\"]+)\",\\s*\"commit\": \"(?<currentDigest>[0-9a-f]{40})\"",
      ],
      datasourceTemplate: "github-tags",
      versioningTemplate: "semver",
    },
    // Reusable-workflow calls (`uses: unified-systems-com/tap/.github/workflows/<f>.yml@<ref>`,
    // and the same into unified-ai-review) follow the head of that repository's `main`.
    // The ref is either a pin, a bare 40-character SHA (any trailing comment is optional and
    // never read), or the branch `main` itself, which the pinDigests rule above pins.
    //
    // ONE matchString with an alternation, not one per form: the regex manager lists deps
    // matchString by matchString, and a pin moves a line from one form to the other, so two
    // matchStrings would renumber the deps and fail Renovate's post-edit re-extraction. It
    // also means one line is only ever matched once.
    //
    // The match stops at the ref, and the template rewrites only the ref at its end, so the
    // rest of the line (quoting, spacing, a trailing comment) stays as it was. The template
    // is needed because a pin has no currentDigest to substitute: without it Renovate would
    // replace `main` with `main`.
    {
      customType: "regex",
      managerFilePatterns: ["/^\\.github/workflows/[^/]+\\.ya?ml$/"],
      matchStrings: [
        "uses:\\s*[\"']?unified-systems-com/(?<repo>tap|unified-ai-review)/\\.github/workflows/[^@\\s\"']+\\.ya?ml@(?:(?<currentDigest>[0-9a-f]{40})|main)\\b",
      ],
      depNameTemplate: "unified-systems-com/{{{repo}}}",
      packageNameTemplate: "https://github.com/unified-systems-com/{{{repo}}}",
      currentValueTemplate: "main",
      datasourceTemplate: "git-refs",
      autoReplaceStringTemplate: "{{{replace '(?:[0-9a-f]{40}|main)$' newDigest replaceString}}}",
    },
    // The TAP floor: `requires_tap = ">=0.4.2"` in a plugin's manifest, looked up as a GitHub
    // release of tap (tags are `v0.4.2`; semver versioning reads them with or without the
    // `v`, and the edit keeps the manifest's bare form). Only the plain one-bound form is
    // matched: a manifest that states an upper bound or a second clause has made a decision
    // this rule must not rewrite. A trailing comment on the line is left as it is. Which
    // updates are offered is decided by the two package rules above (minor steps only).
    {
      customType: "regex",
      managerFilePatterns: ["/^tap_plugin/[^/]+/tap-plugin\\.toml$/"],
      matchStrings: ["(?:^|\\n)requires_tap\\s*=\\s*\">=\\s*(?<currentValue>\\d+\\.\\d+\\.\\d+)\""],
      depNameTemplate: "unified-systems-com/tap",
      datasourceTemplate: "github-releases",
      versioningTemplate: "semver",
      extractVersionTemplate: "^v(?<version>.+)$",
    },
  ],
  lockFileMaintenance: {
    enabled: true,
    // config:recommended's built-in Monday-4am window would defer these PRs invisibly.
    schedule: ["at any time"],
  },
};
