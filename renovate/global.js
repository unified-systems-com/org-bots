// Renovate GLOBAL (self-hosted) configuration for the unified-systems-com fleet.
//
// This file is read by .github/workflows/renovate.yml. It says WHICH repositories the App
// works on (the repositories topic discovery found; tap is named below but the workflow never
// selects it) and the bot-wide rules. It does not say how each repository's dependencies
// are grouped or labelled: that is repository config, which comes from
//   - the repository's own renovate.json5, when it has one (today only tap does), or
//   - renovate/preset.js in this repo, which every discovered repository gets below.
//
// Renovate runs as the `tap-renovate` GitHub App. The workflow mints an installation token for
// the run's slice of the fleet only and passes it as the platform token; Renovate pushes its
// branches to the target repository and opens the PR from there, authored by the App's bot.
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
// tap's sign-off (DCO) and issue-link checks exempt only the approved identities in tap's
// tap/tap.pr-bots.json, which require GitHub account type `Bot`. tap-renovate[bot] (id
// 315114127, type Bot) is in that file, so its PRs pass both checks. Renovate must NOT add a
// Signed-off-by trailer: an automated system never certifies the DCO.
const SELF_CONFIGURED = [
  {
    repository: `${ORG}/tap`,
    // tap's former job ran with Renovate's default concurrent limit (10). Keep it, so
    // moving tap back onto Renovate changes who runs it and nothing else. Its hourly
    // limit is already the default 2, and its lockfile-maintenance branch is exempt from
    // both limits by tap's own config.
    prConcurrentLimit: 10,
  },
];

// Every other repository comes from topic discovery, never from a list here:
// scripts/discover_fleet.py lists the unified-systems-com repositories carrying the
// `tap-plugin` topic (owner-checked, archived and disabled ones dropped, org-bots refused) in a
// separate workflow job, and passes the result in as ORG_BOTS_FLEET, the JSON object
// {"core": [...], "fleet": [...]}. Only `fleet` is read here; tap stays named above whether or
// not it carries the topic. A repository joins by carrying the topic (new-plugin applies it)
// and leaves by losing it or being archived.
//
// Renovate's own autodiscover cannot do this: on GitHub it lists `GET /user/repos`, the
// repositories the token's user owns or has a role on. The App's token is scoped per run, and
// the list of repositories it can see is not a fleet definition. See scripts/discover_fleet.py.
const NAME = /^[A-Za-z0-9._-]+$/;
// The repository that runs this automation is never a target, whatever its topics say.
const REFUSED = new Set(["org-bots"]);
// A named repository keeps its named entry: if discovery also finds it, the named entry wins
// and the shared preset is not applied on top of it.
const NAMED = new Set(SELF_CONFIGURED.map((r) => r.repository));

function discoveredFleet(raw) {
  let parsed;
  try {
    parsed = JSON.parse(raw || "");
  } catch {
    throw new Error("ORG_BOTS_FLEET must be the JSON that scripts/discover_fleet.py prints");
  }
  const fleet = parsed && Array.isArray(parsed.fleet) ? parsed.fleet : null;
  if (!fleet || fleet.length === 0) {
    throw new Error("ORG_BOTS_FLEET lists no repositories; discovery must find at least one");
  }
  for (const name of fleet) {
    if (typeof name !== "string" || !NAME.test(name) || name === "." || name === "..") {
      throw new Error(`ORG_BOTS_FLEET: not a repository name: ${JSON.stringify(name).slice(0, 80)}`);
    }
    if (REFUSED.has(name)) {
      throw new Error(`ORG_BOTS_FLEET: ${name} is never a discovered repository`);
    }
  }
  if (new Set(fleet).size !== fleet.length) {
    throw new Error("ORG_BOTS_FLEET lists a repository twice");
  }
  return fleet.filter((name) => !NAMED.has(`${ORG}/${name}`));
}

const FLEET = discoveredFleet(process.env.ORG_BOTS_FLEET);

// Sorted by name, because the workflow scopes the token with the same cut over the sorted list
// of discovered names plus tap (every n-th from the i-th); the two must pick the same slice.
const ALL = [
  ...SELF_CONFIGURED,
  ...FLEET.map((name) => ({
    repository: `${ORG}/${name}`,
    ...FLEET_PRESET,
  })),
].sort((a, b) => (a.repository < b.repository ? -1 : a.repository > b.repository ? 1 : 0));

