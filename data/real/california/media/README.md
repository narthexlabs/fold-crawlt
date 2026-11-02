# California YouTube catalog (`data/real/california/media/`)

The 11 California dioceses other than Oakland (Oakland's feed is `data/real/media/`). Parish, school and diocese YouTube
channels, their recent videos and streams, a privacy screen, and a HEURISTIC ranking statewide and per diocese.
Files only; nothing is in Supabase. National counterpart (every other diocese): `data/real/us/media/`.

## How it is built
| step | script | network |
|---|---|---|
| 1 collect | `ingest/california/media_collect.py`: every YouTube link the parish index holds (site crawl `social`, the diocese directory row, the DiscoverMass row, Wayback captures, diocese caches); the merge clean-up's verdict on each link is recorded | none (run in fold-app; its two inputs are copied to `_inputs/` for the runner) |
| 2 resolve | `ingest/california/media_resolve.py fetch`: link → channel `/about` page (oEmbed for single videos, owner for playlists) | youtube.com |
| 3 verify | `media_resolve.py verify`: ownership verdict with reasons (`verified`, `probable`, `unverified`, `borrowed`) | none |
| 4 feed | `ingest/california/media_feed.py`: `/videos` (last 25) and `/streams` (live, upcoming, last 30 days, up to 15) tabs of each verified / probable channel → `ingest/public/media/media_privacy.py` → `ingest/public/media/rank.py` HeuristicJudge (statewide and per diocese) | youtube.com |
| 5 report | `ingest/california/media_report.py`: channels.json, audit.json, coverage.json | none |

Rules (`ingest/california/media_common.py`): youtube.com only, one request every 2 s, robots.txt honoured
(`/feeds/videos.xml` and `/results` are disallowed and never requested), honest user agent
`FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)` (2026-11-02: with the HTTP library's default user
agent YouTube no longer returns the page data the parser reads), no API key. A consent or "confirm you're not a bot" page
stops the run (`_blocked.json`), never worked around. Runs as ONE GitHub Actions job (`media`, narthexlabs/fold-crawlt).

Verdicts: `verified` = the channel's About links to the parish website, or its description names it, or its @handle is the
site's name; or the parish name plus the city or a link from the parish's own site. `probable` = the name matches with
nothing else. Only these two are read. `audit.json` lists the rest with the reason.

## Files
| file | what |
|---|---|
| `channels.json` | catalogued channels (parish / school / diocese) with verdict, reasons, evidence, activity, livestream sample |
| `feed/<diocese>.json` | every kept video of that diocese's channels (Oakland's item fields + diocese, parish_id, channel, is_live, livestream, `prefilter: routine_liturgy` for routine Mass streams) |
| `feed.json` | counts and notes for the whole state (items are in `feed/`) |
| `ranked_heuristic.json` | HEURISTIC candidate lists: statewide top 100 and per diocese (top 100 each) |
| `quarantine.json` | titles dropped by the privacy screen (redacted title + reason) |
| `coverage.json` | per diocese: parishes indexed, with a YouTube link, with a verified / probable channel, channels active / streaming, videos kept, on-list candidates, quarantined |
| `audit.json` | channels not catalogued (unverified / borrowed / unresolved) and why |
| `_inputs/` | the collect step's output (candidates + trimmed parish index), so the runner can run steps 2-5 |

Dates are approximate ("3 weeks ago" on YouTube → `date_precision: approx`).

## Resume
Dispatch job `media` (`gh api -X POST repos/narthexlabs/fold-crawlt/actions/workflows/crawl.yml/dispatches -f ref=main
-f 'inputs[job]=media' -f 'inputs[minutes]=110'`). After a new site crawl, re-run step 1 in fold-app
(`python3 -I ingest/california/media_collect.py --reindex`), then copy `data/raw/california/youtube/{candidates,parish_index}.json`
to `_inputs/` (trimmed, as in the 2026-11-02 copy) and push to both repos.
