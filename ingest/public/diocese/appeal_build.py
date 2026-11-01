"""Build data/real/appeal/appeal.json: what the /appeal page shows about the Bishop's Ministries Appeal.

Everything here is assembled from data already in the repository (each with its own source) plus the diocese's
own Appeal page and 2026 Appeal post, read through the diocesan WordPress REST API (public JSON, the same route the
other diocesan ingests use). No figure is computed that a source does not support; sums are labeled as sums.

    python3 ingest/public/diocese/appeal_build.py            # fetches the two diocesan pages (cached 7 days)
    python3 ingest/public/diocese/appeal_build.py --offline  # uses the cached copies only

Sources read:
  * https://www.oakdiocese.org/bishops-ministries-appeal/ (wp/v2/pages?slug=bishops-ministries-appeal)
  * https://www.oakdiocese.org/2026-bishops-ministries-appeal-one-faith-one-family-one-mission/ (wp/v2/posts)
  * data/real/enrichment/parish_profiles.json  bishops_ministries_appeal (bulletin lines)
  * data/real/cceb_programs.json, data/real/ecosystem/orgs.json (SVdP conferences), data/real/civic/neighbors.json,
    data/real/civic/parish_neighbors.json (partner food sites), data/real/oakland/clergy.json,
    data/real/diocese/hierarchy_stats.json, data/seed/kpi_values.json (school enrollment, CSA Appeal revenue),
    data/real/calendars/events.json (formation / youth / sacramental-preparation events), data/real/media/feed.json
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, ROOT, get, now_iso  # noqa: E402

OUT = REAL / "appeal" / "appeal.json"
CACHE = RAW / "diocese" / "_appeal_cache"
OFFLINE = "--offline" in sys.argv
PAGE_URL = "https://www.oakdiocese.org/bishops-ministries-appeal/"
POST_URL = "https://www.oakdiocese.org/2026-bishops-ministries-appeal-one-faith-one-family-one-mission/"
API_PAGE = "https://www.oakdiocese.org/wp-json/wp/v2/pages?slug=bishops-ministries-appeal"
API_POST = "https://www.oakdiocese.org/wp-json/wp/v2/posts?slug=2026-bishops-ministries-appeal-one-faith-one-family-one-mission"


def cached_json(url: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
    if f.exists() and (OFFLINE or time.time() - f.stat().st_mtime < 7 * 86400):
        return json.loads(f.read_text()), datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
    if OFFLINE:
        return None, None
    r = get(url, timeout=20)
    if r is None or r.status_code != 200:
        return None, None
    f.write_text(r.text)
    return r.json(), now_iso()


def text_of(rendered: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", rendered or ""))).strip()


def load(p: str):
    return json.loads((ROOT / p).read_text())


def main() -> None:
    page, page_got = cached_json(API_PAGE)
    post, post_got = cached_json(API_POST)
    page_text = text_of(page[0]["content"]["rendered"]) if page else ""
    post_text = text_of(post[0]["content"]["rendered"]) if post else ""
    page_html = page[0]["content"]["rendered"] if page else ""

    def has(s: str, t: str) -> bool:
        return s.lower() in t.lower()

    # ---- the four 2026 ministries, in the diocese's own words (short paraphrase; quotes <= 15 words)
    ministries = [
        {"key": "sustain", "name": "Sustain Our Family", "theme": "Outreach to the poor",
         "diocese_says": "Meals and shelter for those in need, comfort and stability for the most vulnerable.",
         "found_in_source": has("Sustain Our Family", post_text)},
        {"key": "build", "name": "Build Our Future", "theme": "Seminarians and vocations",
         "diocese_says": "Seminarian formation and vocations: the next generation of priests.",
         "found_in_source": has("Build Our Future", post_text)},
        {"key": "heal", "name": "Heal Our Members", "theme": "Care for those facing trials, the sick and the homebound",
         "diocese_says": "Counseling and spiritual resources for those facing personal trials.",
         "found_in_source": has("Heal Our Members", post_text)},
        {"key": "empower", "name": "Empower Our Mission", "theme": "Catholic education and faith formation",
         "diocese_says": "Faith formation of young people, so they grow in Christ and lead the Church.",
         "found_in_source": has("Empower Our Mission", post_text)},
    ]
    vimeo = sorted(set(re.findall(r"(?:player\.)?vimeo\.com/(?:video/)?(\d{6,})", page_html)))

    appeal = {
        "theme": "One Faith. One Family. One Mission.",
        "theme_found_in_source": has("One Faith", post[0]["title"]["rendered"] if post else ""),
        "scripture": {"text": "So we, though many, are one body in Christ", "ref": "Romans 12:5",
                      "found_in_source": has("though many, are one body", post_text)},
        "purpose": "The Appeal supports diocesan ministries from one collective fund; gifts cannot be designated "
                   "to a single program. Money a parish raises above its goal returns to that parish.",
        "facts": [
            {"text": "Gifts go to a single, collective fund.", "found_in_source": has("single, collective fund", page_text),
             "source_url": PAGE_URL},
            {"text": "100% of money collected above each parish's goal is returned to the parish.",
             "found_in_source": has("return 100% of all monies collected above", page_text), "source_url": PAGE_URL},
        ],
        "ministries": ministries,
        "videos": [{"vimeo_id": v, "url": f"https://vimeo.com/{v}", "embed_url": f"https://player.vimeo.com/video/{v}"} for v in vimeo],
        "give_url": PAGE_URL,
        "source_page": {"url": PAGE_URL, "api": API_PAGE, "fetched_at": page_got},
        "source_post": {"url": POST_URL, "api": API_POST, "fetched_at": post_got,
                        "published": post[0]["date"] if post else None},
    }

    # ---- Appeal revenue in the audited CSA statements (the latest posted is FY2021)
    kv = load("data/seed/kpi_values.json")["kpi_values"]
    rev = sorted((v for v in kv if v["kpi_id"] == "csa_bishops_appeal" and not v.get("is_mock")), key=lambda v: v["period"])
    appeal["csa_revenue"] = [{"period": v["period"], "value": v["value"], "source": v["source"], "source_url": v["source_url"],
                              "note": v.get("note")} for v in rev]

    # ---- per-parish goal / progress, only where a bulletin printed it
    prof = {r["id"]: r for r in load("data/real/enrichment/parish_profiles.json")["records"]}
    parishes = []
    for pid, r in prof.items():
        rows = r.get("bishops_ministries_appeal") or []
        if not rows:
            continue
        latest = max(x.get("bulletin_date") or "" for x in rows)
        cur = [x for x in rows if x.get("bulletin_date") == latest]
        metric = {x["metric"]: x for x in cur if x.get("metric")}
        lines = [x for x in cur if x.get("category") == "bma"]
        entry = {"entity_id": pid, "name": r["name"], "bulletin_date": latest,
                 "source_url": cur[0]["source_url"], "fetched_at": cur[0].get("fetched_at"), "page": cur[0].get("page")}
        ctx = " ".join(x.get("context") or "" for x in cur)
        if "goal" in metric:
            entry["goal"] = metric["goal"]["value"]
        if "raised" in metric:
            entry["raised"] = metric["raised"]["value"]
        if "percent" in metric:
            entry["percent"] = metric["percent"]["value"]
        m = re.search(r"as of ([A-Za-z]+ ?\d{1,2}, ?\d{4})", ctx)
        if m:
            entry["as_of_text"] = m.group(1)
        if pid == "theresa-oakland" and lines and "raised" not in entry:
            entry["raised"] = lines[0]["values"][0]          # "Percentage Achieved 54% $19,230 as of September 30"
        if pid == "patrick-rodeo":
            # Oct 11 bulletin: "goal of 42,400.00 ... we are at 85%"; Oct 25 bulletin: "We need about $6,000.00"
            earlier = [x for x in rows if x.get("metric") == "percent"]
            if earlier:
                e = earlier[-1]
                g = re.search(r"goal of \$?([\d,]+(?:\.\d\d)?)", e.get("context") or "")
                entry.update({"percent": e["value"], "bulletin_date": e["bulletin_date"], "source_url": e["source_url"],
                              "page": e["page"], "fetched_at": e.get("fetched_at")})
                if g:
                    entry["goal"] = float(g.group(1).replace(",", ""))
            need = [x for x in rows if "need about" in (x.get("label") or "")]
            if need:
                n = need[0]
                entry["still_needed"] = {"value": n["values"][0], "bulletin_date": n["bulletin_date"],
                                         "source_url": n["source_url"], "page": n["page"], "text": "about"}
        if pid == "our-lady-grace-castro-valley":
            entry = {**entry, "excluded": True,
                     "why": "The bulletin's percentages refer to monthly offertory, not the Appeal; nothing is shown."}
            for k in ("goal", "raised", "percent"):
                entry.pop(k, None)
        if pid == "most-holy-rosary-antioch" and lines:
            entry["line_only"] = {"label": "Bishop's Appeal", "amount": lines[0]["values"][0],
                                  "note": "A collection-report line; the bulletin does not say whether it is a week's gifts or a total."}
        # earlier readings of the same parish (its own past only)
        hist = {}
        for x in rows:
            if x.get("metric") in ("raised", "percent"):
                hist.setdefault(x["bulletin_date"], {})[x["metric"]] = x["value"]
        entry["history"] = [{"bulletin_date": d, **v} for d, v in sorted(hist.items())]
        parishes.append(entry)
    appeal["parish_progress"] = sorted(parishes, key=lambda e: e["name"])

    # ---- Sustain Our Family: Catholic Charities, St. Vincent de Paul, food sites at parishes
    cceb = load("data/real/cceb_programs.json")
    orgs = load("data/real/ecosystem/orgs.json")["records"]
    ents = {e["id"]: e for e in load("data/seed/entities.json")["entities"]}
    svdp = [o for o in orgs if o["type"] == "svdp_conference"]
    svdp_parish = sorted({o["id"][5:] for o in svdp if o["id"].startswith("svdp-") and o["id"][5:] in ents})
    pn = load("data/real/civic/parish_neighbors.json")
    food = pn["partner_food_sites"]
    appeal["sustain"] = {
        "cceb": {"programs": [{"name": p["name"], "url": p["url"]} for p in cceb["programs"]],
                 "impact": cceb["impact_2025"], "fetched_at": cceb["fetched_at"], "entity_id": "cceb"},
        "svdp": {"conferences": len(svdp), "with_parish_link": len(svdp_parish), "parish_ids": svdp_parish,
                 "councils": ["chr-svdp-alameda", "chr-svdp-cc"],
                 "source_urls": sorted({o["source_url"] for o in svdp if o.get("source_url")})},
        "food_sites": {"count": len(food), "parishes": len({f["parish_id"] for f in food}),
                       "sites": [{k: f.get(k) for k in ("parish_id", "network", "program", "hours", "source_url")} for f in food],
                       "fetched_at": pn["meta"]["fetched_at"]},
    }

    # ---- Build Our Future: ordinations, deacons, clergy counts
    clergy = load("data/real/oakland/clergy.json")
    hs = load("data/real/diocese/hierarchy_stats.json")
    last = hs["rows"][-1]
    appeal["build"] = {
        "ordinations_2026": clergy["ordinations_priests"][0],
        "deacons": clergy["permanent_deacons"],
        "seminarians": clergy["seminarians"],
        "hierarchy": {"year": last["year"], "diocesan_priests": last["diocesan_priests"], "religious_priests": last["religious_priests"],
                      "permanent_deacons": last["permanent_deacons"], "edition": last["source_note"],
                      "source_url": hs["source_url"], "fetched_at": hs["generated_at"]},
        "seminary": {"name": "St. Patrick's Seminary & University", "org_id": "sem-st-patricks", "url": "https://stpsu.edu/"},
    }

    # ---- Heal Our Members: where pastoral care meets the sick, the elderly and the incarcerated
    nb = load("data/real/civic/neighbors.json")
    fac = nb["facilities"]
    nh = [f for f in fac if f["type"] == "nursing_home"]
    det = [f for f in fac if f["type"] == "detention"]
    hosp = [f for f in fac if f["type"] == "hospital"]
    appeal["heal"] = {
        "nursing_homes": {"count": len(nh), "residents_per_day_sum": round(sum(f.get("avg_residents_per_day") or 0 for f in nh)),
                          "median_miles_to_parish": round(statistics.median(f["nearest_parish"]["miles"] for f in nh if f.get("nearest_parish")), 2),
                          "source": "CMS Care Compare, Nursing Homes Provider Information", "source_url": nb["meta"]["sources"]["nursing_homes"],
                          "period": nb["meta"].get("cms_nh_modified")},
        "hospitals": {"count": len(hosp), "source_url": nb["meta"]["sources"]["hospitals"], "period": (nb["meta"].get("cdph_modified") or "")[:10]},
        "detention": {"count": len(det), "population_as_listed_sum": sum(f.get("population_as_listed") or 0 for f in det),
                      "facilities": [{"name": f["name"], "nearest_parish": (f.get("nearest_parish") or {}).get("id")} for f in det],
                      "source_url": nb["meta"]["sources"]["detention"]},
        "fetched_at": nb["meta"]["fetched_at"],
    }

    # ---- Empower Our Mission: schools, enrollment, formation
    schools = [e for e in ents.values() if e["type"] == "school"]
    enr = [v for v in kv if v["kpi_id"] == "school_enrollment" and not v.get("is_mock")]
    per = max(v["period"] for v in enr)
    cur = [v for v in enr if v["period"] == per]
    cal = load("data/real/calendars/events.json")
    today = date.today().isoformat()
    form = [e for e in cal["events"] if e["category"] in ("formation", "sacramental_preparation", "youth_young_adults")
            and (e.get("start") or "") >= today]
    appeal["empower"] = {
        "schools": len(schools),
        "enrollment": {"period": per, "schools_reporting": len(cur), "sum": sum(v["value"] for v in cur),
                       "source": cur[0]["source"], "source_url": cur[0]["source_url"]},
        "face": {"text": "FACE (Family Aid-Catholic Education) gives need-based tuition grants; for 2026-27 grants are "
                         "$2,750 (elementary) and $3,500 (high school).", "source_url": "https://www.oakdiocese.org/face-about/",
                 "org_id": "dm-face"},
        "formation_events": {"count": len(form), "parishes": len({e["parish_id"] for e in form}),
                             "window": cal["window"], "source": "parish public calendars", "fetched_at": cal["generated_at"]},
    }

    # ---- media tagged to each ministry (feed ids; the page reads the rows and their status from the database)
    feed = load("data/real/media/feed.json")["items"]
    appeal["media_ids"] = {k: [x["id"] for x in feed if k in (x.get("tags") or [])][:12]
                           for k in ("appeal", "sustain", "build", "heal", "empower")}

    out = {"generated_at": now_iso(), "generated_by": "ingest/public/diocese/appeal_build.py", **appeal}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("theme_found_in_source",)}, indent=1),
          [m["found_in_source"] for m in ministries], [f["found_in_source"] for f in appeal["facts"]],
          len(parishes), appeal["empower"]["enrollment"], appeal["heal"]["nursing_homes"], appeal["heal"]["detention"]["population_as_listed_sum"],
          appeal["sustain"]["svdp"]["conferences"], appeal["sustain"]["food_sites"]["count"], {k: len(v) for k, v in appeal["media_ids"].items()})


if __name__ == "__main__":
    main()
