"""Diocese pass for the YouTube catalog: read each diocese's home page and its cathedral's ("mother church") site for
YouTube links (Matthew, 2026-11-02: that is where a diocese's main video content is hosted). No youtube.com request is
made here; the links found go into the catalog's collect step (ingest/us/media/run.py collect for the national run,
ingest/california/media_collect.py for California), and the runner job `media` resolves and verifies them.

    python3 -I ingest/us/media/diocese_pages.py          # -> data/real/us/media/_inputs/diocese_pages.json

Targets
  * every see in data/real/us/dioceses.json (USCCB) except Oakland (its feed is data/real/media/): the `website`;
  * its cathedral: crawled parish sites (data/real/us/sites/<see>/*.json, then unassigned-<state> in the see city) whose
    name says Cathedral (Co-Cathedral too, at most two), then Wikidata (url_sources/wikidata.json) by diocese label; for
    California the cathedral named in data/real/california/dioceses.json, matched to the parish index by name.
Per host: robots.txt read and honoured (Disallow and Crawl-delay), the honest FindTheChurch user agent
(ingest/california/sites/fetch_site.py http_get), one request at a time, at least 1.5 s apart (or the Crawl-delay), the home
page plus at most ONE same-site page whose link reads as video / livestream / media (none when Crawl-delay > 20 s).
A challenge / 403 / 429 is recorded and never retried harder or worked around. Page bodies are not kept: only the
YouTube links, where they were found, and the status.
"""
from __future__ import annotations

import collections
import json
import re
import sys
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
sys.path.insert(0, str(ROOT / "ingest" / "california"))
import fetch_site as fs  # noqa: E402
from media_common import extract_yt, fold  # noqa: E402

