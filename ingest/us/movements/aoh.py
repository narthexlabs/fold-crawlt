"""Ancient Order of Hibernians - "AOH Divisions Directory" -> divisions and where they meet (2026-11-02).

https://aoh.com/aoh-directory/ (linked from aoh.com as "AOH Divisions Directory" / "Membership/Division Directory") lists
every division by state and county on one page: division name, meeting day/time, meeting place and address. robots.txt
(checked at run time) disallows only /membership-roster/, /join/, /contact/ and /archive/. One request.

Stored (institutional only): division name, state, county, meeting day/time text, meeting place + street address, ZIP,
the division's public website. The "Contact:" part (an officer's name, e-mail, phone) is cut off and never stored, and
the meeting text is dropped if it carries an e-mail address or a phone number.
    python3 -I ingest/us/movements/aoh.py
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
sys.path.insert(0, str(HERE.parent.parent / "public"))
import privacy as pv  # noqa: E402  (the shared privacy screen: a private name in a free-text field -> "[name withheld]")

PAGE = "https://aoh.com/aoh-directory/"
OUTF = c.OUT / "aoh.json"
STATE = re.compile(r'<a class=anchor id=([A-Z]{2})></a>')
COUNTY = re.compile(r"<div class=aohco>\s*([^<]*)")
DIV = re.compile(r"<div class=aohdivision><div class=hd>(.*?)</div><div class=tx>(.*?)</div><div class=ed>.*?viewunit\('([^']+)'\)", re.S)
LOC = re.compile(r"^(.*?),\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$")
PII = re.compile(r"@|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")


def text(s: str) -> str:
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def main() -> None:
    pol = cf.Polite()
    rb = pol.robots_for(PAGE)
    robots = {"url": rb["url"], "status": rb["status"], "class": rb["class"], "crawl_delay": rb.get("crawl_delay"),
              "page_allowed": pol.allowed(PAGE)}
    if not robots["page_allowed"]:
        print("robots.txt disallows the page; stopping"); return
    r = pol.get(PAGE, timeout=60, max_bytes=8_000_000)
    if r["status"] != 200:
        print("fetch:", r["status"], r.get("class")); return
    page = r["body"].decode("utf-8", "replace")
    marks = [(m.start(), m.group(1)) for m in STATE.finditer(page)]
    counties = [(m.start(), text(m.group(1))) for m in COUNTY.finditer(page)]
    recs = []
    for m in DIV.finditer(page):
        pos = m.start()
        st = next((s for p, s in reversed(marks) if p < pos), None)
        county = next((s for p, s in reversed(counties) if p < pos), None)
        hd, tx, unit = m.group(1), m.group(2), m.group(3)
        site = re.search(r'href="(https?://[^"]+)"', hd)
        name = text(hd)
        tx = re.sub(r'<div style="float:right;">.*?</div>', " ", tx, flags=re.S)
        tx = re.split(r"Contact:", tx)[0]
        lines = [text(x) for x in re.split(r"<br\s*/?>", tx) if text(x)]
        meet, place, street, city, zip_ = None, None, None, None, None
        for ln in lines:
            lm = LOC.match(ln)
            if lm and not place:
                head = [x.strip() for x in lm.group(1).split(",")]
                if len(head) >= 2:
                    place, street = ", ".join(head[:-1]), head[-1]
                else:
                    place, street = None, head[0]
                city, zip_ = lm.group(2).strip(), lm.group(4)
                st = lm.group(3) or st
            elif not meet and not PII.search(ln) and len(ln) < 200:
                meet = ln
        recs.append({"unit": unit, "division": name, "state": st, "county": county, "meeting_time": meet,
                     "meeting_place": place, "street": street, "city": city, "zip": zip_,
                     "website": site.group(1) if site else None, "source_url": PAGE, "fetched_at": c.now_iso()})
    withheld = 0
    for x in recs:
        for k in ('meeting_time', 'meeting_place'):
            if x.get(k):
                x[k], n = pv.redact(x[k])
                withheld += n
    if not recs:
        print("no divisions parsed"); return
    old = {x["unit"]: x for x in (c.load(OUTF, {}) or {}).get("records", [])}
    for x in recs:
        if old.get(x["unit"], {}).get("match"):
            x["match"] = old[x["unit"]]["match"]
    c.save(OUTF, {"source": "Ancient Order of Hibernians - AOH Divisions Directory", "source_url": PAGE, "user_agent": c.UA,
                  "robots": robots, "fetched_at": c.now_iso(), "count": len(recs),
                  "fields": "institutional only: division name, state, county, meeting time text, meeting place + address, website",
                  "note": "the directory's Contact: part (officer name, e-mail, phone) is never stored; parish match by match.py",
                  "privacy": f"ingest/public/privacy.py redact on {('meeting_time', 'meeting_place')}: {withheld} name(s) withheld",
                  "records": recs}, list_key="records")
    print(len(recs), "divisions;", sum(1 for x in recs if x["street"]), "with a meeting address")


if __name__ == "__main__":
    main()
