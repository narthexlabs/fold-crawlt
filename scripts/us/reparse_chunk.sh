#!/usr/bin/env bash
# Re-parse one state group's cached parish sites with the current parser and publish them, one commit per diocese.
#   STATES='NY' [RESET=1] bash scripts/us/reparse_chunk.sh [minutes]      (runs in the sites matrix job, same raw cache)
#
# A diocese is stamped as re-parsed (data/raw/us/sites/_reparsed.json, which lives in the runner's actions/cache) ONLY
# after its commit is confirmed on origin/main. 2026-11-01: the stamp used to be written as soon as a diocese was parsed;
# 14 jobs pushing at once were rejected ("cannot lock ref", 4 tries), the stamp was saved with the cache anyway, and every
# later re-parse printed "0 dioceses re-parsed, 0 left" while only 1,988 of 11,230 ok records carried the new fields.
# RESET=1 deletes the stamp first (re-parse the whole group again). Work is pushed every <=20 minutes, so a cut job keeps
# what it published and resumes from the stamp.
set -u
cd "$(git rev-parse --show-toplevel)"
RMIN="${1:-60}"
: "${STATES:?set STATES to the matrix group, e.g. STATES=NY}"
RAWD=data/raw/us/sites
TRAILER=$'\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5'

push_confirmed() {
  # Up to 15 tries: rebase on origin (-X theirs: our fresh records win for our own dioceses), push, jittered 10-90 s backoff.
  # Returns 0 only when origin/main is HEAD or contains it.
  local i remote
  for i in $(seq 1 15); do
    git pull --rebase -X theirs --autostash -q origin main || { git rebase --abort 2>/dev/null || true; }
    if git push -q origin HEAD:main; then
      remote=$(git ls-remote origin refs/heads/main | cut -f1)
      if [ "$remote" = "$(git rev-parse HEAD)" ]; then return 0; fi
      git fetch -q origin main && git merge-base --is-ancestor HEAD FETCH_HEAD && return 0
      echo "push $i: accepted but HEAD not found on origin/main; retrying"
    else
      echo "push $i: rejected; retrying"
    fi
    sleep $(( 10 + RANDOM % 81 ))
  done
  return 1
}

if [ "${RESET:-0}" = "1" ]; then
  rm -f "$RAWD/_reparsed.json" && echo "reset: deleted $RAWD/_reparsed.json (whole group is re-parsed)"
fi
git rebase --abort 2>/dev/null || true
git pull --rebase -X theirs --autostash -q origin main || { git rebase --abort 2>/dev/null || true; }

END=$(( $(date +%s) + RMIN * 60 ))
SKIP=""; PENDING=""; TOT_D=0; TOT_R=0; TOT_C=0; TOT_S=0; LEFT="?"
publish() {
  # commit what is staged per diocese already; push; stamp the dioceses whose records are committed and on origin/main
  local ok="" keep="" d
  [ -n "$PENDING" ] || return 0
  if push_confirmed; then
    for d in $PENDING; do
      if [ -z "$(git status --porcelain -- "data/real/us/sites/$d/")" ]; then ok="$ok,$d"; TOT_S=$((TOT_S + 1)); else keep="$keep $d"; fi
    done
    [ -n "$ok" ] && python3 -I ingest/us/sites/run_state.py stamp --diocese "${ok#,}"
    PENDING="$keep"
  else
    echo "push failed 15 times: $(echo $PENDING | wc -w) dioceses stay unstamped (re-parsed again next run)"
  fi
}

while :; do
  LEFTMIN=$(( (END - $(date +%s)) / 60 ))
  [ "$LEFTMIN" -ge 1 ] || break
  CH=$(( LEFTMIN < 20 ? LEFTMIN : 20 ))
  rm -f "$RAWD/_last_touched.json" "$RAWD/_reparse_stats.json"
  timeout $(( CH * 60 + 900 )) python3 -I ingest/us/sites/run_state.py reparse --state "$STATES" --minutes "$CH" --skip "${SKIP#,}" || true
  [ -f "$RAWD/_reparse_stats.json" ] || { echo "re-parse wrote no stats (cut or failed); stopping"; break; }
  DONE=$(python3 -c "import json;print(' '.join(json.load(open('$RAWD/_last_touched.json'))))")
  read -r R C LEFT <<< "$(python3 -c "import json;s=json.load(open('$RAWD/_reparse_stats.json'));print(s['records'],s['changed'],len(s['left']))")"
  TOT_R=$((TOT_R + R)); TOT_C=$((TOT_C + C))
  for d in $DONE; do
    if [ -d "data/real/us/sites/$d" ]; then
      git add -- "data/real/us/sites/$d/" && git commit -q -m "US parish sites: re-parse $d (current parser, runner cache)$TRAILER" 2>/dev/null
    fi
    PENDING="$PENDING $d"; SKIP="$SKIP,$d"; TOT_D=$((TOT_D + 1))
  done
  git add data/real/us/sites/progress.json data/real/us/sites/README.md
  git commit -q -m "US parish sites: re-parse progress $(date -u +%FT%H:%MZ)$TRAILER" 2>/dev/null || true
  publish
  [ -n "$DONE" ] || break
  [ "$LEFT" = "0" ] && break
done
publish

LINE="REPARSE SUMMARY states=$STATES dioceses_processed=$TOT_D records_reparsed=$TOT_R records_changed=$TOT_C stamped_on_origin=$TOT_S unconfirmed=$(echo $PENDING | wc -w) dioceses_left=$LEFT"
echo "$LINE"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "$LINE" >> "$GITHUB_STEP_SUMMARY"
exit 0
