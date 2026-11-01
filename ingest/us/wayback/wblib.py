"""Wayback Machine client shared by the two recoverers (dioceses.py, sites.py). Python 3 + requests (via fetch_site).

web.archive.org is unreachable from the project sandbox but reachable from GitHub Actions runners. Rules kept here:
  * ONE client, one request at a time, >= 1.1 s between request starts (CDX and capture alike), honest FindTheChurch UA
    (fetch_site.http_get). web.archive.org/robots.txt is a 404. Archived copies are read, nothing on a live parish host.
  * 429 -> cool-down; 5xx / timeout -> two retries with a pause; WbStop after 8 failed requests in a row (the archive is
    refusing us: the run ends cleanly and resumes next time). Nothing is retried harder.
  * Captures are fetched as raw `id_` content at the exact CDX timestamp.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ingest" / "california" / "sites"))
import fetch_site as fs  # noqa: E402

CDX = "https://web.archive.org/cdx/search/cdx"
PACE = 1.1
SAFE = ":*()|.?^$/,\\"


class WbStop(Exception):
    """The archive keeps refusing us; end the run (state is saved per item)."""


def norm(url: str) -> str:
    u = urlparse(url if "://" in url else "http://" + url)
    host = u.netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"/index\.(?:html?|php|aspx?)$", "/", u.path or "/")
    path = path.rstrip("/") or "/"
    return host + path + (("?" + u.query) if u.query else "")


def iso_of(ts: str) -> str:
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}+00:00"


def date_of(ts: str) -> str:
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"


def archive_url(ts: str, original: str) -> str:
    return f"https://web.archive.org/web/{ts}/{original}"


class Archive:
    def __init__(self, pace: float = PACE):
        self.pace, self.last, self.lock = pace, 0.0, threading.Lock()
        self.fail_run = 0
        self.n = 0
        self.log: list[str] = []

    def _once(self, url: str, timeout: int, max_bytes: int) -> dict:
        with self.lock:
            wait = self.last + self.pace - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
        self.n += 1
        return fs.http_get(url, timeout=timeout, max_bytes=max_bytes)

    def get(self, url: str, timeout: int = 60, max_bytes: int = 3_000_000) -> dict:
        r = {}
        for attempt in range(3):
            r = self._once(url, timeout, max_bytes)
            st = r.get("status")
            if st == 429:
                self.log.append(f"429 cooldown {url[:90]}")
                time.sleep(90)
                continue
            if r.get("err") or (st and st >= 500):
                time.sleep(20 * (attempt + 1))
                continue
            break
        bad = bool(r.get("err")) or r.get("status") in (429, 502, 503, 504) or (r.get("status") or 0) >= 500
        self.fail_run = self.fail_run + 1 if bad else 0
        if self.fail_run >= 8:
            raise WbStop(f"8 failed requests in a row; last {r.get('status')} {r.get('err')}")
        return r

    def capture(self, ts: str, original: str) -> dict:
        return self.get(f"https://web.archive.org/web/{ts}id_/{original}", timeout=60)


class Index:
    """The newest 200/html capture per normalised URL of one site (one CDX request, cached on disk)."""

    def __init__(self, arch: Archive, cache: Path, query: str, match: str = "domain", regex: str | None = None,
                 since: str = "2023", limit: int = 3000):
        self.arch, self.cache = arch, cache
        self.map: dict[str, tuple[str, str]] = {}
        self.ok = False
        if cache.exists():
            try:
                d = json.loads(cache.read_text())
                self.map = {k: tuple(v) for k, v in d["map"].items()}
                self.ok = True
                return
            except (ValueError, KeyError):
                pass
        base = {"url": query, "matchType": match, "output": "json", "fl": "timestamp,original", "from": since,
                "collapse": "urlkey", "sort": "reverse", "limit": limit}
        rows = None
        tries = [regex] if regex else []
        tries.append(None)
        for rx in tries:
            p = dict(base)
            p["filter"] = ["statuscode:200", "mimetype:text/html"]
            qs = [("url", p["url"])] + [(k, v) for k, v in p.items() if k not in ("url", "filter")]
            qs += [("filter", f) for f in p["filter"]] + ([("filter", f"original:{rx}")] if rx else [])
            rows = self._pairs(qs)
            if rows:
                break
        if rows is None:
            return
        self.ok = True
        for ts, orig in rows:
            k = norm(orig)
            if k not in self.map or ts > self.map[k][0]:
                self.map[k] = (ts, orig)
        self._save()

    def _pairs(self, qs):
        q = "&".join(f"{k}={quote(str(v), safe=SAFE)}" for k, v in qs)
        r = self.arch.get(f"{CDX}?{q}", timeout=90, max_bytes=6_000_000)
        if r.get("status") != 200:
            return None
        try:
            rows = json.loads(r["body"].decode("utf-8", "replace") or "[]")
        except ValueError:
            return None
        if rows and rows[0][:2] == ["timestamp", "original"]:
            rows = rows[1:]
        return [(x[0], x[1]) for x in rows if len(x) >= 2]

    def _save(self):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps({"map": self.map}, ensure_ascii=False))

    def add_home(self, home_url: str) -> None:
        """One extra exact lookup when the (possibly truncated) listing lacks the home page."""
        k = norm(home_url)
        if k in self.map:
            return
        host = k.split("/")[0]
        rows = self._pairs([("url", host + "/"), ("output", "json"), ("fl", "timestamp,original"), ("from", "2023"),
                            ("filter", "statuscode:200"), ("filter", "mimetype:text/html"), ("sort", "reverse"),
                            ("limit", 1)])
        for ts, orig in rows or []:
            self.map[norm(orig)] = (ts, orig)
        self._save()

    def lookup(self, url: str):
        return self.map.get(norm(url.split("#")[0]))
