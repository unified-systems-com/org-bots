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
# Pacing (tap spec req-cicd-fleet-bot-paced): one repository per invocation, and a random
# 20-60 s gap between sign-off pushes. Never loop this over the fleet in a tight loop.
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
TOKEN="$(gh auth token)"
[[ -n "$TOKEN" ]] || { echo "gh auth token returned nothing: run gh auth login" >&2; exit 1; }

scrub() { if [[ "$1" == *"$TOKEN"* ]]; then echo "(output withheld: it contained the token)" >&2; else printf '%s\n' "$1"; fi; }

run_renovate() {
  local dry=() out
  [[ "$mode" == "dry-run" ]] && dry=(-e RENOVATE_DRY_RUN=full)
  out="$(RENOVATE_TOKEN="$TOKEN" docker run --rm \
      -e RENOVATE_TOKEN \
      -e RENOVATE_PLATFORM=github \
      -e RENOVATE_REPOSITORIES="$REPO" \
      -e RENOVATE_ONBOARDING=false \
      -e RENOVATE_REQUIRE_CONFIG=required \
      -e RENOVATE_GIT_AUTHOR="${ME_NAME} <${ME_EMAIL}>" \
      -e RENOVATE_COMMIT_MESSAGE_SUFFIX="$SUFFIX" \
      -e LOG_LEVEL=info \
      "${dry[@]}" "$RENOVATE_IMAGE" 2>&1)" || { scrub "$out"; return 1; }
  scrub "$out"
}

run_release() {
  local pin_dir out dry=()
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
    ' sh "${dry[@]}" 2>&1)" || { scrub "$out"; return 1; }
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
