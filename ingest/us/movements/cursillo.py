"""National Cursillo Movement (cursillousa.org, formerly natl-cursillo.org) -> diocesan Cursillo links (lane D, source 4).

https://cursillousa.org/links-contacts/ lists one website per diocesan language group ("California - Oakland (F)": F
Filipino, S Spanish, V Vietnamese, K Korean, P Portuguese, ...). That is DIOCESE-level, not parish-level: Cursillo
weekends are run by the diocese and parish "Ultreya" groups are rarely listed nationally, so nothing goes into
by_parish.json. Stored per link: state, diocese as listed, our see slug when the name matches, language group, URL.
No contact names (the page's contact list is not read). One request.

    python3 -I ingest/us/movements/cursillo.py
"""
from __future__ import annotations

import html as H
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import common_fetch as cf  # noqa: E402

URL = "https://cursillousa.org/links-contacts/"
LANG = {"F": "Filipino", "S": "Spanish", "V": "Vietnamese", "K": "Korean", "P": "Portuguese", "C": "Chinese",
        "H": "Haitian Creole", "I": "Italian", "E": "English"}
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
          "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
          "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
          "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
          "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
          "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
          "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
          "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR"}


def see_lookup():
    out = {}
    for s in (c.load(c.ROOT / "data/real/us/dioceses.json", {}) or {}).get("dioceses") or []:
        nm = re.sub(r"^(Archdiocese|Diocese|Eparchy|Archeparchy) of (the )?", "", s["name"]).lower()
        out.setdefault((s.get("see_state"), c.norm_city(nm)), s["slug"])
        out.setdefault((s.get("see_state"), c.norm_city(s.get("see_city"))), s["slug"])
    return out


def main() -> None:
    pol = cf.Polite()
    rb = pol.robots_for(URL)
    r = pol.get(URL)
    sees = see_lookup()
    out = {"source": "National Cursillo Movement - Links & Contacts", "source_url": URL, "user_agent": c.UA,
           "robots": {"url": rb["url"], "status": rb["status"], "class": rb["class"], "allowed": pol.allowed(URL)},
           "fetched_at": c.now_iso(), "http_status": r["status"], "class": r.get("class"),
           "kind": "diocese-level language-group websites; not parish groups; not merged into by_parish.json"}
    recs = []
    if r["status"] == 200:
        h = r["body"].decode("utf-8", "replace")
        for url, txt in re.findall(r'href="(https?://[^"]+)"[^>]*>([^<]{3,90})</a>', h):
            t = H.unescape(txt).replace("\xa0", " ").strip()
            m = re.match(r"^([A-Za-z .]+?)\s*[–-]\s*(.+?)(?:\s*\(([A-Z])\))?$", t)
            st = STATES.get(t) if t in STATES else (STATES.get(m.group(1).strip()) if m else None)
            if not st or "cursillousa.org" in url:
                continue
            dio = m.group(2).strip() if m and m.group(1).strip() in STATES else t
            lang = LANG.get(m.group(3), m.group(3)) if m and m.group(3) else "English (default group)"
            slug = sees.get((st, c.norm_city(dio))) or sees.get((st, c.norm_city(dio.split("-")[0])))
            recs.append({"state": st, "diocese_as_listed": dio, "see_slug": slug, "language_group": lang,
                         "website": url, "source_url": URL, "fetched_at": out["fetched_at"]})
    out["count"] = len(recs)
    out["matched_to_see"] = sum(1 for x in recs if x["see_slug"])
    out["records"] = recs
    c.save(c.OUT / "cursillo.json", out, list_key="records")
    print(out["http_status"], out["count"], "links;", out["matched_to_see"], "matched to a see")


if __name__ == "__main__":
    main()
