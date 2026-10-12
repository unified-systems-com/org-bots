#!/usr/bin/env bash
# Run Renovate or release-please (stage 1, the release PR) against ONE repository, as YOU, from
# your machine. The hand process for a repository no bot serves: tap after its App lanes were
# retired (George, bom-bom Q75a, 2026-09-29), and any fleet repository while the fork bot is
# unavailable.
#
#   scripts/run-as-maintainer.sh <repo> renovate [--dry-run | --yes]
#   scripts/run-as-maintainer.sh <repo> release  [--dry-run | --yes]
#
# Default is --dry-run: report what would change, write nothing. --yes writes.
#
# What --yes does:
#   renovate  Renovate, not in fork mode, with your token, on <repo> only. It needs the
#             repository's own Renovate config (tap has renovate.json5). Its branches are
#             committed as you, with the commit subject suffixed "[via maintainer-run]" so the
#             PR title passes tap's title check.
#   release   `release-please release-pr` with your token (not --fork), which opens or
#             refreshes the release PR and labels it. Cut the release afterwards with
#             scripts/cut-release.sh, as for every repository.
#   then      Sign-off. The tools' commits carry no Signed-off-by, and tap's DCO check wants one
#             on every commit. For each open branch the tool just wrote (renovate/* or
#             release-please--*), whose ONE commit over the default branch is yours and
#             unsigned, the commit is amended here with `git commit -s` and an issue-link
#             trailer, and force-pushed with a lease. That is your tooling applying your
#             trailer at your command (CONTRIBUTING "Sign-Off"); the certification is still
#             your review and merge of the PR.
#
# Pacing (tap spec req-cicd-fleet-bot-paced), enforced here, not left to the caller:
#   - one repository per invocation;
#   - a --yes run refuses to start within COOLDOWN_MIN (30) minutes of the last --yes run on this
#     machine (stamp: ~/.cache/org-bots/run-as-maintainer.last), so looping it over the fleet
#     cannot become a burst; --dry-run is exempt, it writes nothing;
#   - a --yes run starts after a random 0-3 minute delay;
#   - a random 20-60 s gap between sign-off pushes.
# Inside one run, Renovate itself stays under tap's config limits (config:recommended: about two
# new PRs an hour).
#
# The tools run in disposable containers pinned by digest (the same images CI uses). Your token
# reaches them only as an environment variable; output is checked for it before printing.
set -euo pipefail
set +x
ORG="unified-systems-com"
RENOVATE_IMAGE="ghcr.io/renovatebot/renovate@sha256:e262ed52b23dd8c545c80faf5b1ad5c95a07b3f198888de8d7b8928f45c307c7"
NODE_IMAGE="node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c"
SUFFIX="[via maintainer-run]"

usage() { echo "usage: $0 <repo> renovate|release [--dry-run | --yes]" >&2; exit 2; }
repo_arg=""; tool=""; mode="dry-run"
for a in "$@"; do
  case "$a" in
    --dry-run) mode="dry-run" ;;
    --yes) mode="yes" ;;
    renovate|release) [[ -z "$tool" ]] || usage; tool="$a" ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $a" >&2; usage ;;
    *) [[ -z "$repo_arg" ]] || usage; repo_arg="$a" ;;
  esac
done
[[ -n "$repo_arg" && -n "$tool" ]] || usage
name="${repo_arg#"${ORG}/"}"
[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "not a repository name in ${ORG}: ${repo_arg}" >&2; exit 2; }
REPO="${ORG}/${name}"
for t in docker gh git jq; do command -v "$t" >/dev/null || { echo "missing: $t" >&2; exit 1; }; done
gh repo view "$REPO" --json name >/dev/null

ME_NAME="$(git config user.name || true)"; ME_EMAIL="$(git config user.email || true)"
[[ -n "$ME_NAME" && -n "$ME_EMAIL" ]] || { echo "set git config user.name and user.email" >&2; exit 1; }
if [[ "$mode" == "yes" ]]; then
  COOLDOWN_MIN=30
  stamp="${ORG_BOTS_STATE_DIR:-${HOME}/.cache/org-bots}/run-as-maintainer.last"
  mkdir -p "$(dirname "$stamp")"
  if [[ -f "$stamp" ]]; then
    last="$(cat "$stamp")"; now="$(date +%s)"
    if [[ "$last" =~ ^[0-9]+$ ]] && (( now - last < COOLDOWN_MIN * 60 )); then
      echo "the last --yes run was $(( (now - last) / 60 )) min ago; wait until ${COOLDOWN_MIN} min have passed (req-cicd-fleet-bot-paced)" >&2
      exit 3
    fi
  fi
  date +%s > "$stamp"
  delay=$(( RANDOM % 181 ))
  echo "starting in ${delay}s (random start, req-cicd-fleet-bot-paced)"
  sleep "$delay"
