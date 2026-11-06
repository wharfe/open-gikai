#!/bin/bash
# Vercel Ignored Build Step for the open-gikai project (#101). Wired by
# vercel.json as `bash scripts/vercel-ignore-build.sh || exit 1`.
#
# Contract: exit 0 = SKIP this build, exit 1 = BUILD. 0 is returned from one
# place only, after proving that every path changed since the last successful
# deployment (VERCEL_GIT_PREVIOUS_SHA) lies under data/pending-batches/ — the
# Message Batches sidecars that summarize.py commits mid-run. Nothing the site
# build reads lives there (tests/test_vercel_ignore_build.py fences that), so
# those pushes only added a full deployment to the storage quota.
#
# Anything this script cannot establish is a BUILD: no previous sha, a sha the
# shallow clone does not hold, a failed git, an empty diff, a non-production
# environment. A wrong skip leaves the site stale with nothing pointing back
# here; a wrong build only costs storage.
#
# Compared against the last successful deployment, not HEAD^: a threads commit
# whose build failed followed by a sidecar-only commit must still build.
#
# Recovery: to rebuild a deployment this skipped (Ignored / Canceled), use
# Redeploy in the dashboard and UNTICK "Use project's Ignore Build Step". A
# redeploy of the same sha builds anyway (empty diff), but a Canceled sha whose
# only difference from the older READY one is sidecars would be skipped again.
#
# No `set -e`: every command whose result decides the outcome is checked by
# hand. No EXIT trap: a trap could overwrite the exit status.

set -u

PREFIX="data/pending-batches/"
tmp=""

decide() { # decide <0|1> <reason> [detail]
  local code=$1 reason=$2 detail=${3:-}
  local verdict=build
  [ "$code" -eq 0 ] && verdict=skip
  echo "vercel-ignore: ${verdict} reason=${reason}${detail:+ ${detail}}"
  [ -n "$tmp" ] && rm -f -- "$tmp" 2>/dev/null
  exit "$code"
}

if [ "${VERCEL_ENV:-}" != "production" ]; then
  decide 1 not_production "env=${VERCEL_ENV:-empty}"
fi

if ! top=$(git rev-parse --show-toplevel 2>/dev/null) || [ -z "$top" ]; then
  decide 1 not_a_repo
fi
cd "$top" || decide 1 not_a_repo

prev=${VERCEL_GIT_PREVIOUS_SHA:-}
[ -n "$prev" ] || decide 1 previous_missing
[[ "$prev" =~ ^[0-9a-fA-F]{7,64}$ ]] || decide 1 bad_sha

prev_full=$(git rev-parse --verify --quiet "${prev}^{commit}") && [ -n "$prev_full" ] \
  || decide 1 previous_unavailable
head_full=$(git rev-parse --verify --quiet "HEAD^{commit}") && [ -n "$head_full" ] \
  || decide 1 head_unavailable
# The diff must be about the commit Vercel is deploying. If it says which one
# and the checkout disagrees, do not judge a different tree.
want=${VERCEL_GIT_COMMIT_SHA:-}
if [ -n "$want" ] && [ "$want" != "$head_full" ]; then
  decide 1 head_mismatch
fi

tmp=$(mktemp) || decide 1 git_diff_failed "mktemp"
git diff --no-ext-diff --no-textconv --no-renames --name-only -z \
  "$prev_full" "$head_full" -- > "$tmp"
diff_rc=$?
[ "$diff_rc" -eq 0 ] || decide 1 git_diff_failed "rc=${diff_rc}"

count=0
outside=()
# `|| [ -n "$p" ]` keeps a final record that lacks its NUL terminator: `read`
# reports EOF for it, and dropping it could hide a non-sidecar path.
while IFS= read -r -d '' p || [ -n "$p" ]; do
  count=$((count + 1))
  # `?*` also rejects an empty record and the bare directory name.
  if [[ "$p" != "$PREFIX"?* ]]; then
    outside+=("$p")
  fi
done < "$tmp" || decide 1 git_diff_failed "open_failed"
# Only a failure to OPEN the file is caught above: the loop's status is its
# body's, so a read error midway would look like EOF. Accepted (spec): this is
# a local file written a moment ago, and no test could make that read fail.

[ "$count" -gt 0 ] || decide 1 no_changes
if [ "${#outside[@]}" -gt 0 ]; then
  decide 1 outside_pending_batches "paths=$(printf '%q,' "${outside[@]:0:5}")"
fi

decide 0 pending_batches_only "paths=${count} prev=${prev_full:0:12}"
