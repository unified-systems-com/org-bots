#!/usr/bin/env bash
# Cut a release: release-please stage 2 (`github-release`), run from the maintainer's machine.
#
#   scripts/cut-release.sh <repo> [--dry-run | --yes]
#
# <repo> is a repository name in unified-systems-com (or unified-systems-com/<name>).
# Default is --dry-run: print what would be tagged and change nothing. --yes cuts it.
#
# Why here and not in CI: creating a tag and a GitHub Release needs `contents: write` on the
# target repository, and org-bots holds no write credential. This uses YOUR `gh auth token`,
# so the release is cut by you, after you merged the release PR.
#
# What --yes does, in order:
#   1. If the merged release PR lacks the `autorelease: pending` label (the fork bot cannot
#      apply labels: that needs triage access), add it with your token. release-please finds
#      the merged release PR by that label and nothing else.
#   2. Dry-run, and stop unless it would tag exactly the version in the repository's
#      .release-please-manifest.json on its default branch.
#   3. Run `release-please github-release`: tag the merge commit, publish the Release,
#      comment on the PR, and relabel it `autorelease: tagged`.
#   4. Confirm the tag exists, and list the target's release-sbom runs, which the tag push
#      starts.
#
# The CLI runs in a disposable container (--rm) from the same lockfile CI uses
# (release-please/package-lock.json, mounted read-only), so its whole dependency closure is
# pinned by integrity hash. The token reaches the container only as an environment variable;
# it is never written to disk and never printed.
set -euo pipefail
set +x

ORG="unified-systems-com"
# node:22-slim, pinned by digest: the container holds a write token. Resolved 2026-09-27.
IMAGE="node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c"
PENDING="autorelease: pending"
TAGGED="autorelease: tagged"

usage() { echo "usage: $0 <repo> [--dry-run | --yes]" >&2; exit 2; }

repo_arg="" ; mode="dry-run"
for a in "$@"; do
  case "$a" in
    --dry-run) mode="dry-run" ;;
    --yes) mode="yes" ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $a" >&2; usage ;;
    *) [[ -z "$repo_arg" ]] || usage; repo_arg="$a" ;;
  esac
done
[[ -n "$repo_arg" ]] || usage
name="${repo_arg#"${ORG}/"}"
if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "not a repository name in ${ORG}: ${repo_arg}" >&2; exit 2
fi
REPO="${ORG}/${name}"

for tool in docker gh jq; do
  command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
PIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../release-please" && pwd)"
[[ -f "$PIN_DIR/package-lock.json" ]] || { echo "missing $PIN_DIR/package-lock.json" >&2; exit 1; }

gh repo view "$REPO" --json name >/dev/null   # fails loudly on a typo or no access

RP_TOKEN="$(gh auth token)"
[[ -n "$RP_TOKEN" ]] || { echo "gh auth token returned nothing: run gh auth login" >&2; exit 1; }
export RP_TOKEN REPO

# Run the pinned release-please CLI's github-release against $REPO; extra args pass through.
# Output is captured, checked for the token, and only then printed.
rp() {
  local out
  out="$(docker run --rm -e RP_TOKEN -e REPO -v "${PIN_DIR}:/pin:ro" "$IMAGE" sh -c '
      set -eu
      export NPM_CONFIG_UPDATE_NOTIFIER=false
      mkdir /work && cp /pin/package.json /pin/package-lock.json /work/ && cd /work
      npm ci --ignore-scripts --no-audit --no-fund --loglevel=error >/dev/null
      exec npx --no-install release-please github-release \
        --token="$RP_TOKEN" --repo-url="$REPO" \
        --config-file=release-please-config.json \
        --manifest-file=.release-please-manifest.json "$@"
    ' sh "$@" 2>&1)" || { scrub "$out"; return 1; }
  scrub "$out"
}
scrub() {
  if [[ "$1" == *"$RP_TOKEN"* ]]; then
    echo "(output withheld: it contained the token)" >&2
  else
    printf '%s\n' "$1"
  fi
}

