"""Builds data/real/calendars/README.md from a run's results, so the coverage figures never go stale."""
from __future__ import annotations

from collections import Counter, defaultdict

REASON_TEXT = {
    "names_person": "names a private person, couple or family (bookings such as 'X and Y', bare names)",
    "non_event_marker": "not an event (lectionary or rota codes, titles without letters)",
    "baptism_individual": "an individual baptism (classes are kept)",
    "wedding_quince": "a wedding, rehearsal, convalidation or quinceañera booking",
    "private_booking": "private, rental, hold, appointment, 'busy' or similar",
    "funeral_memorial": "a funeral, memorial, vigil or rosary for a named person",
    "mass_intention": "a Mass intention",
    "facility_hold": "a room hold ('not available', 'in use')",
    "marked_private": "marked PRIVATE or CONFIDENTIAL in the feed",
    "no_title": "no title",
}
STATUS_TEXT = {
    "blocked": "site refuses this environment (HTTP 403/429, bot check, TLS or proxy error); not bypassed",
    "no_public_feed": "site reachable, but no machine-readable public calendar found on the crawled pages",
    "no_website": "no website in the diocesan directory",
    "not_ingested": "calendar embed found but deliberately not read",
    "excluded": "calendar found but excluded as a whole",
}


def _md(s) -> str:
    return str(s if s is not None else "").replace("|", "/")


