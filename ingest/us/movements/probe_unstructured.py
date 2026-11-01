"""Record what the Society of St Vincent de Paul (source 2) and the Legion of Mary (source 3) publish nationally.

Neither has a public, structured directory of parish-based units, so this module only fetches each candidate page
once (robots.txt honoured), records status and what was found, and writes ssvp.json / legion_of_mary.json with zero
records. Nothing goes into by_parish.json. Re-run to re-check:

    python3 -I ingest/us/movements/probe_unstructured.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import common as c  # noqa: E402
import common_fetch as cf  # noqa: E402

SOURCES = {
    "ssvp": {
        "source": "Society of St. Vincent de Paul USA (National Council)",
        "pages": ["https://ssvpusa.org/", "https://ssvpusa.org/volunteer/", "https://members.ssvpusa.org/",
                  "https://ssvpusa.org/about-us/organizational-structure/"],
        "finding": ("No public conference finder. ssvpusa.org's 'volunteer' page is a contact form (ZIP code field) that "
                    "routes a person to a local council by e-mail; members.ssvpusa.org is the member portal; the only "
                    "public locator is thriftstores.ssvpusa.org/store-locator (thrift stores = entities, not parish "
                    "conferences). robots.txt: 'User-agent: *' Allow / with Content-Signal search=yes, ai-train=no, "
                    "use=reference (named AI-training bots disallowed). Conferences are parish-based, so the realistic "
                    "path is diocesan/district council sites (e.g. svdp<city>.org 'find a conference' pages) one by one."),
    },
    "legion_of_mary": {
        "source": "Legion of Mary (Concilium Legionis Mariae and US senatus sites)",
        "pages": ["https://www.legionofmary.ie/", "https://www.legionofmary.org/"],
        "finding": ("No national list of praesidia. legionofmary.ie (Concilium, Dublin) publishes no unit directory; "
                    "legionofmary.org now serves unrelated (gambling) content and is not used. US senatus/regia sites "
                    "publish contact forms, not structured praesidium lists. Skipped as instructed; parish sites and "
                    "bulletins remain the source for Legion of Mary groups."),
    },
}


def main() -> None:
    pol = cf.Polite()
    for key, s in SOURCES.items():
        checks = []
        for u in s["pages"]:
            rb = pol.robots_for(u)
            r = pol.get(u)
            body = r["body"].decode("utf-8", "replace") if r.get("body") else ""
            t = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
            checks.append({"url": u, "robots_status": rb["status"], "allowed": pol.allowed(u), "http_status": r["status"],
                           "class": r.get("class"), "final_url": r.get("final_url"),
                           "title": re.sub(r"\s+", " ", t.group(1)).strip()[:120] if t else None})
        c.save(c.OUT / f"{key}.json", {"source": s["source"], "user_agent": c.UA, "fetched_at": c.now_iso(),
                                        "status": "no structured public directory", "finding": s["finding"],
                                        "checks": checks, "count": 0, "records": []})
        print(key, [(x["url"], x["http_status"], x["class"]) for x in checks])


if __name__ == "__main__":
    main()