// Pilot: ORG_BOTS_ONLY=<repository name> narrows a run to ONE repository from the list
// above: tap, or a discovered repository. The workflow scopes the token to the run's
// repositories; discovery, and autodiscover off, are what decide which repositories get PRs.
// A name that is not in the list is an error, never a way to reach a repository discovery
// did not find (owner unified-systems-com AND the `tap-plugin` topic).
// "all" (or nothing) and "fleet" both mean every listed repository, tap included: tap takes
// part in the ramp's slices like any other repository. tap's own Renovate workflow was retired
// on 2026-09-29 (tap@b62b5f5e), so no second Renovate runs on it. A workflow_dispatch string
// input that is left empty is replaced by its default, so an empty value cannot be used to ask
// for "all".
const RAW = (process.env.ORG_BOTS_ONLY || "").trim();
const ONLY = RAW === "all" || RAW === "fleet" ? "" : RAW;
const repositories = ONLY ? ALL.filter((r) => r.repository === `${ORG}/${ONLY}`) : ALL;
if (ONLY && repositories.length !== 1) {
  throw new Error(`ORG_BOTS_ONLY=${ONLY} is not tap or a discovered repository`);
}

// The ramp. On 2026-09-28 the old fork bot's first fleet-wide run opened about 50 PRs in 20
// minutes, and GitHub's anti-abuse flagged the two-day-old account. So a fleet or all run
// covers ONE slice of the list: ORG_BOTS_BATCH=<i>/<n> keeps every n-th repository starting
// at the i-th (1-based), so n runs cover the whole list once. The workflow sets it (scripts/next_batch.py:
// the slice after the last successful auto run, unless the dispatch names one); "all" or unset means no slicing, for a one-repo run.
const BATCH = (process.env.ORG_BOTS_BATCH || "all").trim();
let batched = repositories;
if (!ONLY && BATCH !== "all") {
  const m = /^([1-9][0-9]*)\/([1-9][0-9]*)$/.exec(BATCH);
  if (!m || Number(m[1]) > Number(m[2])) {
    throw new Error(`ORG_BOTS_BATCH=${BATCH} is not <i>/<n> with 1 <= i <= n`);
  }
  const [i, n] = [Number(m[1]), Number(m[2])];
  batched = repositories.filter((_, k) => k % n === i - 1);
}

// The App's bot identity, from the workflow's environment (public values). Both are required:
//   RENOVATE_BOT_LOGIN  the bot user's login, `tap-renovate[bot]`
//   RENOVATE_BOT_ID     its numeric id: `gh api 'users/tap-renovate[bot]' --jq .id`
// Commits are then authored by the bot's own noreply address, never by Renovate's Mend-owned
// default. A missing or malformed value stops the run before any repository is touched, rather
// than committing under the wrong name.
const LOGIN = (process.env.RENOVATE_BOT_LOGIN || "").trim();
const ID = (process.env.RENOVATE_BOT_ID || "").trim();
if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$/.test(LOGIN) || !/^[1-9][0-9]*$/.test(ID)) {
  throw new Error("RENOVATE_BOT_LOGIN and RENOVATE_BOT_ID must be set to the App bot's login and numeric id");
}

module.exports = {
  platform: "github",
  // Never enumerate what the token can see: the list above (tap plus discovery) is the only
  // thing that decides which repositories get PRs.
  autodiscover: false,
  repositories: batched,

  username: LOGIN,
  gitAuthor: `${LOGIN} <${ID}+${LOGIN}@users.noreply.github.com>`,

  // No onboarding PRs, and a repository with no renovate config is still processed
  // (with the preset above).
  onboarding: false,
  requireConfig: "optional",

  // Pilot limits, per repository. A repository's own config can set its own.
  prConcurrentLimit: 2,
  prHourlyLimit: 2,

  // PR-only: a human merges every update.
  automerge: false,

  // postUpgradeTasks run a command inside the Renovate container, in the checkout of the
  // repository being updated, while the container holds the bot token. Exactly one command is
  // allowed: the boot-record digest refresh (renovate/preset.js, renovate/boot-records/), from
  // the read-only mount the workflow adds. The pattern is anchored at both ends and takes no
  // arguments, so a repository's own config cannot use it to run anything else. Commands run
  // without a shell (allowShellExecutorForPostUpgradeCommands, stated here so it stays off).
  allowedCommands: ["^python3 -I /github-action/boot-records/refresh\\.py$"],
  allowShellExecutorForPostUpgradeCommands: false,

  // No dependency-dashboard issues anywhere, tap included. `force` wins over repository
  // config, so tap's own renovate.json5 cannot turn it back on. An outside account's
  // issue can also be refused (issues off, or interaction limits), so the run would not
  // depend on it anyway.
  force: {
    dependencyDashboard: false,
  },
};
