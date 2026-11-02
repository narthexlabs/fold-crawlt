#!/usr/bin/env bash
# YouTube media catalog, one chunk (GitHub Actions job `media` in narthexlabs/fold-crawlt; also runs locally).
# ONE job only: one host (youtube.com), one request every 2 s, robots.txt honoured (/feeds/videos.xml and /results are
# disallowed and never requested), honest user agent. A consent / "confirm you're not a bot" page stops the run and is
# committed as _blocked.json (never worked around). Resumable: the YouTube page cache (data/raw/**/youtube/cache) and
# resolved.json are kept between runs by actions/cache, so a second dispatch continues where the deadline stopped.
#
#   bash scripts/us/media_chunk.sh 110            # minutes
# Order: California (11 dioceses, Oakland excluded: its feed is data/real/media/) first, then national (ingest/us/media/).
#
# Saving is loud (2026-11-02): every save prints file count, size and the largest files, refuses any file over 50 MB,
# commits, pushes, and then checks that the commit is on origin/main; if it is not after 6 tries the step FAILS.
set -u
MIN=${1:-110}
cd "$(dirname "$0")/../.."
TRAILER=$'\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5'
export MEDIA_DEADLINE=$(( $(date +%s) + MIN * 60 ))
export PYTHONDONTWRITEBYTECODE=1

save() {  # save PATH MESSAGE
  local path="$1" msg="$2" big
  if [ ! -e "$path" ]; then echo "save: $path does not exist (nothing written)"; return 0; fi
  echo "== save $path: $(find "$path" -type f | wc -l) files, $(du -sh "$path" | cut -f1); largest:"
  find "$path" -type f -printf '   %s bytes  %p\n' | sort -k1 -n | tail -3
  big=$(find "$path" -type f -size +50M)
  if [ -n "$big" ]; then echo "::error::file(s) over 50 MB, not committed: $big"; exit 1; fi
  git add "$path"
  if git diff --cached --quiet -- "$path"; then echo "save: nothing changed under $path"; return 0; fi
  git diff --cached --shortstat
  git commit -q -m "$msg$TRAILER" || { echo "::error::git commit failed for $path"; exit 1; }
  for i in 1 2 3 4 5 6; do
    if ! git pull --rebase -X theirs --autostash origin main; then git rebase --abort 2>/dev/null || true; sleep 10; continue; fi
    if git push origin HEAD:main; then
      git fetch -q origin main
      if git merge-base --is-ancestor HEAD origin/main; then echo "save: $(git rev-parse --short HEAD) is on origin/main ($msg)"; return 0; fi
      echo "save: push reported success but HEAD is not on origin/main; retrying"
    fi
    sleep $((RANDOM % 20 + 5))
  done
  echo "::error::'$msg' did not reach origin/main after 6 tries"; exit 1
}

# ---------------------------------------------------------------- California
CA_RAW=data/raw/california/youtube
CA_OUT=data/real/california/media
mkdir -p "$CA_RAW"
rm -f "$CA_RAW/blocked.json"
cp "$CA_OUT/_inputs/candidates.json" "$CA_OUT/_inputs/parish_index.json" "$CA_RAW/"
echo "== California: resolve links -> channels"
python3 -I -u ingest/california/media_resolve.py fetch || echo "::warning::California resolve exited non-zero"
if [ -f "$CA_RAW/blocked.json" ]; then
  cp "$CA_RAW/blocked.json" "$CA_OUT/_blocked.json"
  save "$CA_OUT/_blocked.json" "YouTube catalog: stopped, YouTube served a consent / bot page (California resolve)"
  exit 0
fi
if [ "$(date +%s)" -ge "$MEDIA_DEADLINE" ]; then echo "deadline reached during resolve; next run continues"; exit 0; fi
echo "== California: ownership check"
python3 -I -u ingest/california/media_resolve.py verify || { echo "::error::California verify failed"; exit 1; }
echo "== California: /videos and /streams tabs, privacy screen, heuristic ranking"
python3 -I -u ingest/california/media_feed.py || echo "::warning::California feed exited non-zero"
if [ -f "$CA_RAW/blocked.json" ]; then cp "$CA_RAW/blocked.json" "$CA_OUT/_blocked.json"; fi
save "$CA_OUT" "YouTube catalog: California dioceses (Actions $(date -u +%FT%H:%MZ))"
if [ -f "$CA_RAW/blocked.json" ]; then exit 0; fi

# ---------------------------------------------------------------- national (every diocese outside California)
echo "== National"
mkdir -p data/raw/us/youtube data/real/us/media
rm -f data/raw/us/youtube/blocked.json
python3 -I -u ingest/us/media/run.py all || echo "::warning::national run exited non-zero"
if [ -f data/raw/us/youtube/blocked.json ]; then cp data/raw/us/youtube/blocked.json data/real/us/media/_blocked.json; fi
save data/real/us/media "YouTube catalog: national (Actions $(date -u +%FT%H:%MZ))"
python3 - <<'EOF'
import glob, json
fs = sorted(glob.glob("data/real/us/media/dioceses/*.json"))
v = sum(json.load(open(f))["counts"]["videos_kept"] for f in fs)
print(f"national on disk: {len(fs)} diocese files, {v} videos")
EOF
