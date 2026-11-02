# US parish and diocese YouTube catalog (`data/real/us/media/`)

The national version of the Media tab's source: parish and diocese YouTube channels, their recent videos and streams,
a privacy screen, and a HEURISTIC ranking per diocese and nationally. Files only; nothing is in Supabase.

Scope: every US diocese **outside California**. California's 11 dioceses: `data/real/california/media/` (same method,
`ingest/california/media_*.py`). Oakland: `data/real/media/` (the live /media page). Together they cover the country.

## How it is built
`ingest/us/media/run.py` reuses the California modules (nothing rebuilt):

1. **collect** (no network): every YouTube link in the crawled parish sites, `data/real/us/sites/<diocese>/<domain>.json`
   (`social.youtube`, and `social.livestream` links that point at YouTube), plus the Gemini url_context records.
   A parish here is one crawled website; a site listed under two folders keeps its assigned diocese over `unassigned-<st>`.
2. **fetch** (youtube.com): each link → its channel's public `/about` page (channel id, title, @handle, description, the
   links under About); single videos via YouTube oEmbed; playlists via the playlist page's owner.
3. **verify** (no network): `media_resolve.match_pair` / `match_diocese`, unchanged.
   - `verified`: the channel's About links to the parish website, or its description names it, or its @handle is the
     site's name; or the parish name plus the city, or plus a link from the parish's own website.
   - `probable`: the parish name matches and nothing else corroborates it; or part of the name plus the city or the
     parish's own link.
   - `unverified` (not read) and `borrowed` (site-builder defaults, national/Vatican/publisher channels, a person's own
     channel, another diocese's or another parish's channel) are listed in `audit.json` with the reason.
   National differences: every name/city pair on a multi-parish site is tried; the "names another city" check uses the
   parish's own state; a channel linked only by `unassigned-<st>` sites is compatible with dioceses whose see is in that state.
4. **feed** (youtube.com): for each verified / probable channel, the public `/videos` tab (last 25 uploads) and `/streams`
   tab (live now, upcoming, and streams of the last 30 days, up to 15). Titles go through
   `ingest/public/media/media_privacy.py`: funerals of lay people, Mass intentions, weddings, baptisms, students and other
   private names are quarantined (redacted title + reason). Ranking: `ingest/public/media/rank.py` HeuristicJudge, once
   per diocese (its file) and, once every diocese is read, once nationally (`national.json`).

**Rules** (in `ingest/california/media_common.py`): youtube.com only; one request every 2 s across the whole process;
robots.txt read and honoured (`/feeds/videos.xml` and `/results` are disallowed and never requested); the honest user
agent `FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)`; no API key. A consent page or a
"confirm you're not a bot" page stops the run and is committed as `_blocked.json`; it is never worked around.
Runs as ONE GitHub Actions job (`media` in narthexlabs/fold-crawlt `.github/workflows/crawl.yml`; one host = one job).

## Files
| file | what |
|---|---|
| `dioceses/<slug>.json` | one per diocese (USCCB slug, or `unassigned-<st>`): `counts`, `candidates` (the diocese's ranked list, up to 100, HEURISTIC), `channels` (owner, verdict + reasons, activity, livestream, the 6 most recent kept videos), `quarantine` (redacted titles + reason). Written only once all of that diocese's channels are read. |
| `coverage.json` | per diocese: parishes indexed, parishes with a YouTube link, parishes with a verified / probable channel, diocese channels, channels active in 45 days, streamed in 30 days, videos kept, on-list candidates, quarantined; plus the collect / verify counts and the run's request counts (`complete` says whether every diocese was read). |
| `audit.json` | channels not catalogued (unverified / borrowed with the reason; unresolved links). |
| `national.json` | the national HEURISTIC list (top 200), written when every diocese has been read. |
| `_blocked.json` | only if YouTube served a consent / bot page: where and when; the run stopped there. |

Dates are approximate: YouTube's tabs say "3 weeks ago", so `published_at` is computed from the time the page was read
(`date_precision: approx`). Every item carries `url`, `source`, `source_url` and `fetched_at`.

## Resume
Actions → "National crawl" → Run workflow, job `media`, minutes `330` (or
`gh api -X POST repos/narthexlabs/fold-crawlt/actions/workflows/crawl.yml/dispatches -f ref=main -f 'inputs[job]=media' -f 'inputs[minutes]=330'`).
The page cache (`data/raw/us/youtube/cache`, restored by actions/cache) and `resolved.json` make each run continue where the
previous one stopped. By hand: `MEDIA_DEADLINE=$(( $(date +%s) + 6000 )) python3 -I ingest/us/media/run.py all`.

## For the /media page (note for the site lane; app/, lib/, public/ were not touched)
- Today `lib/media.ts` reads one Oakland file (`data/real/media/feed.json`) whose items carry `status`, `feed_scope`,
  `deanery_id`. The national data is per diocese: `data/real/us/media/dioceses/<slug>.json` and
  `data/real/california/media/feed/<slug>.json` + `ranked_heuristic.json`.
- A national /media needs: a diocese picker (USCCB slug; `unassigned-<st>` rows are parish sites not yet tied to a diocese,
  best shown under their state), the diocese's `candidates` in rank order as "Worth watching", and per parish the
  channel card (`channels[]`: title, link, verdict, `activity_status`, `livestream.streams_last_30d`, `recent`).
- Show every item as "HEURISTIC, candidate" until a person approves it (the Oakland flow: `set_media_status`); there are no
  human statuses in these files. Use `embed_url` (youtube-nocookie) for playback, `thumbnail_url` for the card.
- Keep the source line: "From the parish's public YouTube channel, read <fetched_at>; date approximate".
- Do not show `probable` channels without the word "probable" (or show verified only); never show `audit.json` publicly.
- Never compare or rank parishes or dioceses by these counts (no "most active" lists); the counts are coverage, not merit.
