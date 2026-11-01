#!/usr/bin/env bash
# One-shot chunk for a fresh session: pull, crawl ~100 minutes of DiscoverMass church pages (non-California states, in
# the order TX NY IL FL PA NJ MA OH MI WI MN then by count), publish, commit, push. Safe to run repeatedly.
# Resumes from the published files (data/real/us/discovermass/<state>.json), so no data/raw cache is needed.
# One crawler per machine (flock); a second start exits. Usage: scripts/us/discovermass_chunk.sh [minutes] [repo]
set -euo pipefail
MIN="${1:-100}"
REPO="${2:-/home/claude/fold-app}"
cd "$REPO"
git pull --rebase --autostash origin main
rc=0
python3 -I ingest/us/discovermass_crawl.py crawl --all --minutes "$MIN" || rc=$?
# rc 2 = time budget reached (normal); 3 = blocked/unreachable (publish what we have, then stop); other = crash
python3 -I ingest/us/discovermass_crawl.py publish --all
git add data/real/us/discovermass
if git diff --cached --quiet; then echo "nothing new to commit"; [ "$rc" = 2 ] && exit 0   # 2 = time budget reached: a normal end
exit "$rc"; fi
n=$(python3 -c "import json;print(json.load(open('data/real/us/discovermass/progress.json'))['totals']['records'])")
git commit -m "DiscoverMass national address book: ${n} church pages so far (secondary source)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5"
git pull --rebase --autostash origin main
git push origin HEAD:main || { git pull --rebase --autostash origin main && git push origin HEAD:main; }
[ "$rc" = 2 ] && exit 0   # 2 = time budget reached: a normal end
exit "$rc"
