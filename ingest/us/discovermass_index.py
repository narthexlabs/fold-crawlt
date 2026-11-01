"""DiscoverMass national church index from its WordPress sitemaps -> data/real/us/discovermass_index.json.

No church page is requested. The sitemaps (wp-sitemap-posts-item-1..N.xml) are national; the California crawler
(ingest/california/discovermass_crawl.py) already fetched them under robots.txt "Crawl-delay: 10" with its lock file
and 10.5 s pacing, so this script only READS that cache:
    data/raw/california/discovermass/sitemaps/   (git-ignored)
To refresh the cache, run `python3 -I ingest/california/discovermass_crawl.py index` (it fetches the sitemaps
politely and holds the lock); then re-run this script.

    python3 -I ingest/us/discovermass_index.py

Slug shape: <parish-name>-<city>-<st>[-<n>]. State = trailing postal code; "-d-c" = DC; a trailing "-<n>" after
a state code marks a second page for the same slug (kept, counted separately). City is a GUESS: the longest
trailing token run that always travels together across a state's slugs (a city with 2+ churches), else the last
token; then "park/heights/springs..." and "san/new/fort..." neighbours are folded in. A hint only (it errs).
"""
import fcntl
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "raw" / "california" / "discovermass"
MAPS = CACHE / "sitemaps"
OUT = ROOT / "data" / "real" / "us" / "discovermass_index.json"
BASE = "https://discovermass.com"
STATES = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd "
             "oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc pr gu vi as mp".split())
NOT_CITY = {"church", "parish", "cathedral", "chapel", "mission", "shrine", "basilica", "catholic", "site", "worship",
            "community", "center", "oratory", "newman"}
CITY_PREFIX = {"san", "santa", "saint", "st", "new", "east", "west", "north", "south", "fort", "ft", "port", "mount",
               "mt", "los", "las", "el", "la", "sault", "glen"}
CITY_FINAL = {"park", "city", "heights", "springs", "beach", "falls", "hills", "grove", "valley", "lake", "creek",
              "ridge", "island", "harbor", "point", "river", "mills", "view", "village", "junction", "station",
              "bluff", "rapids", "forks", "landing", "haven", "gardens", "lakes", "harbour", "run", "hill"}


def parse_slug(slug: str) -> dict:
    dup = None
    m = re.match(r"^(.*-(?:%s|d-c))-(\d+)$" % "|".join(sorted(STATES)), slug)
    base = slug
    if m:
        base, dup = m.group(1), int(m.group(2))
    state, body = None, base
    if base.endswith("-d-c"):
        state, body = "dc", base[:-4]
    else:
        m = re.search(r"-([a-z]{2})$", base)
        if m and m.group(1) in STATES:
            state, body = m.group(1), base[:-3]
    return {"slug": slug, "base": base, "dup": dup, "state": state, "body": body}


def main():
    lockf = (CACHE / ".lock").open("a")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("a discovermass crawler holds the lock; try again later")
    top = (MAPS / "wp-sitemap.xml").read_text()
    names = [u.rsplit("/", 1)[1] for u in re.findall(r"<loc>([^<]+)</loc>", top) if "posts-item" in u]
    rows, fetched = [], []
    for n in names:
        p = MAPS / n
        if not p.exists():
            sys.exit(f"missing cached sitemap {n}; run the California crawler's 'index' step first")
        fetched.append(json.loads(p.with_suffix(".json").read_text())["fetched_at"])
        for loc, lastmod in re.findall(r"<loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?", p.read_text()):
            mm = re.search(r"/church/([^/]+)/?$", loc)
            if mm:
                r = parse_slug(mm.group(1))
                r["lastmod"] = (lastmod or "")[:10] or None
                r["sitemap"] = n
                rows.append(r)
    # City guess: per state, count trailing n-grams of the slug body.
    cnt = defaultdict(Counter)
    for r in rows:
        if r["state"]:
            t = r["body"].split("-")
            for k in range(1, min(4, len(t)) + 1):
                cnt[r["state"]]["-".join(t[-k:])] += 1
    for r in rows:
        r["city_guess"] = None
        if not r["state"]:
            continue
        t, c = r["body"].split("-"), cnt[r["state"]]
        if len(t) < 2 or t[-1] in NOT_CITY:
            continue
        k = 1
        if c["-".join(t[-1:])] >= 2:
            while k < min(4, len(t) - 1) and c["-".join(t[-k - 1:])] == c["-".join(t[-k:])]:
                k += 1
        if t[-k] in CITY_FINAL and k < len(t) - 1 and t[-k - 1] not in NOT_CITY:
            k += 1  # "...-asbury-park": the final word alone is not a city
        while k < min(4, len(t) - 1) and t[-k - 1] in CITY_PREFIX:
            k += 1  # "...-west-covina", "...-san-antonio"
        r["city_guess"] = "-".join(t[-k:])
    by_state = Counter(r["state"] or "unresolved" for r in rows)
    uniq = Counter(r["state"] or "unresolved" for r in rows if r["dup"] is None)
    out_rows = [{"slug": r["slug"], "state": r["state"], "city_guess": r["city_guess"], "dup": r["dup"],
                 "lastmod": r["lastmod"]} for r in rows]
    unresolved = [r["slug"] for r in rows if not r["state"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "discovermass.com WordPress sitemaps (wp-sitemap-posts-item-*.xml); no church pages fetched",
        "sitemap_index_url": BASE + "/wp-sitemap.xml", "url_template": BASE + "/church/{slug}/",
        "sitemaps": names, "sitemaps_fetched_at": {"first": min(fetched), "last": max(fetched)},
        "robots": "User-agent: * / Crawl-delay: 10 (cache robots.json fetched_at in the California crawler cache)",
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(rows), "count_excluding_dup_pages": sum(uniq.values()),
        "by_state_all_pages": dict(sorted(by_state.items(), key=lambda x: (-x[1], x[0]))),
        "by_state_excluding_dup_pages": dict(sorted(uniq.items(), key=lambda x: (-x[1], x[0]))),
        "unresolved_state_count": len(unresolved), "unresolved_state_slugs": unresolved,
        "churches": out_rows}, separators=(",", ":"), ensure_ascii=False))
    print(len(rows), "pages;", sum(uniq.values()), "excluding dup pages;", len(unresolved), "unresolved")
    print("city_guess filled:", sum(1 for r in rows if r["city_guess"]))


if __name__ == "__main__":
    main()
