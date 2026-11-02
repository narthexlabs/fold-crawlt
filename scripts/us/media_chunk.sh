#!/usr/bin/env bash
# YouTube media catalog, one chunk (GitHub Actions job `media` in narthexlabs/fold-crawlt; also runs locally).
# ONE job only: one host (youtube.com), one request every 2 s, robots.txt honoured (/feeds/videos.xml and /results are
# disallowed and never requested), honest user agent. A consent / "confirm you're not a bot" page stops the run and is
# committed as _blocked.json (never worked around). Resumable: the YouTube page cache (data/raw/**/youtube/cache) and
# resolved.json are kept between runs by actions/cache, so a second dispatch continues where the deadline stopped.
#
#   bash scripts/us/media_chunk.sh 110            # minutes
# Order: California (11 dioceses, Oakland excluded: its feed is data/real/media/) first, then national (ingest/us/media/).
set -u
MIN=${1:-110}
cd "$(dirname "$0")/../.."
TRAILER=$'\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5'
export MEDIA_DEADLINE=$(( $(date +%s) + MIN * 60 ))
export PYTHONDONTWRITEBYTECODE=1

push() {
  for i in 1 2 3 4 5; do
    git pull --rebase -X theirs --autostash -q origin main || { git rebase --abort 2>/dev/null || true; }
    git push -q origin HEAD:main && return 0
    sleep $((RANDOM % 20 + 5))
  done
}

# ---------------------------------------------------------------- California
CA_RAW=data/raw/california/youtube
CA_OUT=data/real/california/media
mkdir -p "$CA_RAW"
rm -f "$CA_RAW/blocked.json"
cp "$CA_OUT/_inputs/candidates.json" "$CA_OUT/_inputs/parish_index.json" "$CA_RAW/"
echo "== California: resolve links -> channels"
python3 -I -u ingest/california/media_resolve.py fetch || true
if [ -f "$CA_RAW/blocked.json" ]; then
  cp "$CA_RAW/blocked.json" "$CA_OUT/_blocked.json"
  git add "$CA_OUT/_blocked.json" && git commit -q -m "YouTube catalog: stopped, YouTube served a consent / bot page (California resolve)$TRAILER"
  push; exit 0
fi
if [ "$(date +%s)" -ge "$MEDIA_DEADLINE" ]; then echo "deadline reached during resolve; next run continues"; exit 0; fi
echo "== California: ownership check"
python3 -I -u ingest/california/media_resolve.py verify
echo "== California: /videos and /streams tabs, privacy screen, heuristic ranking"
python3 -I -u ingest/california/media_feed.py || true
if [ -f "$CA_RAW/blocked.json" ]; then cp "$CA_RAW/blocked.json" "$CA_OUT/_blocked.json"; fi
git add "$CA_OUT" && git commit -q -m "YouTube catalog: California dioceses (Actions $(date -u +%FT%H:%MZ))$TRAILER" || true
push
if [ -f "$CA_RAW/blocked.json" ]; then exit 0; fi

# ---------------------------------------------------------------- national (when the code is present)
if [ -f ingest/us/media/run.py ]; then
  echo "== National (every diocese outside California)"
  mkdir -p data/raw/us/youtube data/real/us/media
  rm -f data/raw/us/youtube/blocked.json
  python3 -I -u ingest/us/media/run.py all || true
  if [ -f data/raw/us/youtube/blocked.json ]; then cp data/raw/us/youtube/blocked.json data/real/us/media/_blocked.json; fi
  git add data/real/us/media && git commit -q -m "YouTube catalog: national (Actions $(date -u +%FT%H:%MZ))$TRAILER" || true
  push
fi
