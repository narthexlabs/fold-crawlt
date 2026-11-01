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
  git pull --rebase --autostash -q origin main || { git rebase --abort 2>/dev/null; return 1; }
  git push -q origin HEAD:main || { git pull --rebase --autostash -q origin main && git push -q origin HEAD:main; }
}

git pull --rebase --autostash -q origin main
python3 -I ingest/us/dioceses/directory_probe.py --minutes 20
probe_rc=$?
git add data/real/us/dioceses/
git commit -q -m "US diocese directory probe: $(date -u +%FT%H:%MZ)${TRAILER}" 2>/dev/null

python3 -I ingest/us/sites/run_state.py chunk --minutes "$MIN" --workers "${WORKERS:-128}" ${STATES:+--state "$STATES"}
rc=$?
if [ -f data/raw/us/sites/_last_touched.json ]; then
  for d in $(python3 -c "import json;print(' '.join(json.load(open('data/raw/us/sites/_last_touched.json'))))"); do
    git add "data/real/us/sites/$d/"
    git commit -q -m "US parish sites: $d$TRAILER" 2>/dev/null
  done
fi
git add data/real/us/sites/progress.json data/real/us/sites/README.md
git commit -q -m "US parish sites: progress $(date -u +%FT%H:%MZ)${TRAILER}" 2>/dev/null
push
echo "probe rc=$probe_rc chunk rc=$rc"
exit $rc