def build_readme(head: dict, rows: list[dict], names: dict, platform) -> str:
    c = head["counts"]
    w = head["window"]
    by = head["by_parish"]
    parish_rows = [r for r in rows if r.get("owner_id") not in (None, "dio-oakland")]
    per = defaultdict(list)
    for r in parish_rows:
        per[r["parish_id"]].append(r)
    active = {p: [r for r in rs if r["status"] == "active"] for p, rs in per.items()}
    with_feed = sorted(p for p, rs in active.items() if rs)
    dio = next((r for r in rows if r.get("owner_id") == "dio-oakland"), {})
    dio_lf = dio.get("last_fetch") or {}
    parishes_own = sorted(k for k, v in by.items() if not k.startswith("(") and v.get("from_own_feed", 0))
    parishes_only_dio = sorted(k for k, v in by.items() if not k.startswith("(") and not v.get("from_own_feed", 0))
    dio_events_total = dio_lf.get("kept", 0)

    def status_of(p):
        rs = per.get(p, [])
        if any(r["status"] == "active" for r in rs):
            return "active"
        for s in ("blocked", "no_website", "excluded", "not_ingested", "no_public_feed"):
            if any(r["status"] == s for r in rs):
                return s
        return "no_public_feed"

    all_parishes = sorted(per)
    groups = defaultdict(list)
    for p in all_parishes:
        st = status_of(p)
        if st != "active":
            groups[st].append(p)

    L = []
    L.append("# Public parish calendars (`data/real/calendars/`)\n")
    L.append(f"Built {head['generated_at'][:10]} by `ingest/connectors/calendars/run.py`. Window: the next "
             f"{w['days']} days ({w['start']} to {w['end_exclusive']}, exclusive; {w['timezone']}). "
             "Sources are calendars that parishes publish on their own websites, plus the diocesan events API. "
             "Everything here is public; nothing was read behind a login, a key or a bot check.\n")
    L.append("## In brief\n")
    dio_n, dio_at = c.get("diocesan_events", dio_events_total), c.get("diocesan_events_at_parishes", 0)
    L.append(f"- **{c['events']:,} public events** in the window: {c['events'] - dio_n:,} from **{len(with_feed)} "
             f"parishes** that publish a machine-readable calendar, and {dio_n} from the diocesan calendar "
             f"({dio_at} of them held at a parish).")
    L.append(f"- **{len([k for k in by if not k.startswith('(')])} parishes** have at least one event in the window "
             f"({len(parishes_own)} from their own feed; {len(parishes_only_dio)} only through the diocesan calendar).")
    L.append(f"- Of the 75 directory parishes: {len(with_feed)} with a readable feed; "
             f"{len(groups['blocked'])} whose sites refuse this environment; {len(groups['no_public_feed'])} with no "
             f"public feed on the crawled pages; {len(groups['no_website'])} without a website; "
             f"{len(groups['excluded']) + len(groups['not_ingested'])} whose only calendar was deliberately not read.")
    ex = c.get("excluded_events_by_reason", {})
    L.append(f"- **Privacy:** {sum(ex.values()):,} event instances were dropped because they name or concern private "
             "people or are not events (details below). Only counts are kept; no dropped title is stored anywhere.\n")

    L.append("## Files\n")
    L.append("| File | What it is |\n|---|---|")
    L.append("| `events.json` | One row per event instance (recurring series expanded). Header: window, counts, per-parish "
             "summary, field definitions. Rows are written one per line so diffs stay readable. |")
    L.append("| `feeds.json` | The feed registry: every calendar found (or looked for) per parish, its type, URL, the public "
             "page it is published on, status, and the last fetch result with counts of kept and dropped events. |")
    L.append("| `README.md` | This file, regenerated on every run. |\n")
    L.append("Event fields: " + "; ".join(f"`{k}` {v}" for k, v in head["fields"].items()) + ".\n")

    L.append("## How to rerun\n")
    L.append("```bash\npip install requests icalendar recurring-ical-events\n"
             "python3 ingest/connectors/calendars/run.py                 # refetch every known feed (about 2 minutes)\n"
             "python3 ingest/connectors/calendars/run.py --rediscover    # also look for new feeds on parish sites\n"
             "python3 ingest/connectors/calendars/privacy.py             # privacy self-test\n```\n")
    L.append("Feeds a parish gives us directly (or found from a home connection) go in "
             "`ingest/connectors/calendars/feeds_manual.json` as `{parish_id, type, url, page_url}`; the next run picks "
             "them up. Event `id`s are stable hashes, so a loader can upsert on `id` and delete rows of the same "
             "`feed_id` that are no longer present.\n")

    L.append("## Method\n")
    L.append("1. **Discover** (`discover.py`). Scan the HTML already crawled from each parish site "
             "(`data/raw/parish_sites/`) and up to three of its calendar/events pages for: Google Calendar embeds "
             "(including base64-encoded ids in website-builder widgets), Microsoft 365 published calendars, `.ics`/"
             "`webcal` links, CCB public calendars, MyChurchEvents, CalendarWiz, CatholicWebsite.com's `/calendar.ics`, "
             "and The Events Calendar's WordPress REST route (probed once on WordPress sites). Holiday overlays "
             "(`#holiday@group.v.calendar.google.com`) are ignored.")
    L.append("2. **Fetch** politely: one honest User-Agent with a contact address, at least 2 seconds between requests "
             "to a host, one retry on 429/503, none on 403, no bot-check workarounds. Raw feed bodies are not kept "
             "(charter R22) unless `--keep-raw`.")
    L.append("3. **Parse and expand.** iCalendar with `icalendar` and `recurring-ical-events` (RRULE, RDATE, EXDATE and "
             "moved instances; Windows time-zone names in Microsoft feeds are mapped); The Events Calendar REST "
             "API; CalendarWiz RSS (start and end come from the item GUID); MyChurchEvents JSON (one-off events, and "
             "weekly series whose weekday bit mask is unambiguous; undocumented recurrence codes are skipped and "
             "counted, never guessed; events the parish hides from its public calendar are skipped).")
    L.append("4. **Screen for privacy** (`privacy.py`, below), **guess a category** (`categorize.py`, keyword rules; a "
             "guess, not a judgment), move room codes such as `CH:` or `@JP2 =` into `location`, and drop "
             "duplicates of the same event on two room calendars.")
    L.append("5. **Match diocesan events to parishes** by venue name and city (`St. Jarlath, Oakland` -> `jarlath-oakland`).\n")

    L.append("## Coverage by parish\n")
    L.append("| Parish | Platform | Feeds | Events in window (own feed) | Note | Published at |\n|---|---|---|---|---|---|")
    for p in with_feed:
        rs = active[p]
        plats = sorted({platform(r.get("url")) for r in rs})
        n = by.get(p, {}).get("from_own_feed", by.get(p, {}).get("events", 0))
        notes = []
        if any("facility" in " ".join(e for e in [r.get("note") or "", (r.get("last_fetch") or {}).get("calendar_name") or "",
                                                     r.get("page_url") or "", r.get("label") or ""]).lower()
               or r.get("label") for r in rs):
            notes.append("room/facility calendar")
        if any((r.get("last_fetch") or {}).get("kept", 0) == 0 for r in rs):
            notes.append("a feed with no events in the window")
        if any(r["type"] == "calendarwiz_rss" for r in rs):
            notes.append("RSS: next ~100 items only")
        for r in rs:
            if r.get("display_tz"):
                notes.append(f"times shown as the parish's own embed shows them ({r['display_tz']})")
        pages = sorted({r.get("page_url") for r in rs if r.get("page_url")})
        L.append(f"| {_md(names.get(p, p))} | {', '.join(plats)} | {len(rs)} | {n:,} | {'; '.join(notes)} | "
                 + ", ".join(pages[:2]) + " |")
    L.append("")
    L.append(f"**Diocesan calendar** ({dio.get('url')}): {dio_events_total} events kept in the window "
             f"(fetch status {dio_lf.get('status')}{'; ' + dio_lf['note'] if dio_lf.get('note') else ''}). "
             f"Parishes reached only through it: {', '.join(names.get(p, p) for p in parishes_only_dio) or 'none'}.\n")

    L.append("### Parishes without a readable public calendar\n")
    for st in ("blocked", "no_public_feed", "no_website", "excluded", "not_ingested"):
        ps = groups.get(st) or []
        if not ps:
            continue
        L.append(f"- **{st.replace('_', ' ')}** ({len(ps)}): {STATUS_TEXT[st]}. " + ", ".join(names.get(p, p) for p in ps) + ".")
    special = [r for r in parish_rows if r["status"] in ("excluded", "not_ingested")]
    if special:
        L.append("")
        L.append("Calendars deliberately not read:")
        for r in special:
            L.append(f"- {names.get(r['parish_id'], r['parish_id'])}: {r['note']} (page: {r.get('page_url')}).")
    L.append("")

    L.append("## Privacy\n")
    L.append("Charter rules R19 and R21 (`docs/research/03-ethics-and-data-charter.md`): only public parish events, "
             "never records about private people. Parish calendars, and above all room-booking calendars, mix public "
             "offerings with bookings that name parishioners. The screen fails closed: when a title might name a "
             "private person, the event is dropped.\n")
    L.append("- **Whole calendars** are skipped when their own name says Mass intentions, funerals, weddings, prayer or "
             "sick lists, birthdays, staff availability or appointments; their feed address is not stored.")
    L.append("- **Events** are dropped for the reasons below. Public roles (Fr., Bishop, Deacon, Sister, saints) may "
             "appear in titles; lay names riding along on public events are cut (\"Small Group Kate\" -> \"Small "
             "Group\", \"Speaker, <name>\" -> \"Speaker\").")
    L.append("- **Never stored:** descriptions, organizers, attendees, contact details, meeting links and passcodes, "
             "private-home locations.")
    L.append("- Events from room or facility calendars carry the tag `facility_calendar`: they show how rooms are "
             "used, which is useful for coordination but is not a list of offerings.\n")
    L.append("| Reason dropped | Event instances |\n|---|---|")
    for k, v in sorted(ex.items(), key=lambda kv: -kv[1]):
        L.append(f"| {REASON_TEXT.get(k, k)} | {v:,} |")
    L.append("")

    cats = Counter()
    for v in by.values():
        cats.update(v["categories"])
    L.append("## Categories (keyword guess)\n")
    L.append("| Category | Events |\n|---|---|")
    for k, v in cats.most_common():
        L.append(f"| {k} | {v:,} |")
    L.append("")

    L.append("## Limitations\n")
    L.append("- **Coverage is about websites, not parish life.** Many parishes keep their calendar in a bulletin PDF, "
             "on Facebook, in Flocknote or in a system with no public feed. Absence here is not absence of events.")
    L.append(f"- **{len(groups['blocked'])} parish sites refuse this environment** (datacenter IP blocks). Re-crawl "
             "them from a home connection (`data/real/README-parish-sites.md`), then run with `--rediscover`.")
    L.append("- **Room calendars** (St. Francis of Assisi, Concord; St. Joseph, Fremont; Queen of All Saints, Concord) "
             "list uses of rooms, including meetings and practices, not only public offerings.")
    L.append("- **CalendarWiz RSS** returns only the next ~100 items (about two to three weeks).")
    L.append("- **MyChurchEvents** recurring series with undocumented recurrence codes are skipped (count in `feeds.json`).")
    L.append("- **Titles are as published** (abbreviations, all caps, priests' initials such as `Mass: CA`).")
    L.append("- **Time zones.** A Google calendar whose own website embed displays another zone (`ctz=`) is read "
             "the same way, so times match what the parish publishes: St. Paul, San Pablo stores some series in "
             "America/Chicago and its embed renders Chicago time, which makes its weekday Mass 7:30 AM as on its Mass "
             "times page (a straight conversion would say 5:30 AM). Such feeds carry `display_tz` in `feeds.json`.")
    L.append("- **Snapshots go stale**: calendars change daily. Rerun before using the data operationally.\n")
    L.append("## Sources\n")
    L.append("Public calendar pages and feeds, listed per parish in `feeds.json` (`page_url`, `url`). Diocesan "
             "events: https://www.oakdiocese.org/wp-json/tribe/events/v1/events . Libraries: icalendar "
             "(https://pypi.org/project/icalendar/), recurring-ical-events (https://pypi.org/project/recurring-ical-events/).\n")
    return "\n".join(L)
