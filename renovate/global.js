// Renovate GLOBAL (self-hosted) configuration for the unified-systems-com fleet.
//
// This file is read by .github/workflows/renovate.yml. It says WHICH repositories the fork
// bot works on (tap, named below, plus the repositories topic discovery found) and the
// bot-wide rules. It does not say how each repository's dependencies
// are grouped or labelled: that is repository config, which comes from
//   - the repository's own renovate.json5, when it has one (today only tap does), or
//   - renovate/preset.js in this repo, which every discovered repository gets below.
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

// Every other repository comes from topic discovery, never from a list here:
// scripts/discover_fleet.py lists the unified-systems-com repositories carrying the
// `tap-plugin` topic (owner-checked, archived and disabled ones dropped, org-bots refused) in a
// separate workflow job, and passes the result in as ORG_BOTS_FLEET, the JSON object
// {"core": [...], "fleet": [...]}. Only `fleet` is read here; tap stays named above whether or
// not it carries the topic. A repository joins by carrying the topic (new-plugin applies it)
// and leaves by losing it or being archived.
//
// Renovate's own autodiscover cannot do this: on GitHub it lists `GET /user/repos`, the
// repositories the token's user owns or has a role on. The fork bot has no role anywhere, so
// that is only its own forks, which carry no topics. See scripts/discover_fleet.py.
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

const ALL = [
  ...SELF_CONFIGURED,
  ...FLEET.map((name) => ({
    repository: `${ORG}/${name}`,
    ...FLEET_PRESET,
  })),
];

// Pilot: ORG_BOTS_ONLY=<repository name> narrows a run to ONE repository from the list
// above: tap, or a discovered repository. The token is a user token, so it is not scoped per
// repository: discovery, and autodiscover off, are what decide which repositories get PRs.
// A name that is not in the list is an error, never a way to reach a repository discovery
// did not find (owner unified-systems-com AND the `tap-plugin` topic).
// "all" (or nothing) means every listed repository. "fleet" means every discovered
// repository but not tap: tap still runs its own Renovate (App mode, .github/workflows/
// renovate.yml in tap) until that is retired, and two Renovates on one repository open
// duplicate PRs. A workflow_dispatch string input that is left empty is replaced by its
// default, so an empty value cannot be used to ask for "all".
const RAW = (process.env.ORG_BOTS_ONLY || "").trim();
const ONLY = RAW === "all" || RAW === "fleet" ? "" : RAW;
const repositories =
  RAW === "fleet"
    ? ALL.filter((r) => r.repository !== `${ORG}/tap`)
    : ONLY
      ? ALL.filter((r) => r.repository === `${ORG}/${ONLY}`)
      : ALL;
if (ONLY && repositories.length !== 1) {
  throw new Error(`ORG_BOTS_ONLY=${ONLY} is not tap or a discovered repository`);
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
  // Never enumerate what the token can see: the list above (tap plus discovery) is the only
  // thing that decides which repositories get PRs.
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
