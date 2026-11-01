"""Shared polite-fetch helpers for the national (lane G) collectors: robots.txt, per-host pacing, egress check.

The HTTP call and the response classes are imported at run time from ingest/california/sites/fetch_site.py (honest
FindTheChurch user agent, TLS verified, challenge / parked / blocked detection), so the national crawl classifies
exactly as California does. Nothing here solves, retries or works around a challenge, a 403/429 or a proxy refusal.

`egress_open()` answers one question before a chunk starts: can this machine reach ordinary websites at all? The
sandbox's egress proxy sometimes refuses every host (CONNECT 403). That is a property of our connection, not of the
parish site, so a chunk that finds egress closed records the fact in progress.json and stops, instead of writing
thousands of sites down as `proxy_denied`.
"""
from __future__ import annotations

import sys
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
CA_SITES = ROOT / "ingest" / "california" / "sites"
if str(CA_SITES) not in sys.path:
    sys.path.insert(0, str(CA_SITES))
import fetch_site as fs  # noqa: E402  (lane A / California code, imported, never copied)

UA = fs.USER_AGENT
EGRESS_FAIL = {"proxy_denied", "unreachable_via_proxy"}
# Hosts used only to tell "our egress is closed" from "this site is down": robots.txt of large, always-up sites.
EGRESS_PROBES = ["https://www.usccb.org/robots.txt", "https://www.vatican.va/robots.txt", "https://en.wikipedia.org/robots.txt"]


def host_of(u: str) -> str:
    return fs.host_of(u)


def egress_open() -> tuple[bool, list[dict]]:
    """True when at least one probe host answers (any HTTP status). One request per probe host."""
    out = []
    for u in EGRESS_PROBES:
        r = fs.http_get(u, timeout=12, max_bytes=20000)
        out.append({"url": u, "status": r["status"], "err": r["err"]})
        if r["status"]:
            return True, out
    return False, out


def https_only(url: str) -> str:
    """The sandbox has no DNS and no plain-HTTP egress: only HTTPS through its proxy. A `http://` URL (USCCB lists most
    diocese sites that way) fails name resolution here and would be misread as a dead site, so fetch it over HTTPS."""
    return "https://" + url[len("http://"):] if url.lower().startswith("http://") else url


class Polite:
    """robots.txt per host (cached for the process), Crawl-delay honoured (floor 1.05 s, cap 30 s), one request at a
    time per host across threads."""

    def __init__(self, floor: float = 1.05, cap: float = 30.0):
        self.floor, self.cap = floor, cap
        self.pacer = fs.Pacer(floor)
        self.robots: dict[str, dict] = {}
        self.host_locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()
        self.n = 0

    def _hlock(self, host: str) -> threading.Lock:
        with self.lock:
            return self.host_locks.setdefault(host, threading.Lock())

    def robots_for(self, url: str) -> dict:
        url = https_only(url)
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        h = host_of(url)
        if h in self.robots:
            return self.robots[h]
        with self._hlock(h):
            self.pacer.wait(h, self.floor)
            r = fs.http_get(base + "/robots.txt", timeout=15, max_bytes=300000)
            self.n += 1
        cls = fs.classify(r)
        rec = {"url": base + "/robots.txt", "status": r["status"], "class": cls, "rp": None, "delay": self.floor}
        if r["status"] == 200 and r["body"] and b"<html" not in r["body"][:600].lower():
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(r["body"].decode("utf-8", "replace").splitlines())
            rec["rp"] = rp
            cd = rp.crawl_delay(UA) or rp.crawl_delay("*")
            rec["delay"] = min(max(self.floor, float(cd or 0) + (0.05 if cd else 0)), self.cap)
            rec["crawl_delay"] = cd
        self.robots[h] = rec
        return rec

    def allowed(self, url: str) -> bool:
        rp = self.robots_for(url)["rp"]
        return rp is None or rp.can_fetch(UA, url)

    def get(self, url: str, accept: str | None = None, timeout: int = 25, max_bytes: int = 4_000_000) -> dict:
        """dict from fetch_site.http_get plus `class`; robots_disallowed answered without a request."""
        url = https_only(url)
        rb = self.robots_for(url)
        if rb["class"] in {"challenge"} | EGRESS_FAIL:
            return {"url": url, "status": None, "body": b"", "err": rb["class"], "headers": {}, "ctype": "", "final_url": url,
                    "class": rb["class"]}
        if not self.allowed(url):
            return {"url": url, "status": None, "body": b"", "err": None, "headers": {}, "ctype": "", "final_url": url,
                    "class": "robots_disallowed"}
        h = host_of(url)
        with self._hlock(h):
            self.pacer.wait(h, rb["delay"])
            r = fs.http_get(url, accept=accept, timeout=timeout, max_bytes=max_bytes)
            self.n += 1
        r["class"] = fs.classify(r)
        return r


def sleep_until(t: float) -> None:
    while time.time() < t:
        time.sleep(min(5, t - time.time()))
