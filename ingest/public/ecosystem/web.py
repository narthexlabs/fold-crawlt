"""Cached, polite page fetcher for the ecosystem scripts.

Raw HTML lands in data/raw/ecosystem/web/ (gitignored); an index records the URL, HTTP status and fetch time so
every record built from a page can carry source_url + fetched_at. One honest User-Agent (from common.py), no
retries around blocks: a 403/429 is recorded and the page is skipped.

    python3 -I ingest/public/ecosystem/web.py URL [URL ...]     # fetch and print a text preview
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, get, now_iso  # noqa: E402

WEB = RAW / "ecosystem" / "web"
INDEX = WEB / "index.json"
DELAY = 1.0
_last = [0.0]


def _key(url: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", url.lower().split("://", 1)[-1])[:80].strip("-")
    return f"{base}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def _index() -> dict:
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return {}


def fetch(url: str, refresh: bool = False, timeout: int = 30) -> dict:
    """Return {url, final_url, status, fetched_at, html, path}. Uses the cache unless refresh=True."""
    WEB.mkdir(parents=True, exist_ok=True)
    idx = _index()
    k = _key(url)
    path = WEB / f"{k}.html"
    if not refresh and k in idx and (idx[k]["status"] != 200 or path.exists()):
        meta = idx[k]
        html = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        return {**meta, "html": html, "path": str(path)}
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = get(url, timeout=timeout)
    _last[0] = time.time()
    meta = {"url": url, "final_url": getattr(r, "url", url), "status": getattr(r, "status_code", None),
            "fetched_at": now_iso()}
    html = ""
    if r is not None and r.ok:
        html = r.text
        path.write_text(html, encoding="utf-8")
    idx = _index()
    idx[k] = meta
    INDEX.write_text(json.dumps(idx, indent=1), encoding="utf-8")
    return {**meta, "html": html, "path": str(path)}


def text_of(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return re.sub(r"\n\s*\n+", "\n", soup.get_text("\n", strip=True))


def links_of(html: str, base: str) -> list[tuple[str, str]]:
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        out.append((re.sub(r"\s+", " ", a.get_text(" ", strip=True)), urljoin(base, a["href"])))
    return out


if __name__ == "__main__":
    for u in sys.argv[1:]:
        p = fetch(u)
        print("=" * 20, u, p["status"], p.get("final_url"))
        print(text_of(p["html"])[:4000] if p["html"] else "(no body)")
