#!/usr/bin/env bash
# One 100-minute chunk of the national parish-website crawl (lane G4), from a clean clone of narthexlabs/fold-app.
#   bash scripts/us/sites_chunk.sh [minutes]        (default 100; run it in the background with a 2-hour timeout)
# Pull -> directory probe (only sees not yet probed) -> crawl + parse the next unfinished dioceses (by DiscoverMass church
# count per state, skipping domains already in data/real/us/sites/) -> commit one diocese per commit -> push.
# Exit 3: this machine's network refuses ordinary websites (sandbox egress closed); nothing is recorded about any site.
set -u
cd "$(git rev-parse --show-toplevel)"
MIN="${1:-100}"
# One chunk per clone at a time: a second one would send parallel requests to the same hosts.
exec 9>"$(git rev-parse --git-dir)/sites_chunk.lock"
if ! flock -n 9; then echo "$(date -u +%FT%TZ) another sites_chunk is running in this clone; exiting"; exit 0; fi
TRAILER=$'\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5'

push() {
  # 14 matrix runners push at once: rebase with -X theirs (our fresh records win for our own dioceses), retry with jitter.
  # 15 tries, 10-90 s apart (2026-11-01: 4 tries 5-25 s apart lost pushes to "cannot lock ref" when 14 jobs ended together).
  for i in $(seq 1 15); do
    git pull --rebase -X theirs --autostash -q origin main || { git rebase --abort 2>/dev/null || true; }
    git push -q origin HEAD:main && return 0
    echo "push $i rejected; retrying"
    sleep $(( 10 + RANDOM % 81 ))
  done
  return 1
}

git pull --rebase --autostash -q origin main
probe_rc=0
if [ "${SKIP_PROBE:-0}" != 1 ]; then   # in a matrix only one job probes; otherwise every job commits directory_status.json and they conflict
  python3 -I ingest/us/dioceses/directory_probe.py --minutes 20
  probe_rc=$?
  git add data/real/us/dioceses/
  git commit -q -m "US diocese directory probe: $(date -u +%FT%H:%MZ)${TRAILER}" 2>/dev/null
fi

# Top-up (2026-11-01): after the chunk, fetch only the confession / adoration / liturgy pages of sites already crawled ok
# (ingest/us/sites/run_state.py topup). Its minutes come out of the chunk's, so the job's total time is unchanged.
# TOPUP_MINUTES=0 turns it off.
TOPUP_MIN="${TOPUP_MINUTES:-25}"
CHUNK_MIN=$(( MIN > TOPUP_MIN + 20 ? MIN - TOPUP_MIN : MIN ))
python3 -I ingest/us/sites/run_state.py chunk --minutes "$CHUNK_MIN" --workers "${WORKERS:-128}" ${STATES:+--state "$STATES"}
rc=$?
if [ -f data/raw/us/sites/_last_touched.json ]; then
  for d in $(python3 -c "import json;print(' '.join(json.load(open('data/raw/us/sites/_last_touched.json'))))"); do
    git add "data/real/us/sites/$d/"
    git commit -q -m "US parish sites: $d$TRAILER" 2>/dev/null
  done
fi
if [ "$TOPUP_MIN" -gt 0 ] && [ "$rc" -ne 3 ]; then
  rm -f data/raw/us/sites/_last_touched.json
  timeout $(( TOPUP_MIN * 60 + 300 )) python3 -I ingest/us/sites/run_state.py topup --kinds confession,adoration,liturgy \
    --minutes "$TOPUP_MIN" --workers "${WORKERS:-128}" ${STATES:+--state "$STATES"}
  echo "topup rc=$?"
  if [ -f data/raw/us/sites/_last_touched.json ]; then
    for d in $(python3 -c "import json;print(' '.join(json.load(open('data/raw/us/sites/_last_touched.json'))))"); do
      git add "data/real/us/sites/$d/"
      git commit -q -m "US parish sites: confession / adoration / liturgy pages top-up, $d$TRAILER" 2>/dev/null
    done
  fi
fi
git add data/real/us/sites/progress.json data/real/us/sites/README.md
git commit -q -m "US parish sites: progress $(date -u +%FT%H:%MZ)${TRAILER}" 2>/dev/null
push || echo "PUSH FAILED after 15 tries: this chunk's commits did not reach origin"
echo "probe rc=$probe_rc chunk rc=$rc"
exit $rc
