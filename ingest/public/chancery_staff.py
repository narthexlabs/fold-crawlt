"""Chancery staff directory (https://www.oakdiocese.org/staff/) -> data/real/chancery_staff.json.

The page is a PowerPack post grid of `bbwp_bios` posts, paginated /staff/page/N/. Each card has
<h3> name, <h4 class="designation"> title, tel:/mailto: links, and department(s) as CSS classes
`bios_departments-<slug>` whose labels come from the grid's filter bar. Values are kept exactly as listed.
Raw HTML pages are cached under data/raw/staff/ (gitignored).

The site's Azure WAF intermittently returns 403; each page is retried with backoff.

Usage: python ingest/public/chancery_staff.py [--offline]   (--offline re-parses cached HTML)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, REAL, get, now_iso, save_json  # noqa: E402

BASE = "https://www.oakdiocese.org/staff/"
CACHE = RAW / "staff"
OUT = REAL / "chancery_staff.json"


def fetch(url: str, path: Path, offline: bool, tries: int = 6) -> str | None:
    if offline:
        return path.read_text(encoding="utf-8") if path.exists() else None
    for attempt in range(tries):
        r = get(url, timeout=30)
        if r is not None and r.ok and "Forbidden" not in r.text[:400]:
            path.write_text(r.text, encoding="utf-8")
            return r.text
        if r is not None and r.status_code == 404:
            return None
        time.sleep(5 * (attempt + 1))
    return path.read_text(encoding="utf-8") if path.exists() else None


def parse(html: str, labels: dict[str, str], page: int) -> list[dict]:
    s = BeautifulSoup(html, "html.parser")
    for li in s.select("li[data-filter]"):
        f = li.get("data-filter") or ""
        if f.startswith(".bios_departments-"):
            labels[f[len(".bios_departments-"):]] = li.get_text(" ", strip=True)
    out = []
    for p in s.select(".pp-content-post"):
        c = p.select_one(".content")
        if not c:
            continue
        name = c.find("h3").get_text(" ", strip=True) if c.find("h3") else None
        des = c.select_one("h4.designation")
        tels = [a.get_text(" ", strip=True) for a in c.select('a[href^="tel:"]')]
        exts = [e.get_text(" ", strip=True) for e in c.select("span.ext") if e.get_text(strip=True)]
        mails = [a["href"][7:].strip() for a in c.select('a[href^="mailto:"]')]
        links = [a["href"] for a in c.select("a[href^=http]")]
        slugs = [k[len("bios_departments-"):] for k in p.get("class", []) if k.startswith("bios_departments-")]
        prof = p.find("meta", itemprop="mainEntityOfPage")
        out.append({
            "name": name,
            "title": des.get_text(" ", strip=True) if des else None,
            "department_slugs": slugs,
            "email": mails[0] if mails else None,
            "emails_all": mails if len(mails) > 1 else None,
            "phone": tels[0] if tels else None,
            "phone_ext": exts[0] if exts else None,
            "phones_all": tels if len(tels) > 1 else None,
            "website": links[0] if links else None,
            "profile_url": prof.get("itemid") if prof else None,
            "post_id": p.get("data-id"),
            "listing_page": page,
            "is_team_or_office": bool(name and not des and re.search(r"team|office|department", name, re.I)),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)

    labels: dict[str, str] = {}
    rows, failed, last = [], [], 1
    first = fetch(BASE, CACHE / "page_1.html", a.offline)
    if first is None:
        sys.exit("staff page unreachable (WAF 403) and no cache; rerun later")
    nums = [int(x) for x in re.findall(r"/staff/page/(\d+)/", first)]
    last = max(nums) if nums else 1
    rows += parse(first, labels, 1)
    for n in range(2, last + 1):
        time.sleep(1.0)
        h = fetch(f"{BASE}page/{n}/", CACHE / f"page_{n}.html", a.offline)
        if h is None:
            failed.append(n)
            continue
        rows += parse(h, labels, n)

    seen, uniq = set(), []
    for r in rows:
        k = r["post_id"] or (r["name"], r["email"])
        if k in seen:
            continue
        seen.add(k)
        offices = [labels[s] for s in r["department_slugs"] if s in labels]
        r["department"] = "; ".join(offices) if offices else None
        r["tags"] = [s for s in r["department_slugs"] if s not in labels]  # e.g. staff, priests, deacons, religious, vicar-general
        uniq.append(r)

    by_dept: dict[str, int] = {}
    for r in uniq:
        by_dept[r["department"] or "(unassigned)"] = by_dept.get(r["department"] or "(unassigned)", 0) + 1
    save_json(OUT, {
        "source": BASE, "retrieved": now_iso(), "listing_pages": last, "failed_pages": failed,
        "count": len(uniq), "department_labels": labels,
        "notes": ["department = office filter label(s) from the grid's CSS classes; non-office classes (staff, priests, deacons, religious, vicar-general) go to tags; '(unassigned)' rows have no office label.",
                  "Values (titles, phones, emails) are as listed on the page; emails are not lower-cased."],
        "count_by_department": dict(sorted(by_dept.items(), key=lambda kv: -kv[1])),
        "staff": uniq})
    print(f"staff: {len(uniq)} rows from {last} pages; failed pages: {failed}")


if __name__ == "__main__":
    main()