fi

TOKEN="$(gh auth token)"
[[ -n "$TOKEN" ]] || { echo "gh auth token returned nothing: run gh auth login" >&2; exit 1; }

scrub() { if [[ "$1" == *"$TOKEN"* ]]; then echo "(output withheld: it contained the token)" >&2; else printf '%s\n' "$1"; fi; }

run_renovate() {
  local dry=() preset_mount=() preset_dir require_config out
  # ${dry[@]+"${dry[@]}"}, not "${dry[@]}": macOS ships bash 3.2, which under set -u treats an
  # empty array as unbound, and --yes leaves dry empty.
  [[ "$mode" == "dry-run" ]] && dry=(-e RENOVATE_DRY_RUN=full)
  # renovate/global.js's own fleet-mode run already solves "a repository with no config of
  # its own": it spreads renovate/preset.js into every repository's config and sets
  # requireConfig=optional, globally -- safe there only because EVERY repository it processes
  # either gets that spread or is tap, which always carries its own config. This path never
  # wired either half in -- it hard-coded `required` with nothing mounted, so the one real
  # gap was a missing mount here, not a missing renovate.json5 in every plugin
  # (unified-systems-com/tap#999 corrected that premise; unified-systems-com/org-bots#22 is
  # this fix).
  #
  # tap is excluded from the mount, exactly as global.js excludes it from the spread: it
  # carries its own renovate.json5, read directly from the repository by Renovate's own
  # platform client -- mounting preset.js on top would register its boot-record custom
  # manager twice. `requireConfig` stays `required` for tap specifically (and for whatever
  # other repository name this script is handed) -- optional only where the mount actually
  # supplies a config, so a renamed or missing tap renovate.json5 still refuses loudly
  # instead of silently falling back to Renovate's built-in defaults -- flipping this
  # unconditionally would be a real, if narrow, fail-open regression for every repo besides
  # the fleet ones this fix targets.
  #
  # Known gap, accepted rather than chased: preset.js's postUpgradeTasks command
  # (python3 -I /github-action/boot-records/refresh.py) is mounted by the fleet Action, not
  # here. A manual run that bumps a boot-record pin will not get that digest refresh; the
  # PR's own `tap.git_pin --check` conformance gate catches the mismatch on push, so this
  # fails loud rather than silent -- narrower than fleet mode, not unsafe.
  require_config=required
  # Case-insensitive: GitHub resolves a repository name case-insensitively (the earlier
  # `gh repo view "$REPO"` check would succeed for "unified-systems-com/Tap" just as it does
  # for "unified-systems-com/tap"), but this comparison was a plain string match -- an
  # operator typo in case would have sent tap down the fleet-preset path, mounting
  # preset.js on top of its own renovate.json5 (the double-registration this exclusion
  # exists to prevent). `tr`, not `${VAR,,}`: the header above targets macOS's bash 3.2,
  # which has no case-conversion parameter expansion.
  if [[ "$(printf '%s' "$REPO" | tr '[:upper:]' '[:lower:]')" != "${ORG}/tap" ]]; then
    # set -e already aborts if the `cd` below fails (a script copied without its sibling
    # renovate/ directory); this check exists for the narrower case: `docker run -v
    # <missing-file>:<dest>:ro` does not fail the way a missing `cd` target does -- Docker
    # creates an empty directory at the mount point instead of erroring, which would hand
    # Renovate an unreadable "config file" rather than refuse. Fail loudly here instead of
    # finding that out from Renovate's own error, or worse, from `optional` silently
    # falling back to its built-in defaults.
    preset_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../renovate" && pwd)"
    [[ -f "${preset_dir}/preset.js" ]] || {
      echo "expected ${preset_dir}/preset.js to exist; refusing to flip RENOVATE_REQUIRE_CONFIG" \
        "to optional with no config actually mounted" >&2
      return 1
    }
    preset_mount=(-v "${preset_dir}/preset.js:/renovate-preset.js:ro" -e RENOVATE_CONFIG_FILE=/renovate-preset.js)
    require_config=optional
  fi
  out="$(RENOVATE_TOKEN="$TOKEN" docker run --rm \
      -e RENOVATE_TOKEN \
      -e RENOVATE_PLATFORM=github \
      -e RENOVATE_REPOSITORIES="$REPO" \
      -e RENOVATE_ONBOARDING=false \
      -e RENOVATE_REQUIRE_CONFIG="$require_config" \
      -e RENOVATE_GIT_AUTHOR="${ME_NAME} <${ME_EMAIL}>" \
      -e RENOVATE_COMMIT_MESSAGE_SUFFIX="$SUFFIX" \
      -e LOG_LEVEL=info \
      ${preset_mount[@]+"${preset_mount[@]}"} \
      ${dry[@]+"${dry[@]}"} "$RENOVATE_IMAGE" 2>&1)" || { scrub "$out"; return 1; }
  scrub "$out"
}

