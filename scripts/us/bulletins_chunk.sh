#!/usr/bin/env bash
# One chunk of the national bulletin-groups run (lane G9), from a clean clone of narthexlabs/fold-app.
#   bash scripts/us/bulletins_chunk.sh [minutes]       (default 100; run in the background with a 2-hour timeout)
# Pull -> fetch bulletins + extract groups for sites not yet done (times-but-no-groups first) -> one commit per diocese -> push.
# Exit 3: egress closed (nothing recorded about any site).
set -u
cd "$(git rev-parse --show-toplevel)"
MIN="${1:-100}"
TRAILER=$'\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5'
push() {
  git pull --rebase --autostash -q origin main || { git rebase --abort 2>/dev/null; return 1; }
  git push -q origin HEAD:main || { git pull --rebase --autostash -q origin main && git push -q origin HEAD:main; }
}
exec 9>/tmp/bulletins_chunk.lock; flock -n 9 || { echo "another bulletins chunk is running"; exit 4; }
git pull --rebase --autostash -q origin main
python3 -I ingest/us/bulletins/run.py --minutes "$MIN"
rc=$?
if [ -f data/raw/us/bulletins/_last_touched.json ]; then
  for d in $(python3 -c "import json;print(' '.join(json.load(open('data/raw/us/bulletins/_last_touched.json'))))"); do
    git add "data/real/us/bulletins/$d/"
    git commit -q -m "US parish bulletins: groups for $d$TRAILER" 2>/dev/null
  done
fi
git add data/real/us/bulletins/README.md
git commit -q -m "US parish bulletins: README measures $(date -u +%FT%H:%MZ)${TRAILER}" 2>/dev/null
push
echo "chunk rc=$rc"
exit $rc
