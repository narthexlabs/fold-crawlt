"""Pull oakdiocese.org pages, event venues/organizers and keyword-matched posts through the public WordPress
REST API (the HTML front end answers 403 to this environment; /wp-json/ is open).

Writes raw JSON to data/raw/ecosystem/ (gitignored). build_ecosystem.py reads it for evidence snippets.

    python3 -I ingest/public/ecosystem/oakdiocese_wp.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, get, now_iso, save_json  # noqa: E402

BASE = "https://www.oakdiocese.org/wp-json/wp/v2"
OUT = RAW / "ecosystem"
DELAY = 0.8

# Search terms for news posts that tend to name religious communities, formation programs and ministries.
POST_TERMS = [
    "seminarian", "seminary", "vocations", "ordination", "diaconate", "deacon formation", "religious sisters",
    "religious brothers", "Dominican", "Jesuit", "Franciscan", "Salesian", "Paulist", "Carmelite", "Benedictine",
    "Missionaries of Charity", "Daughters of Charity", "Holy Names", "Christian Brothers", "jubilarian",
    "consecrated life", "Saint Vincent de Paul", "Knights of Columbus", "Serra", "Order of Malta",
    "chaplain", "Santa Rita", "jail ministry", "prison ministry", "hospital", "campus ministry", "Newman",
    "retreat", "Cursillo", "Charismatic", "Legion of Mary", "Opus Dei", "Neocatechumenal", "Focolare",
    "Communion and Liberation", "Vietnamese", "Filipino", "Korean", "Chinese", "Tongan", "Eritrean",
    "Portuguese", "Hispanic ministry", "Black Catholic", "Catholic Worker", "St. Mary's Center",
    "Graduate Theological Union", "School of Applied Theology", "lay ministry", "Catholic Voice",
    "Serra Club", "Catholic Daughters", "Saint Mary's College", "Cal State", "Newman Center", "Kaiser",
    "Highland Hospital", "John Muir", "Couples for Christ", "Ignatian", "Jesuit Volunteer", "Holy Redeemer",
    "San Damiano", "Carmel", "Missionaries of Faith", "Siervos", "Capuchin", "Oblates", "Divine Word",
    "De La Salle", "Holy Family Sisters", "Sisters of the Holy Family", "Mercy", "Maryknoll", "Marist",
    "Brazilian", "Haitian", "Indonesian", "Polish", "Ethiopian", "Ge'ez", "Syro-Malabar", "Igbo",
    "Little Sisters", "Notre Dame", "Carondelet", "Moreau", "Salesian College", "Cristo Rey", "Lumen Christi",
    "campus minister", "young adult", "Catholic Relief Services", "Malta", "Gabriel Project", "SPRED",
    "Catholic Charities", "food pantry", "homeless", "Sant'Egidio", "Friars of the Renewal", "Rachel's Vineyard",
    "Schoenstatt", "Marriage Encounter", "Teams of Our Lady", "Secular Franciscan", "Third Order", "Benedict",
]


def fetch_all(route: str, params: str = "") -> list[dict]:
    items, page = [], 1
    while True:
        url = f"{BASE}/{route}?per_page=100&page={page}{params}"
        r = get(url, timeout=40)
        if r is None or not r.ok:
            print(f"  {route} page {page}: {getattr(r, 'status_code', 'ERR')}")
            break
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        for b in batch:
            b["_api_url"] = url
        items += batch
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
        time.sleep(DELAY)
    return items


def main() -> None:
    fetched = now_iso()
    if "--posts-only" in sys.argv:
        prev = __import__("json").loads((OUT / "oakdiocese_posts.json").read_text())
        posts = {p["id"]: p for p in prev["items"]}
        hits = prev["terms"]
        for term in POST_TERMS:
            if term in hits:
                continue
            it = fetch_all("posts", f"&search={term.replace(' ', '+')}&_fields=id,slug,link,title,date,content")
            hits[term] = [p["id"] for p in it]
            for p in it:
                posts.setdefault(p["id"], p)
            print(f"posts '{term}': {len(it)}")
            time.sleep(DELAY)
        save_json(OUT / "oakdiocese_posts.json", {"fetched_at": fetched, "terms": hits, "items": list(posts.values())})
        return
    pages = fetch_all("pages", "&_fields=id,slug,link,title,content,modified,parent")
    print(f"pages: {len(pages)}")
    save_json(OUT / "oakdiocese_pages.json", {"fetched_at": fetched, "items": pages})
    for route in ("tribe_venue", "tribe_organizer"):
        it = fetch_all(route, "&_fields=id,slug,link,title,content,meta")
        print(f"{route}: {len(it)}")
        save_json(OUT / f"oakdiocese_{route}.json", {"fetched_at": fetched, "items": it})
        time.sleep(DELAY)
    posts: dict[int, dict] = {}
    hits: dict[str, list[int]] = {}
    for term in POST_TERMS:
        it = fetch_all("posts", f"&search={term.replace(' ', '+')}&_fields=id,slug,link,title,date,content")
        hits[term] = [p["id"] for p in it]
        for p in it:
            posts.setdefault(p["id"], p)
        print(f"posts '{term}': {len(it)}")
        time.sleep(DELAY)
    save_json(OUT / "oakdiocese_posts.json", {"fetched_at": fetched, "terms": hits, "items": list(posts.values())})


if __name__ == "__main__":
    main()