echo "== ${REPO} (${mode})"

# 1. The merged release PR, if it is not labelled. More than one is ambiguous: stop.
unlabelled="$(gh pr list -R "$REPO" --state merged --limit 50 \
  --json number,title,headRefName,labels \
  --jq "[.[] | select(.headRefName | startswith(\"release-please--\"))
             | select(any(.labels[]; .name == \"${PENDING}\" or .name == \"${TAGGED}\") | not)]")"
count="$(jq 'length' <<< "$unlabelled")"
if (( count > 1 )); then
  echo "more than one merged, unlabelled release PR; label the right one '${PENDING}' by hand:" >&2
  jq -r '.[] | "  #\(.number) \(.title)"' <<< "$unlabelled" >&2
  exit 1
fi
label_pr=""
if (( count == 1 )); then
  label_pr="$(jq -r '.[0].number' <<< "$unlabelled")"
  echo "merged release PR #${label_pr} ($(jq -r '.[0].title' <<< "$unlabelled")) has no '${PENDING}' label"
fi

# The version a correct release tags: the manifest's root package on the default branch.
expected=""
if manifest="$(gh api "repos/${REPO}/contents/.release-please-manifest.json" --jq .content 2>/dev/null)"; then
  version="$(base64 --decode <<< "$manifest" | jq -r '."." // empty')"
  [[ -n "$version" ]] && expected="v${version}"
fi
echo "manifest on the default branch: ${expected:-"(no root package; tag check skipped)"}"

if [[ "$mode" == "dry-run" ]]; then
  [[ -z "$label_pr" ]] || echo "would add '${PENDING}' to #${label_pr} first; until then the dry run below cannot see that PR"
  rp --dry-run
  echo "dry run only: nothing changed. Re-run with --yes to cut it."
  exit 0
fi

# 2. Label, then dry-run and check the tag before anything is created.
if [[ -n "$label_pr" ]]; then
  echo "adding '${PENDING}' to #${label_pr} with your token"
  gh pr edit "$label_pr" -R "$REPO" --add-label "$PENDING" >/dev/null
fi
plan="$(rp --dry-run)"
printf '%s\n' "$plan"
tags=()
while IFS= read -r t; do [[ -n "$t" ]] && tags+=("$t"); done \
  < <(grep -oE "^  tag: '[^']+'" <<< "$plan" | sed -E "s/tag: '(.*)'/\1/")
prs="$(grep -oE "^  pullNumber: [0-9]+" <<< "$plan" | awk '{print $2}' | sort -u)"
if (( ${#tags[@]} == 0 )); then
  echo "nothing to release."
  exit 0
fi
if [[ -n "$expected" && ! " ${tags[*]} " == *" ${expected} "* ]]; then
  echo "refusing: would tag ${tags[*]}, but the manifest says ${expected}" >&2
  exit 1
fi

# 3. Cut it.
rp

# 4. Verify.
ok=1
for t in "${tags[@]}"; do
  if gh api "repos/${REPO}/git/ref/tags/${t}" --jq '"tag \(.ref) -> \(.object.sha)"'; then :; else
    echo "MISSING: tag ${t}" >&2; ok=0
  fi
done
for n in $prs; do
  labels="$(gh pr view "$n" -R "$REPO" --json labels --jq '[.labels[].name] | join(", ")')"
  echo "#${n} labels: ${labels}"
  [[ "$labels" == *"$TAGGED"* ]] || { echo "#${n} was not relabelled '${TAGGED}'" >&2; ok=0; }
done
gh release list -R "$REPO" --limit 3
echo "release-sbom runs (the tag push starts one; it can take a minute to appear):"
gh run list -R "$REPO" --workflow release-sbom.yml --limit 3 || true
(( ok )) || exit 1
