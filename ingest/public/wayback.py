"""Wayback Machine (Internet Archive) captures of parish sites we cannot read directly.

    python3 -I ingest/public/wayback.py [--ids a,b]  -> data/raw/wayback/captures.json

From a host that cannot reach web.archive.org (this sandbox) only step 1's fallback works. To read capture content,
run the stdlib twin ingest/public/wayback_home.py on a home connection and then ingest/public/wayback_build.py
(data/real/README-recovery.md, section 2).

Plan (two steps):
  1. List captures. Preferred: the CDX API on web.archive.org
     (cdx/search/cdx?url=<domain>/*&output=json&filter=statuscode:200&from=2026). Fallback when web.archive.org cannot
     be reached: the Availability API on archive.org (wayback/available?url=<url>&timestamp=<YYYYMMDD>), which returns
     the capture closest to a timestamp. Asked with today's date it gives the latest capture of that URL; asked with
     earlier dates it walks back through a dead domain's history (one capture per probe).
  2. Fetch the capture with the id_ flag (web.archive.org/web/<ts>id_/<url>) for raw content. This needs
     web.archive.org; when that host is unreachable the capture is recorded (date, archive URL) but not read.

Rules: plain default User-Agent; at most one request per second to archive.org hosts (robots.txt of archive.org
allows /wayback/; web.archive.org's robots.txt is read first when reachable). Rows built from a capture carry source
`wayback-<capture date>` and the archive URL as source_url.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RAW, now_iso  # noqa: E402

OUT = RAW / "wayback"
SESSION = requests.Session()  # default python-requests User-Agent
PACE = 1.1
_last = [0.0]

# entity id -> URLs to look up (home, Mass times, bulletin page) as recorded in parish_sites.json / the fill JSON
URLS = {
    "christ-light-oakland": ["ctlcathedral.org/", "ctlcathedral.org/mass", "ctlcathedral.org/confession-reconciliation",
                             "ctlcathedral.org/bulletins"],
    "most-holy-rosary-antioch": ["holyrosaryantioch.org/", "holyrosaryantioch.org/mass", "holyrosaryantioch.org/bulletins"],
    "anne-union-city": ["saintannecatholic.org/", "saintannecatholic.org/mass-schedule", "saintannecatholic.org/bulletins"],
    "benedict-oakland": ["saintbenedictoakland.com/", "saintbenedictoakland.com/mass-times",
                         "saintbenedictoakland.com/bulletins"],
    "columba-oakland": ["stcolumba-oak.com/", "stcolumba-oak.com/mass-times", "stcolumba-oak.com/bulletins"],
    "leo-great-oakland": ["ctlcathedral.org/st-leo", "stleothegreat.org/"],
    "louis-bertrand-oakland": ["slboakland.com/", "slboakland.com/mass-times", "slboakland.com/bulletins"],
    "mark-richmond": ["stmarkrichmond.org/", "stmarkrichmond.org/sacraments", "stmarkrichmond.org/jf"],
    "perpetua-lafayette": ["stperpetua.org/", "stperpetua.org/mass-times-directions", "stperpetua.org/timely-perpetuan"],
    "anthony-mary-help-christians-oakland": ["stanthonymaryhelp.org/", "stanthony-maryhelp.org/"],
    "our-lady-mercy-richmond": ["pointrichmondcatholic.org/", "pointrichmondcatholic.org/masses/"],
    "augustine-oakland": ["staugustineoakland.com/", "staugustineoakland.com/mass-schedule/",
                          "staugustineoakland.com/bul/"],
    "jarlath-oakland": ["saintjarlath.org/", "saintjarlath.org/sacraments/"],
    "good-shepherd-pittsburg": ["goodshepherdpittsburg.org/"],
    "divine-mercy-oakland": ["divinemercyoak.org/"],
    "raymond-dublin": ["st-raymond-dublin.org/"],
    "anthony-oakley": ["stanthonyoakley.org/"],
    "cornelius-richmond": ["stcorneliusrichmond.org/"],
    "our-lady-lourdes-oakland": ["lourdesoakland.com/"],
    "peter-martyr-pittsburg": ["saintpetermartyr.org/", "saintpetermartyr.org/horarios-contacto/"],
    "rose-lima-patrick-crockett": ["strosecrockett.org/"],
    "bernard-oakland": ["oakdiocese.org/diocese/parish-directory/st.-bernard-church"],
}
# dead / hijacked / unreachable domains: walk back through history to find the last capture that was a parish site
HISTORY = {"our-lady-lourdes-oakland", "anthony-oakley", "cornelius-richmond", "divine-mercy-oakland",
           "good-shepherd-pittsburg", "raymond-dublin", "anthony-mary-help-christians-oakland"}
WALK = ["20261031", "20260601", "20260101", "20250601", "20250101", "20240101", "20230101", "20220101", "20200101"]


def _get(url: str, timeout: int = 40):
    w = _last[0] + PACE - time.time()
    if w > 0:
        time.sleep(w)
    _last[0] = time.time()
    try:
        return SESSION.get(url, timeout=timeout), None
    except requests.RequestException as e:
        return None, type(e).__name__


def cdx_reachable() -> dict:
    r, err = _get("https://web.archive.org/robots.txt", timeout=30)
    return {"url": "https://web.archive.org/robots.txt", "ok": bool(r is not None and r.ok),
            "status": r.status_code if r is not None else None, "error": err, "checked_at": now_iso()}


def available(url: str, ts: str) -> dict:
    q = f"https://archive.org/wayback/available?url={url}&timestamp={ts}"
    r, err = _get(q)
    out = {"query": q, "asked_timestamp": ts, "checked_at": now_iso()}
    if r is None or not r.ok:
        out["error"] = err or f"HTTP {r.status_code}"
        return out
    c = (r.json().get("archived_snapshots") or {}).get("closest")
    if c:
        t = c["timestamp"]
        out.update(capture=t, capture_date=f"{t[:4]}-{t[4:6]}-{t[6:8]}", status=c.get("status"), archive_url=c["url"],
                   raw_url=c["url"].replace(f"/web/{t}/", f"/web/{t}id_/"))
    else:
        out["capture"] = None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    ids = [x for x in a.ids.split(",") if x] or list(URLS)
    today = date.today().strftime("%Y%m%d")
    res = {"generated_at": now_iso(), "web_archive_org": cdx_reachable(), "parishes": {}}
    print("web.archive.org reachable:", res["web_archive_org"])
    for e in ids:
        rec = {"latest": [], "history": []}
        for u in URLS[e]:
            rec["latest"].append(dict(available(u, today), url=u))
        if e in HISTORY:
            seen = set()
            for ts in WALK[1:]:
                h = available(URLS[e][0], ts)
                if h.get("capture") and h["capture"] not in seen:
                    seen.add(h["capture"])
                    rec["history"].append(h)
        res["parishes"][e] = rec
        lat = [x.get("capture_date") for x in rec["latest"]]
        print(f"{e:40s} latest={lat} history={[h['capture_date'] for h in rec['history']]}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "captures.json").write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