run_release() {
  local pin_dir out dry=()
  # ${dry[@]+"${dry[@]}"}, not "${dry[@]}": macOS ships bash 3.2, which under set -u treats an
  # empty array as unbound, and --yes leaves dry empty.
  pin_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../release-please" && pwd)"
  [[ "$mode" == "dry-run" ]] && dry=(--dry-run)
  out="$(RP_TOKEN="$TOKEN" REPO="$REPO" docker run --rm -e RP_TOKEN -e REPO -v "${pin_dir}:/pin:ro" "$NODE_IMAGE" sh -c '
      set -eu
      export NPM_CONFIG_UPDATE_NOTIFIER=false
      mkdir /work && cp /pin/package.json /pin/package-lock.json /work/ && cd /work
      npm ci --ignore-scripts --no-audit --no-fund --loglevel=error >/dev/null
      exec npx --no-install release-please release-pr \
        --token="$RP_TOKEN" --repo-url="$REPO" \
        --config-file=release-please-config.json \
        --manifest-file=.release-please-manifest.json "$@"
    ' sh ${dry[@]+"${dry[@]}"} 2>&1)" || { scrub "$out"; return 1; }
  scrub "$out"
}

# Amend each tool-written branch's single unsigned commit with your sign-off and an issue-link
# trailer. Skips anything that is not exactly one commit of yours over the default branch.
sign_off() {
  local prefix=$1 trailer=$2 default work b n head_email body
  default="$(gh api "repos/${REPO}" --jq .default_branch)"
  work="$(mktemp -d)"; trap 'rm -rf "$work"' RETURN
  git -c credential.helper= -c "credential.helper=!f() { echo username=x-access-token; echo \"password=\${GH_SIGN_TOKEN}\"; }; f" \
    clone --quiet --filter=blob:none "https://github.com/${REPO}.git" "$work/r"
  for b in $(git -C "$work/r" branch -r --format='%(refname:lstrip=3)' | grep -E "^${prefix}" || true); do
    [[ "$b" =~ ^[A-Za-z0-9._/-]+$ && "$b" != *..* ]] || { echo "skip ${b}: unusable name"; continue; }
    n="$(git -C "$work/r" rev-list --count --no-merges "origin/${default}..origin/${b}")"
    [[ "$n" == 1 ]] || { echo "skip ${b}: ${n} commits over ${default}, expected 1"; continue; }
    head_email="$(git -C "$work/r" log -1 --format=%ae "origin/${b}")"
    body="$(git -C "$work/r" log -1 --format=%B "origin/${b}")"
    [[ "$head_email" == "$ME_EMAIL" ]] || { echo "skip ${b}: last commit is not yours (${head_email})"; continue; }
    grep -q '^Signed-off-by:' <<< "$body" && { echo "ok ${b}: already signed"; continue; }
    git -C "$work/r" checkout --quiet -B "$b" "origin/${b}"
    old="$(git -C "$work/r" rev-parse HEAD)"
    git -C "$work/r" -c user.name="$ME_NAME" -c user.email="$ME_EMAIL" \
      commit --quiet --amend --no-edit -s --trailer "$trailer"
    GH_SIGN_TOKEN="$TOKEN" git -C "$work/r" -c credential.helper= \
      -c "credential.helper=!f() { echo username=x-access-token; echo \"password=\${GH_SIGN_TOKEN}\"; }; f" \
      push --quiet --force-with-lease="${b}:${old}" origin "HEAD:refs/heads/${b}"
    echo "signed ${b}"
    sleep $(( 20 + RANDOM % 41 ))
  done
}

case "$tool" in
  renovate) run_renovate ;;
  release)  run_release ;;
esac

if [[ "$mode" == "yes" ]]; then
  export GH_SIGN_TOKEN="$TOKEN"
  case "$tool" in
    renovate) sign_off "renovate/" "No-issue: dependency update (Renovate, run by hand)" ;;
    release)  sign_off "release-please--" "No-issue: release PR (release-please, run by hand)" ;;
  esac
  echo "Open PRs on ${REPO}:"
  gh pr list -R "$REPO" --author "@me" --state open --json number,title --jq '.[] | "  #\(.number) \(.title)"'
fi
