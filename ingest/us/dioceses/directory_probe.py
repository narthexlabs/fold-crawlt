#!/usr/bin/env python3
"""Probe each US see's own website for a cheap, machine-readable parish directory (lane G4).

    python3 -I ingest/us/dioceses/directory_probe.py [--only slug,slug] [--minutes 60] [--workers 12] [--refresh]

For every see in data/real/us/dioceses.json (196, all with a website), at most ~1 request per second per host
(robots.txt Crawl-delay honoured), honest FindTheChurch user agent, never a challenge solved or worked around:
  1. robots.txt                                  (unreadable because of a challenge -> `challenge`, stop)
  2. /wp-json/                                   WordPress REST root: namespaces + routes
  3. /wp-json/wp/v2/types                        custom post types; a type whose slug/name says parish / church /
                                                 location / directory is the parish directory
  4. that type's collection (<=10 pages of 100)  -> parishes.json (name, city, address, website, lat/lng when present)
     no such type: /wp-json/wp/v2/search?search=parish&subtype=page  (records the page that lists parishes)
  5. /sitemap.xml (or robots.txt Sitemap:)       status + whether a parish-looking URL appears
  6. /parishes/, /find-a-parish/, /parish-directory/  (only when no REST door; status only, HTML is not parsed here)

Status per see (data/real/us/dioceses/directory_status.json):
  open-rest   a parish post type answered with records (parishes.json written)
  rest-pages  REST is open but parishes are on an ordinary page (url recorded; per-diocese reader needed)
  html        no REST door; a sitemap or an HTML directory page answered (per-diocese reader needed)
  challenge   a bot challenge (Cloudflare, Sucuri, Imperva...) on robots.txt or the first door
  blocked     403/429 without a challenge page, or robots.txt disallows the doors
  dead        DNS / TLS / connection failure, 5xx
  egress_closed  our own network refused the request (sandbox proxy); nothing is known about the site. Re-run.

Privacy: only parish name, address, city, zip, website, coordinates and the source URL are kept. Phones, e-mails and
any person's name (pastor, staff) are not stored from these generic reads.
Raw REST bodies are cached in data/raw/us/dioceses/<slug>/ (gitignored); a rerun asks only for what is missing.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import html as _html
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import common_fetch as CF  # noqa: E402

US = ROOT / "data" / "real" / "us"
OUT = US / "dioceses"
RAW = ROOT / "data" / "raw" / "us" / "dioceses"
STATUS = OUT / "directory_status.json"

PARISH_TYPE = re.compile(r"parish|church|parroquia|location|directory|communit|mass[-_]?times|worship", re.I)
NOT_PARISH_TYPE = re.compile(r"school|staff|clergy|priest|people|person|employ|job|event|news|post$|page$|attachment|media|"
                             r"block|template|menu|nav|wp_|elementor|product|order|form|tribe_venue|tribe_organizer", re.I)
URL_RX = re.compile(r"https?://[^\s\"'<>]+", re.I)
SKIP_SITE = re.compile(r"facebook\.com|instagram\.com|twitter\.com|x\.com/|youtube\.com|google\.com/maps|goo\.gl|maps\.app|"
                       r"flocknote|wa\.me|mailto:|tel:|\.(?:pdf|jpe?g|png|gif)(?:\?|$)", re.I)
ZIP_RX = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
STREET_RX = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,40}\b(?:st|street|rd|road|ave|avenue|blvd|boulevard|dr|drive|way|ln|lane|"
                       r"ct|court|pl|place|hwy|highway|pkwy|parkway|cir|circle|ter|terrace|sq|square|trl|trail|pike|route|rte)\b\.?",
                       re.I)
HTML_DOORS = ["/parishes/", "/find-a-parish/", "/parish-directory/"]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def text(s) -> str:
    if s is None:
        return ""
    if isinstance(s, dict):
        s = s.get("rendered", "")
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(s)))).strip()


class Probe:
    def __init__(self, see: dict, pol: CF.Polite, refresh: bool):
        self.see, self.pol, self.refresh = see, pol, refresh
        self.slug = see["slug"]
        u = urlparse(see["website"] if "://" in see["website"] else "https://" + see["website"])
        self.base = f"https://{u.netloc}"  # see common_fetch.https_only: no plain-HTTP egress from the sandbox
        self.raw = RAW / self.slug
        self.raw.mkdir(parents=True, exist_ok=True)
        self.log: list[dict] = []

    def get(self, path_or_url: str, accept: str | None = None, cache: bool = True) -> dict:
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        fn = self.raw / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
        if cache and fn.exists() and not self.refresh:
            d = json.loads(fn.read_text())
            d["body"] = d.get("body", "").encode("utf-8")
            return d
        r = self.pol.get(url, accept=accept)
        rec = {"url": url, "final_url": r.get("final_url"), "status": r.get("status"), "class": r["class"],
               "ctype": r.get("ctype", ""), "headers": {k: v for k, v in (r.get("headers") or {}).items()
                                                        if k in ("x-wp-total", "x-wp-totalpages", "content-type")},
               "fetched_at": now_iso()}
        self.log.append({k: rec[k] for k in ("url", "status", "class", "fetched_at")})
        body = r.get("body") or b""
        if r["class"] not in CF.EGRESS_FAIL:          # an egress refusal says nothing about the site; never cache it
            fn.write_text(json.dumps(dict(rec, body=body.decode("utf-8", "replace") if r["class"] == "ok" else ""),
                                     ensure_ascii=False))
        rec["body"] = body if r["class"] == "ok" else b""
        return rec

    def json_of(self, rec: dict):
        if rec["class"] != "ok":
            return None
        try:
            return json.loads(rec["body"])
        except ValueError:
            return None

    def run(self) -> dict:
        res = {"slug": self.slug, "name": self.see["name"], "website": self.see["website"], "base": self.base,
               "checked_at": now_iso(), "status": None, "doors": {}, "notes": []}
        rb = self.pol.robots_for(self.base + "/")
        res["robots"] = {"status": rb["status"], "class": rb["class"], "crawl_delay": rb.get("crawl_delay")}
        if rb["class"] in CF.EGRESS_FAIL:
            res["status"] = "egress_closed"
            return res
        if rb["class"] == "challenge":
            res["status"] = "challenge"
            return res
        # --- WordPress REST
        root = self.get("/wp-json/", accept="application/json")
        res["doors"]["wp-json"] = {"status": root["status"], "class": root["class"]}
        if root["class"] in CF.EGRESS_FAIL:
            res["status"] = "egress_closed"
            return res
        if root["class"] == "challenge":
            res["status"] = "challenge"
            return res
        rj = self.json_of(root)
        if isinstance(rj, dict) and "namespaces" in rj:
            res["doors"]["wp-json"]["namespaces"] = rj.get("namespaces", [])[:40]
            types = self.json_of(self.get("/wp-json/wp/v2/types", accept="application/json")) or {}
            cands = []
            for k, t in (types.items() if isinstance(types, dict) else []):
                nm = f"{k} {t.get('name', '')} {t.get('rest_base', '')}"
                if PARISH_TYPE.search(nm) and not NOT_PARISH_TYPE.search(k):
                    cands.append((k, t.get("rest_base") or k, t.get("name")))
            res["doors"]["wp_types_parishlike"] = [c[0] for c in cands]
            for k, rest_base, nm in cands[:3]:
                items, pages = self.collection(rest_base)
                recs = [r for r in (self.record(it, k) for it in items) if r]
                res["doors"].setdefault("collections", []).append({"type": k, "rest_base": rest_base, "items": len(items),
                                                                    "pages": pages, "with_website": sum(1 for r in recs if r["website"])})
                if len(recs) >= 5:
                    self.write_parishes(recs, k, rest_base)
                    res["status"] = "open-rest"
                    res["parishes"] = len(recs)
                    res["parishes_with_website"] = sum(1 for r in recs if r["website"])
                    res["parish_type"] = k
                    break
            if not res["status"]:
                q = self.json_of(self.get("/wp-json/wp/v2/search?" + urlencode({"search": "parishes", "per_page": 20}),
                                          accept="application/json"))
                hits = [{"title": text(h.get("title")), "url": h.get("url"), "subtype": h.get("subtype")}
                        for h in (q if isinstance(q, list) else []) if isinstance(h, dict)]
                hits = [h for h in hits if re.search(r"parish|church|director|find", f"{h['title']} {h['url']}", re.I)]
                res["doors"]["wp_search_parish"] = hits[:8]
                res["status"] = "rest-pages" if hits else None
        # --- sitemap
        sm_url = None
        rp = rb.get("rp")
        if rp is not None and getattr(rp, "site_maps", None) and rp.site_maps():
            sm_url = rp.site_maps()[0]
        sm = self.get(sm_url or "/sitemap.xml", accept="application/xml,text/xml,*/*")
        res["doors"]["sitemap"] = {"url": sm["url"], "status": sm["status"], "class": sm["class"]}
        if sm["class"] == "ok":
            locs = re.findall(rb"<loc>\s*([^<\s]+)\s*</loc>", sm["body"])[:3000]
            res["doors"]["sitemap"]["locs"] = len(locs)
            res["doors"]["sitemap"]["parishlike"] = [x.decode() for x in locs if re.search(rb"parish|church|director", x, re.I)][:10]
        # --- HTML directory pages (status only)
        if not res["status"]:
            for p in HTML_DOORS:
                h = self.get(p)
                res["doors"].setdefault("html", []).append({"path": p, "status": h["status"], "class": h["class"]})
                if h["class"] == "ok":
                    res["status"] = "html"
                    res["directory_page"] = h.get("final_url") or h["url"]
                    break
                if h["class"] in {"challenge", "blocked_403", "blocked_429"} | set(CF.EGRESS_FAIL):
                    break
        if not res["status"]:
            classes = [d.get("class") for d in [res["doors"].get("wp-json", {}), res["doors"].get("sitemap", {})]
                       + res["doors"].get("html", [])]
            if any(c in CF.EGRESS_FAIL for c in classes):
                res["status"] = "egress_closed"
            elif "challenge" in classes:
                res["status"] = "challenge"
            elif any(c in ("blocked_403", "blocked_429", "robots_disallowed") for c in classes):
                res["status"] = "blocked"
            elif sm["class"] == "ok":
                res["status"] = "html"
            elif all(c in ("dns", "ssl_error", "conn_error", "timeout", "http_5xx") for c in classes if c):
                res["status"] = "dead"
            else:
                res["status"] = "html_unknown"
        res["requests"] = len(self.log)
        return res

    def collection(self, rest_base: str) -> tuple[list, int]:
        items, page = [], 1
        while page <= 10:
            r = self.get(f"/wp-json/wp/v2/{rest_base}?" + urlencode({"per_page": 100, "page": page}), accept="application/json")
            js = self.json_of(r)
            if not isinstance(js, list):
                break
            items += js
            total = int((r.get("headers") or {}).get("x-wp-totalpages") or 0)
            if (total and page >= total) or (not total and len(js) < 100):
                break
            page += 1
        return items, page

    def record(self, it: dict, ptype: str) -> dict | None:
        if not isinstance(it, dict):
            return None
        name = text(it.get("title"))
        if not name:
            return None
        blob_parts = []
        for k in ("acf", "meta", "fields", "custom_fields", "toolset", "cmb2"):
            if isinstance(it.get(k), (dict, list)):
                blob_parts.append(json.dumps(it[k], ensure_ascii=False))
        content = (it.get("content") or {}).get("rendered", "") if isinstance(it.get("content"), dict) else ""
        blob = " ".join(blob_parts) + " " + content
        flat = text(blob.replace("\\/", "/"))
        site = None
        # 1. a field whose key says website (ACF / meta / toolset), e.g. acf.website, wpcf-parish-url, _parish_website
        for k in ("acf", "meta", "fields", "custom_fields", "toolset", "cmb2"):
            obj = it.get(k)
            stack = [obj]
            while stack and not site:
                o = stack.pop()
                if isinstance(o, dict):
                    for kk, vv in o.items():
                        if isinstance(vv, str) and re.search(r"web|url|site|homepage|link", kk, re.I) \
                                and not re.search(r"facebook|insta|twitter|youtube|map|image|img|photo|logo|bulletin|"
                                                  r"donat|giv|video|stream|calendar|feed|email", kk, re.I):
                            cand = vv.strip()
                            if cand and "://" not in cand and re.match(r"^(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?$", cand, re.I):
                                cand = "https://" + cand
                            h = CF.host_of(cand) if cand.startswith("http") else ""
                            if h and h != CF.host_of(self.base) and not SKIP_SITE.search(cand):
                                site = cand
                                break
                        elif isinstance(vv, (dict, list)):
                            stack.append(vv)
                elif isinstance(o, list):
                    stack += [x for x in o if isinstance(x, (dict, list))]
            if site:
                break
        # 2. a link in the content whose text or label says website, then 3. the first outside link (old rule)
        if not site:
            m = re.search(r"<a[^>]+href=[\"'](https?://[^\"']+)[\"'][^>]*>\s*(?:visit\s+)?(?:(?:parish|church)\s+)?"
                          r"(?:web\s*-?site|home\s*page|sitio web)", content, re.I)
            if m and CF.host_of(m.group(1)) != CF.host_of(self.base) and not SKIP_SITE.search(m.group(1)):
                site = m.group(1)
        for u in ([] if site else URL_RX.findall(blob.replace("\\/", "/"))):
            u = u.rstrip(".,;)\"'\\")
            h = CF.host_of(u)
            if SKIP_SITE.search(u) or h == CF.host_of(self.base) or not h:
                continue
            site = u
            break
        lat = lng = None
        for k in ("lat", "latitude"):
            m = re.search(rf'"{k}"\s*:\s*"?(-?\d{{1,2}}\.\d+)', blob)
            if m:
                lat = float(m.group(1))
        for k in ("lng", "lon", "long", "longitude"):
            m = re.search(rf'"{k}"\s*:\s*"?(-?\d{{1,3}}\.\d+)', blob)
            if m:
                lng = float(m.group(1))
        street = STREET_RX.search(flat)
        zp = ZIP_RX.search(flat[street.end():] if street else flat)
        city = None
        m = re.search(r",\s*([A-Z][A-Za-z .'-]{2,30}),?\s+(?:[A-Z]{2}|[A-Z][a-z]+)\s+\d{5}", flat)
        if m:
            city = m.group(1).strip()
        # parish office phone / e-mail (institutional; Matthew 2026-11-01) only from a field named for the parish or office,
        # never from pastor_/staff_/contact-person fields
        oph = oem = None
        for k in ("acf", "meta", "fields", "custom_fields", "toolset", "cmb2"):
            o = it.get(k)
            if isinstance(o, dict):
                for kk, vv in o.items():
                    if not isinstance(vv, str) or not vv.strip():
                        continue
                    if re.fullmatch(r"(?:wpcf-|_)?(?:parish[_-]?|office[_-]?|main[_-]?|church[_-]?)?(?:phone|telephone|tel)"
                                    r"(?:[_-]?(?:number|1|main))?", kk, re.I) and not oph:
                        oph = vv
                    elif re.fullmatch(r"(?:wpcf-|_)?(?:parish[_-]?|office[_-]?|main[_-]?|church[_-]?)?e-?mail(?:[_-]?address)?",
                                      kk, re.I) and not oem:
                        oem = vv
        try:
            import dirlib as _DL
            oph, oem = _DL.norm_phone(oph), _DL.office_email_ok(oem)
        except Exception:  # noqa: BLE001
            oph = oem = None
        return {"name": name, "type_in_source": ptype, "office_phone": oph, "office_email": oem,
                "address": street.group(0).strip() if street else None, "city": city,
                "zip": zp.group(1) if zp else None, "lat": lat, "lng": lng, "website": site,
                "source_url": it.get("link") or f"{self.base}/wp-json/wp/v2/{ptype}/{it.get('id')}",
                "rest_id": it.get("id"), "extracted_by": "generic WordPress REST reader (fields guessed; verify)"}

    def write_parishes(self, recs: list[dict], ptype: str, rest_base: str) -> None:
        d = OUT / self.slug
        d.mkdir(parents=True, exist_ok=True)
        meta = {"diocese": self.slug, "name": self.see["name"], "source": f"{self.base}/wp-json/wp/v2/{rest_base}",
                "source_label": "diocese-directory-rest", "fetched_at": now_iso(), "post_type": ptype,
                "note": "Read by a generic WordPress REST reader: name from the post title; address, city, zip, coordinates and "
                        "website guessed from the post's custom fields and content. No phones, e-mails or personal names kept. "
                        "Spot-check before treating as the official list.",
                "count": len(recs), "with_website": sum(1 for r in recs if r["website"]), "records": recs}
        (d / "parishes.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", "utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--minutes", type=float, default=0)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--skip-egress-check", action="store_true", help="tests against a local server only")
    a = ap.parse_args()
    sees = [s for s in json.loads((US / "dioceses.json").read_text())["dioceses"] if s.get("website")]
    only = {x for x in a.only.split(",") if x}
    if only:
        sees = [s for s in sees if s["slug"] in only]
    OUT.mkdir(parents=True, exist_ok=True)
    prev = json.loads(STATUS.read_text()) if STATUS.exists() else {"sees": {}}
    if not a.skip_egress_check:
        ok, probes = CF.egress_open()
        if not ok:
            prev.setdefault("egress_log", []).append({"at": now_iso(), "open": False, "probes": probes})
            prev["egress_log"] = prev["egress_log"][-20:]
            STATUS.write_text(json.dumps(prev, indent=1, ensure_ascii=False) + "\n")
            print("EGRESS CLOSED: this machine cannot reach ordinary websites (proxy refusal). Nothing probed; re-run later.")
            return 3
    # "error" is our own crash, not a fact about the site: always retry it.
    todo = [s for s in sees if a.refresh or prev["sees"].get(s["slug"], {}).get("status") in (None, "egress_closed", "error")]
    print(f"{len(sees)} sees, {len(todo)} to probe")
    pol = CF.Polite()
    t0 = time.time()

    def one(s):
        if a.minutes and time.time() - t0 > a.minutes * 60:
            return None
        try:
            r = Probe(s, pol, a.refresh).run()
        except Exception as e:  # noqa: BLE001 - one odd site must not stop the pass
            r = {"slug": s["slug"], "name": s["name"], "website": s["website"], "status": "error", "error": repr(e)[:200],
                 "checked_at": now_iso()}
        print(f"  {s['slug']:60s} {r['status']}", flush=True)
        return r

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(one, todo):
            if r:
                prev["sees"][r["slug"]] = r
    prev["generated_at"] = now_iso()
    prev["method"] = "ingest/us/dioceses/directory_probe.py (see its docstring)"
    prev["user_agent"] = CF.UA
    prev["counts"] = dict(Counter(v.get("status") for v in prev["sees"].values()).most_common())
    STATUS.write_text(json.dumps(prev, indent=1, ensure_ascii=False) + "\n")
    print(prev["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