US = ROOT / "data" / "real" / "us"
CA = ROOT / "data" / "real" / "california"
OUT = US / "media" / "_inputs" / "diocese_pages.json"
MEDIA_LINK = re.compile(r"live\s*-?stream|livestream|watch|video|media|youtube|mass\s+online|online\s+mass|broadcast|tv\b", re.I)
CATH = re.compile(r"\b(co-?)?cathedral\b|\bcatedral\b", re.I)
GAP = 1.5


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def host(u: str | None) -> str:
    try:
        return (urlparse(u or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def ca_slug_for(see: dict, ca: dict) -> str | None:
    if see.get("see_state") != "CA":
        return None
    for s, d in ca.items():
        if fold(d["see_city"]) == fold(see.get("see_city")) or fold(d["short_name"]) in fold(see["name"]):
            return s
    return None


def targets() -> list[dict]:
    sees = json.loads((US / "dioceses.json").read_text())["dioceses"]
    ca = {d["slug"]: d for d in json.loads((CA / "dioceses.json").read_text())["dioceses"]}
    site_cath: dict[str, list[dict]] = collections.defaultdict(list)
    for f in sorted((US / "sites").glob("*/*.json")):
        if f.name in ("summary.json", "progress.json"):
            continue
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(d, dict) or not d.get("domain"):
            continue
        names = [n for n in d.get("names") or [] if n]
        hit = next((n for n in names if CATH.search(n) and "cathedral city" not in n.lower()), None)
        if hit:
            site_cath[f.parent.name].append({"name": hit, "url": d.get("home_url") or f"https://{d['domain']}/", "domain": d["domain"],
                                             "city": (d.get("cities") or [None])[0], "co": bool(re.search(r"co-?cathedral", hit, re.I)),
                                             "source": f"data/real/us/sites/{f.parent.name}/{f.name}"})
    wd = json.loads((US / "url_sources" / "wikidata.json").read_text())["records"]
    ca_idx_f = CA / "media" / "_inputs" / "parish_index.json"
    ca_idx = json.loads(ca_idx_f.read_text())["parishes"] if ca_idx_f.exists() else []
    out = []
    for s in sees:
        if s["slug"] == "diocese-of-oakland":
            continue
        cs = ca_slug_for(s, ca)
        if s.get("website"):
            out.append({"diocese": s["slug"], "ca_slug": cs, "kind": "diocese_home", "name": s["name"], "url": s["website"],
                        "source": "data/real/us/dioceses.json (USCCB)"})
        cands = []
        if cs:
            cname = (ca[cs].get("cathedral") or {}).get("name")
            if cname:
                generic = {"of", "the", "st", "saint", "s", "cathedral", "basilica", "our", "lady", "holy", "blessed", "mary", "christ"}
                ct = set(re.findall(r"[a-z]+", fold(cname))) - generic
                for p in ca_idx:
                    pt = set(re.findall(r"[a-z]+", fold(p.get("name"))))
                    fits = (ct <= pt) if ct else fold(cname) in fold(p.get("name"))
                    if p["diocese"] == cs and p.get("website") and CATH.search(p.get("name") or "") and fits:
                        cands.append({"name": p["name"], "url": p["website"], "co": False, "ca_parish_id": p["id"],
                                      "source": "data/real/california/dioceses.json cathedral + media/_inputs/parish_index.json"})
                        break
        else:
            own = sorted(site_cath.get(s["slug"], []), key=lambda c: c["co"])
            if not own:
                st = s.get("see_state") or ""
                own = [c for c in site_cath.get(f"unassigned-{st.lower()}", []) if fold(c.get("city")) == fold(s.get("see_city"))]
            cands = own[:2]
            if not cands:
                w = next((r for r in wd if r.get("website") and CATH.search(r.get("name") or "") and
                          fold(r.get("diocese") or "") == fold(s["name"])), None)
                if w:
                    cands = [{"name": w["name"], "url": w["website"], "co": False, "source": w["source_url"]}]
        for c in cands:
            if host(c["url"]) and host(c["url"]) != host(s.get("website")) or not s.get("website"):
                out.append({"diocese": s["slug"], "ca_slug": cs, "kind": "cathedral", **c})
            else:
                out.append({"diocese": s["slug"], "ca_slug": cs, "kind": "cathedral", **c, "same_host_as_diocese": True})
    return out


class HostWorker:
    def __init__(self, h: str):
        self.h, self.last, self.rp, self.delay = h, 0.0, None, GAP

    def wait(self):
        g = time.monotonic() - self.last
        if g < self.delay:
            time.sleep(self.delay - g)
        self.last = time.monotonic()

    def robots(self, base: str):
        self.rp = urllib.robotparser.RobotFileParser()
        r = fs.http_get(urljoin(base, "/robots.txt"))
        self.last = time.monotonic()
        if r.get("status") == 200 and not r.get("err"):
            self.rp.parse(r["body"].decode("utf-8", "ignore").splitlines())
            cd = self.rp.crawl_delay(fs.USER_AGENT) or self.rp.crawl_delay("*")
            if cd:
                self.delay = max(GAP, float(cd))
        else:
            self.rp.parse([])

    def ok(self, url: str) -> bool:
        return self.rp.can_fetch(fs.USER_AGENT, url) if self.rp else True


def read_host(h: str, tlist: list[dict]) -> list[dict]:
    w = HostWorker(h)
    first = tlist[0]["url"]
    w.robots(first)
    done = []
    for t in tlist:
        rec = {**t, "pages": [], "yt_links": [], "status": None, "fetched_at": now_iso()}
        url = t["url"] if "://" in t["url"] else "https://" + t["url"]
        if not w.ok(url):
            rec["status"] = "robots_disallowed"
            done.append(rec)
            continue
        w.wait()
        r = fs.http_get(url)
        st = fs.classify(r)
        rec["status"], rec["final_url"] = st, r.get("final_url")
        rec["pages"].append({"url": url, "status": st})
        if st == "ok":
            body = r["body"].decode("utf-8", "ignore")
            for l in extract_yt(body):
                rec["yt_links"].append({**{k: l[k] for k in ("type", "key", "url", "live_hint")}, "found_on": r.get("final_url") or url})
            if w.delay <= 20:
                scan = fs.scan_html(r["body"])
                base = r.get("final_url") or url
                nxt = None
                for a in scan.links:
                    href, text = a.get("href") or "", (a.get("text") or "") + " " + (a.get("label") or "")
                    if href.startswith(("mailto:", "tel:", "javascript:")):
                        continue
                    u = urljoin(base, href)
                    if host(u) == host(base) and (MEDIA_LINK.search(text or "") or MEDIA_LINK.search(urlparse(u).path)) and u.rstrip("/") != base.rstrip("/"):
                        nxt = u.split("#")[0]
                        break
                if nxt and w.ok(nxt):
                    w.wait()
                    r2 = fs.http_get(nxt)
                    st2 = fs.classify(r2)
                    rec["pages"].append({"url": nxt, "status": st2})
                    if st2 == "ok":
                        seen = {(l["type"], l["key"]) for l in rec["yt_links"]}
                        for l in extract_yt(r2["body"].decode("utf-8", "ignore")):
                            if (l["type"], l["key"]) not in seen:
                                rec["yt_links"].append({**{k: l[k] for k in ("type", "key", "url", "live_hint")}, "found_on": r2.get("final_url") or nxt})
        done.append(rec)
    return done


def main() -> None:
    ts = targets()
    by_host: dict[str, list[dict]] = collections.defaultdict(list)
    for t in ts:
        by_host[host(t["url"] if "://" in t["url"] else "https://" + t["url"])].append(t)
    print(f"{len(ts)} targets on {len(by_host)} hosts", flush=True)
    res = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for out in ex.map(lambda kv: read_host(*kv), by_host.items()):
            res += out
    res.sort(key=lambda r: (r["diocese"], r["kind"]))
    counts = {"targets": len(res), "hosts": len(by_host),
              "by_kind": dict(collections.Counter(r["kind"] for r in res)),
              "by_status": dict(collections.Counter(r["status"] for r in res)),
              "with_youtube_links": sum(1 for r in res if r["yt_links"]),
              "youtube_links": sum(len(r["yt_links"]) for r in res),
              "dioceses_with_a_link": len({r["diocese"] for r in res if r["yt_links"]})}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": now_iso(), "generated_by": "ingest/us/media/diocese_pages.py",
                               "user_agent": fs.USER_AGENT, "counts": counts, "targets": res}, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(counts, indent=1), flush=True)


if __name__ == "__main__":
    main()
