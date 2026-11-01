#!/usr/bin/env python3
"""Polite, resumable fetch of parish websites (python standard library only).

This one file runs in the sandbox AND on a home laptop (python3.10, stdlib only), so a site that refuses the
sandbox's datacenter IP can be fetched again from a residential connection with the same code. It only fetches and
caches; parsing happens in parse_site.py (bs4, sandbox).

    python3 fetch_site.py --input sites.json --raw-root RAW --workers 8 --max-seconds 140 [--retry-failed]

`sites.json`: [{"key": "stjohn.org", "home": "https://www.stjohn.org/"}, ...]
Writes RAW/<key>/manifest.json plus the page bodies (RAW/<key>/NN_<path>.html, RAW/<key>/feeds/<sha>.body).
A rerun skips every URL already cached for that site, so a run cut by --max-seconds simply continues next time.

Rules (CLAUDE.md, docs/design/scaling-california.md)
  * at most 1 request per second per host (robots.txt Crawl-delay honoured, capped at 30 s), up to --workers
    different hosts at once; robots.txt Disallow honoured when the file is readable (a 401/403/5xx robots.txt is
    treated as "unreadable", never as a licence to skip the rules for pages that are not blocked);
  * the HTTP library's plain default User-Agent (python-requests where installed, else urllib's), no cookies, no JavaScript, nothing spoofed;
  * a challenge page (Cloudflare "Just a moment...", Sucuri, captcha wall) is recorded as `challenge` and never
    retried or solved; a 403/429 is recorded as blocked_403/blocked_429 and left for the laptop pass or a person;
  * TLS is always verified (a certificate failure is recorded as ssl_error).

Per-site plan (at most MAX_PAGES pages): robots.txt, home, then the best same-site link for each of
mass times / ministries / bulletin / calendar / livestream / contact (see CATS), plus per-CMS fast paths:
  eCatholic  -> guessed /mass-times, /ministries, /bulletins when the nav has no such link
  WordPress  -> /wp-json/wp/v2/pages index (one request) to find those pages when the nav hides them, and a probe
                of The Events Calendar REST route (cached for the next 90 days when it answers).
Same-site .ics links are cached too. Third-party feeds (Google Calendar) are read by the sandbox later.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import io
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse

TIMEOUT = 20
MAX_BYTES = 2_500_000
MAX_PAGES = 16          # pages per site, robots.txt and feed requests not counted
SITE_SECONDS = 150      # wall-clock cap per site per run (resumes from cache)
CRAWL_DELAY_CAP = 30.0
FEED_DAYS = 90
# Honest, identifying crawler name (owner approved 2026-10-31). Not a browser string; sites can see who we are and block us.
USER_AGENT = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------------------------------ pacing
class Pacer:
    """Reserve request slots per host: consecutive slots at least `delay` seconds apart, across threads."""

    def __init__(self, delay: float = 1.0):
        self.delay, self.next, self.lock = delay, {}, threading.Lock()

    def wait(self, host: str, delay: float | None = None) -> None:
        d = max(self.delay, delay or 0.0)
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next.get(host, 0.0))
            self.next[host] = slot + d
        if slot > now:
            time.sleep(slot - now)


# ------------------------------------------------------------------------------------------------ http
CHALLENGE_STRONG = re.compile(
    rb"just a moment\.\.\.|cf-chl|challenge-platform|cf_chl_opt|attention required! \| cloudflare|sucuri_cloudproxy|"
    rb"sucuri website firewall|checking your browser before accessing|_incapsula_resource|incapsula incident|"
    rb"px-captcha|enable javascript and cookies to continue|please enable cookies|ddos protection by|"
    rb"verify you are human|are you a robot|\"cf-turnstile\"", re.I)
PARKED = re.compile(
    rb"domain (?:name )?(?:may be|is) for sale|buy this domain|this domain is for sale|domain has expired|"
    rb"hugedomains|sedoparking|parkingcrew|bodis\.com|this web page is parked|parked free|namebright|"
    rb"related searches|click here to renew|/lander\b|afternic|dan\.com/buy", re.I)


class _Follow308(urllib.request.HTTPRedirectHandler):
    """Python 3.10's urllib does not follow 308 (permanent redirect, common behind CDNs); 3.11+ and requests do."""

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_Follow308)


def classify_net_error(e: BaseException) -> str:
    r = getattr(e, "reason", e)
    s = str(r)
    if isinstance(r, ssl.SSLError) or "CERTIFICATE_VERIFY" in s or "SSL" in s.upper() and "handshake" in s.lower():
        return "ssl_error"
    if "Tunnel connection failed" in s:
        code = s.split("failed:")[-1].strip()[:3]
        return "proxy_denied" if code in ("403", "407") else "unreachable_via_proxy"   # 502 = DNS / connect failure
    if isinstance(r, socket.gaierror) or "Name or service not known" in s or "Temporary failure in name" in s \
            or "nodename nor servname" in s or "getaddrinfo" in s:
        return "dns"
    if isinstance(r, (socket.timeout, TimeoutError)) or "timed out" in s:
        return "timeout"
    if isinstance(r, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return "conn_error"
    if isinstance(e, (http.client.HTTPException, ConnectionError)):
        return "conn_error"
    return "error:" + type(r).__name__


try:                                   # the sandbox has requests: its plain default User-Agent (python-requests/x.y) is the
    import requests as _requests       # one the other California collectors use; many WAFs refuse Python-urllib's outright.
except ImportError:                    # the laptop has the standard library only: urllib's plain default User-Agent.
    _requests = None
FORCE_URLLIB = False
# --plain-http-fallback (laptop pass, 2026-11-01): when the https:// form of a site fails at the network/TLS level, fetch the
# listed plain http:// URL as well. TLS is still verified on https; a 403 / challenge is never retried. Off in the sandbox,
# which has no plain-HTTP egress anyway.
PLAIN_HTTP_FALLBACK = False
_HTTPS_DOWN: set[str] = set()      # hosts whose https failed once this run: later pages go straight to http (one connection, not two)


def _http_get_requests(url: str, accept: str | None, timeout: int, max_bytes: int) -> dict:
    out = {"url": url, "status": None, "ctype": "", "final_url": url, "body": b"", "err": None, "headers": {}}
    headers = {"Accept": accept} if accept else None
    if USER_AGENT:
        headers = dict(headers or {}, **{"User-Agent": USER_AGENT})
    try:
        r = _requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
        out["status"], out["final_url"] = r.status_code, r.url
        out["headers"] = {k.lower(): v for k, v in r.headers.items()}
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > max_bytes:
                out["truncated"] = True
                break
        r.close()
        out["body"] = bytes(buf[:max_bytes])
    except _requests.exceptions.SSLError:
        out["err"] = "ssl_error"
    except _requests.exceptions.ProxyError as e:
        s = str(e)
        out["err"] = "proxy_denied" if "403" in s or "407" in s else "unreachable_via_proxy"
    except _requests.exceptions.Timeout:
        out["err"] = "timeout"
    except _requests.exceptions.ConnectionError as e:
        s = str(e)
        out["err"] = "dns" if ("Name or service not known" in s or "NameResolution" in s or "getaddrinfo" in s
                                or "Temporary failure in name" in s) else "conn_error"
    except _requests.exceptions.RequestException as e:
        out["err"] = "error:" + type(e).__name__
    except Exception as e:  # noqa: BLE001 - never kill a worker
        out["err"] = "error:" + type(e).__name__
    if out["err"]:
        return out
    out["ctype"] = out["headers"].get("content-type", "")
    return out


def http_get(url: str, accept: str | None = None, timeout: int = TIMEOUT, max_bytes: int = MAX_BYTES) -> dict:
    """GET, upgrading http:// to https:// first (the cloud sandbox has HTTPS egress only: plain http fails with
    "Name or service not known"). If the upgraded request fails at the network level, the site is recorded as
    `https_only` (listed as http-only, not reachable from here) rather than dns/dead."""
    NET_ERRS = ("dns", "conn_error", "timeout", "unreachable_via_proxy", "ssl_error")
    host = urlparse(url).netloc.lower()
    if url.startswith("http://"):
        up = "https://" + url[len("http://"):]
        if PLAIN_HTTP_FALLBACK and host in _HTTPS_DOWN:
            return _http_get_once(url, accept, timeout, max_bytes)
        r = _http_get_once(up, accept, timeout, max_bytes)
        if r["err"] in NET_ERRS:
            if PLAIN_HTTP_FALLBACK:
                r2 = _http_get_once(url, accept, timeout, max_bytes)
                if not r2["err"]:
                    _HTTPS_DOWN.add(host)
                    r2["https_failed"] = r["err"]
                    return r2
            r["err"] = "https_only"
            r["url"] = url
        return r
    if PLAIN_HTTP_FALLBACK and host in _HTTPS_DOWN and url.startswith("https://"):
        return _http_get_once("http://" + url[len("https://"):], accept, timeout, max_bytes)
    r = _http_get_once(url, accept, timeout, max_bytes)
    if PLAIN_HTTP_FALLBACK and r["err"] in NET_ERRS and url.startswith("https://"):
        r2 = _http_get_once("http://" + url[len("https://"):], accept, timeout, max_bytes)
        if not r2["err"]:
            _HTTPS_DOWN.add(host)
            r2["https_failed"] = r["err"]
            return r2
    return r


def _http_get_once(url: str, accept: str | None = None, timeout: int = TIMEOUT, max_bytes: int = MAX_BYTES) -> dict:
    """GET with the plain default User-Agent. Always returns a dict (never raises)."""
    if _requests is not None and not FORCE_URLLIB:
        return _http_get_requests(url, accept, timeout, max_bytes)
    out = {"url": url, "status": None, "ctype": "", "final_url": url, "body": b"", "err": None, "headers": {}}
    req = urllib.request.Request(url)
    if USER_AGENT:
        req.add_header("User-Agent", USER_AGENT)
    if accept:
        req.add_header("Accept", accept)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            out["status"], out["final_url"] = r.status, r.geturl()
            out["headers"] = {k.lower(): v for k, v in r.headers.items()}
            out["body"] = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        out["status"], out["final_url"] = e.code, getattr(e, "url", url) or url
        out["headers"] = {k.lower(): v for k, v in (e.headers or {}).items()}
        try:
            out["body"] = e.read(max_bytes + 1)
        except Exception:  # noqa: BLE001
            out["body"] = b""
    except Exception as e:  # noqa: BLE001 - never kill a worker
        out["err"] = classify_net_error(e)
        return out
    h = out["headers"]
    out["ctype"] = h.get("content-type", "")
    if h.get("content-encoding", "").lower() == "gzip" or out["body"][:2] == b"\x1f\x8b":
        try:
            out["body"] = gzip.decompress(out["body"])
        except Exception:  # noqa: BLE001
            pass
    if len(out["body"]) > max_bytes:
        out["body"] = out["body"][:max_bytes]
        out["truncated"] = True
    return out


def classify(resp: dict) -> str:
    """ok | challenge | blocked_403 | blocked_429 | not_found | http_4xx | http_5xx | parked | not_html | <net error>"""
    if resp["err"]:
        return resp["err"]
    st, body, h = resp["status"], resp["body"], resp["headers"]
    head = body[:12000]
    if h.get("cf-mitigated", "").lower() == "challenge":
        return "challenge"
    if st in (403, 429, 503, 401, 202) and CHALLENGE_STRONG.search(head):
        return "challenge"
    if st == 403:
        return "blocked_403"
    if st == 429:
        return "blocked_429"
    if st in (404, 410):
        return "not_found"
    if st and st >= 500:
        return "http_5xx"
    if st and st >= 400:
        return "http_4xx"
    if st == 200:
        if len(body) < 40000 and CHALLENGE_STRONG.search(head):
            return "challenge"
        ct = resp["ctype"].lower()
        if body[:4] == b"%PDF" or ("html" not in ct and "xml" not in ct and "json" not in ct and "text" not in ct):
            return "not_html"
        if len(body) < 60000 and PARKED.search(head):
            return "parked"
        return "ok"
    return f"http_{st}"


# ------------------------------------------------------------------------------------------------ html scan
class Scan(HTMLParser):
    """Anchors (href + text + title/aria-label), iframe/embed sources, generator meta, alternate links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.frames, self.generator, self.alts = [], [], "", []
        self._cur = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self._cur = {"href": a["href"].strip(), "text": "", "label": (a.get("aria-label") or a.get("title") or "").strip()}
        elif tag in ("iframe", "embed") and a.get("src"):
            self.frames.append(a["src"].strip())
        elif tag == "meta" and a.get("name", "").lower() == "generator":
            self.generator = a.get("content", "")[:80]
        elif tag == "link" and "alternate" in a.get("rel", "").lower() and a.get("href"):
            self.alts.append({"href": a["href"], "type": a.get("type", "")})
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self._cur["text"] = re.sub(r"\s+", " ", self._cur["text"]).strip()[:140]
            self.links.append(self._cur)
            self._cur = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._cur is not None and len(self._cur["text"]) < 160:
            self._cur["text"] += data
        if self._in_title and len(self.title) < 160:
            self.title += data


def scan_html(body: bytes) -> Scan:
    s = Scan()
    try:
        s.feed(body.decode("utf-8", "replace"))
        s.close()
    except Exception:  # noqa: BLE001 - malformed html: keep what we have
        pass
    return s


# ------------------------------------------------------------------------------------------------ link choice
CATS = {
    "mass_times": (r"mass(?:es)?[\s_/-]*(?:times?|schedule|hours)|mass[\s_-]*(?:and|&)[\s_-]*confession|horarios?|misas?\b|"
                   r"\bmass(?:es)?\b|worship|liturg|schedule|confession|reconcil|adoration|sacrament|"
                   # lane G4, 2026-11-01 (sunday_audit): Polish / Vietnamese / Eastern-rite link texts, "service times",
                   # and the visitor pages that usually carry the weekend times; no PREF bonus, so a real "Mass Times"
                   # link still ranks first
                   r"\bmsz[aey]\b|\bmsze\b|nabo[zż]e[nń]stw|porz[aą]dek|th[aá]nh[\s_-]*l[eễ]|gi[oờ][\s_-]*l[eễ]|"
                   r"divine[\s_-]*liturgy|service[\s_-]*times|(?:worship|weekend|church)[\s_-]*services|"
                   r"plan[\s_-]*(?:your|a)[\s_-]*visit|new[\s_-]*here|i'?m[\s_-]*new|first[\s_-]*visit|visit[\s_-]*us", 2),
    "ministries": (r"ministr|\bgroups?\b|organi[sz]ations|get[\s_-]*involved|societ|clubs?\b|apostolate|"
                   r"parish[\s_-]*life|community[\s_-]*life|ministerios|grupos|faith[\s_-]*formation|\bserve\b|"
                   r"\bformation\b|devotions|^involve", 3),
    "bulletin": (r"bulletin|boletin|newsletter|weekly[\s_-]*(?:news|update)", 1),
    "calendar": (r"calendar|calendario|\bevents?\b|eventos|what'?s[\s_-]*happening", 2),
    "live": (r"live[\s_-]*stream|\blive\b|watch[\s_-]*(?:mass|live|online)|mass[\s_-]*online|online[\s_-]*mass|streaming|"
             r"virtual[\s_-]*mass|transmisi", 1),
    "contact": (r"contact", 1),
}
SKIP_PATH = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|svg|docx?|xlsx?|pptx?|zip|mp3|mp4|ics)(?:\?|$)|/wp-(?:json|admin|login|content)|"
                       r"/feed/?$|/tag/|/category/|/author/|/\d{4}/\d{2}/|/event/|/events?/(?:category|tag|list|month|day)/|"
                       r"[?&](?:month|date|tribe|eventdate|ical|outlook|replytocom|share|print)=|/cart|/checkout|/search|"
                       r"/privacy|/terms|/login|/account|/donate|/giving|/give/|/shop|/store|/blog/|/news/[^/]+/", re.I)
PREF = {
    "mass_times": re.compile(r"mass[\s_-]*(?:times?|schedule)|schedule|horario|mass-?and|confession|worship/?$", re.I),
    "ministries": re.compile(r"/ministr[a-z]*/?$|ministerios|/groups?/?$|get-involved|/involved|organi[sz]ations|parish-life", re.I),
    "calendar": re.compile(r"/calendar/?$|/events/?$|calendario|/eventos/?$|upcoming", re.I),
    "bulletin": re.compile(r"bulletin|boletin", re.I),
}


def host_of(u: str) -> str:
    h = urlparse(u).netloc.lower().split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def pick_links(scan: Scan, base: str) -> dict[str, list[str]]:
    found: dict[str, list[tuple[int, str]]] = {k: [] for k in CATS}
    bh = host_of(base)
    for a in scan.links:
        href = urljoin(base, a["href"])
        if not href.startswith("http") or host_of(href) != bh or SKIP_PATH.search(href):
            continue
        href = href.split("#")[0]
        text = (a["text"] or a["label"]).lower()
        path = urlparse(href).path.lower()
        query = urlparse(href).query.lower()
        if path in ("", "/", "/index.php") and not re.match(r"(?:page_id|p|id|itemid|option)=\w", query):
            continue                        # the home page itself; "/?page_id=57" (WordPress without permalinks) is a page
        if path in ("", "/", "/index.php"):
            path = "/?" + query
        for cat, (rx, _) in CATS.items():
            m_text = re.search(rx, text, re.I)
            m_path = re.search(rx, path, re.I)
            if not (m_text or m_path):
                continue
            score = (2 if m_text else 0) + (1 if m_path else 0)
            if cat in PREF and PREF[cat].search(path + " " + text):
                score += 3
            if len(path.strip("/").split("/")) > 3:
                score -= 1
            found[cat].append((score, href))
    out = {}
    for cat, lst in found.items():
        seen, order = set(), []
        for _, u in sorted(lst, key=lambda x: -x[0]):
            if u not in seen:
                seen.add(u)
                order.append(u)
        out[cat] = order
    return out


# A landing / door page ("Enter", "Parish | School | Foundation", a language chooser): the parish's real home is one
# same-site link away, and the home page itself links to almost nothing (lane G4 audit, 2026-11-01: 4 of 40 sampled
# no-Sunday sites). The door's target is fetched as a schedule candidate and its own links are picked as well.
DOOR_TEXT = re.compile(r"^\s*(?:enter|enter\s+(?:here|site|our\s+(?:parish|site|website))|parish|church|the\s+parish|our\s+parish|"
                       r"parish\s+(?:home|website|site)|catholic\s+church|english|welcome|home)\s*$|\(enter\)", re.I)
DOOR_NOT = re.compile(r"school|academy|foundation|preschool|chinese|中文|spanish|espa[nñ]ol|vietnam|korean|polski|portugu|"
                      r"cemetery|donat|giving|privacy|login", re.I)


def internal_links(scan: Scan, base: str) -> set[str]:
    bh = host_of(base)
    out = set()
    for a in scan.links:
        href = urljoin(base, a["href"]).split("#")[0]
        if href.startswith("http") and host_of(href) == bh and not SKIP_PATH.search(href):
            out.add(href.rstrip("/"))
    return out


def door_links(scan: Scan, base: str) -> list[str]:
    """Links out of a thin landing page into the parish's own site (same host): "Enter", "Parish", "Church", "English"."""
    if len(internal_links(scan, base)) >= 12:
        return []
    bh, out = host_of(base), []
    for a in scan.links:
        href = urljoin(base, a["href"]).split("#")[0]
        text = (a["text"] or a["label"]).strip()
        if not href.startswith("http") or host_of(href) != bh or SKIP_PATH.search(href) or DOOR_NOT.search(text + " " + href):
            continue
        if urlparse(href).path.strip("/") == urlparse(base).path.strip("/"):
            continue
        if DOOR_TEXT.search(text) and href not in [u for _, u in out]:
            pri = 2 if re.match(r"^\s*home\s*$", text, re.I) else 1 if re.match(r"^\s*(?:english|welcome)\s*$", text, re.I) else 0
            out.append((pri, href))
    return [u for _, u in sorted(out, key=lambda x: x[0])][:2]


def cms_hint(text: str) -> set[str]:
    t = text[:400000].lower()
    s = set()
    if "ecatholic" in t:
        s.add("ecatholic")
    if "wp-content" in t or "wp-includes" in t or "wp-json" in t:
        s.add("wordpress")
    return s


# ------------------------------------------------------------------------------------------------ one site
class Deadline(Exception):
    pass


class Site:
    def __init__(self, key: str, home: str, raw_root: str, pacer: Pacer, retry_failed: bool, window_start: str,
                 env: str, deadline_at: float):
        self.key, self.home, self.pacer = key, home, pacer
        self.dir = os.path.join(raw_root, key.replace("/", "__"))
        os.makedirs(os.path.join(self.dir, "feeds"), exist_ok=True)
        self.mpath = os.path.join(self.dir, "manifest.json")
        self.retry_failed, self.env, self.window_start = retry_failed, env, window_start
        self.deadline = min(deadline_at, time.monotonic() + SITE_SECONDS)
        self.m = {"key": key, "home": home, "env": env, "window_start": window_start, "pages": [], "feeds": [],
                  "complete": False}
        if os.path.exists(self.mpath):
            try:
                old = json.load(open(self.mpath))
                if old.get("home") == home:
                    self.m = old
                    self.m["env"] = old.get("env") or env
            except Exception:  # noqa: BLE001
                pass
        self.byurl = {p["url"]: p for p in self.m["pages"]}
        self.rp = None
        self.delay = 1.0
        self.n_net = 0

    # -- persistence
    def save(self) -> None:
        tmp = self.mpath + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.m, f, ensure_ascii=False)
        os.replace(tmp, self.mpath)

    # -- one cached request
    def get(self, url: str, kind: str, accept: str | None = None, ext: str = ".html", feed: bool = False,
            respect_robots: bool = True) -> dict | None:
        rec = self.byurl.get(url)
        if rec is not None:
            stale = (self.retry_failed and rec.get("class") not in ("ok", "not_found", "robots_disallowed")
                     and rec.get("env") != self.env)
            if not stale:
                return rec
        if time.monotonic() > self.deadline:
            raise Deadline()
        if respect_robots and self.rp is not None and not self.rp.can_fetch("*", url):
            rec = {"url": url, "kind": kind, "class": "robots_disallowed", "status": None, "fetched_at": now_iso(),
                   "env": self.env}
        else:
            self.pacer.wait(host_of(url), self.delay)
            resp = http_get(url, accept=accept)
            cls = classify(resp)
            rec = {"url": url, "kind": kind, "class": cls, "status": resp["status"], "final_url": resp["final_url"],
                   "ctype": resp["ctype"][:80], "bytes": len(resp["body"]), "fetched_at": now_iso(), "env": self.env,
                   "tries": (self.byurl.get(url) or {}).get("tries", 0) + 1}
            if resp["err"]:
                rec["err"] = resp["err"]
            if cls in ("ok", "challenge", "blocked_403", "blocked_429", "parked", "http_5xx") and resp["body"]:
                name = hashlib.sha1(url.encode()).hexdigest()[:12]
                rel = (f"feeds/{name}{ext}" if feed else f"{len(self.m['pages']):02d}_{name}{ext}") + ".gz"
                with gzip.GzipFile(os.path.join(self.dir, rel), "wb", compresslevel=4, mtime=0) as f:
                    f.write(resp["body"])
                rec["file"] = rel
        if url in self.byurl:
            self.m["pages"][self.m["pages"].index(self.byurl[url])] = rec
        else:
            self.m["pages"].append(rec)
        self.byurl[url] = rec
        self.save()
        return rec

    def body(self, rec: dict) -> bytes:
        if not rec or not rec.get("file"):
            return b""
        with gzip.open(os.path.join(self.dir, rec["file"]), "rb") as f:
            return f.read()

    # -- the plan
    def run(self) -> dict:
        m = self.m
        try:
            self._run()
        except Deadline:
            m["note"] = "run cut by time cap; rerun resumes from cache"
            self.save()
            return m
        m["complete"] = True
        m["finished_at"] = now_iso()
        self.save()
        return m

    def _robots(self) -> None:
        if "robots" in self.m:
            r = self.m["robots"]
            if r.get("text") is not None:
                self.rp = urllib.robotparser.RobotFileParser()
                self.rp.parse(r["text"].splitlines())
                cd = self.rp.crawl_delay("*")
                self.delay = min(max(1.0, float(cd or 0)), CRAWL_DELAY_CAP)
            return
        base = f"{urlparse(self.home).scheme}://{urlparse(self.home).netloc}"
        self.pacer.wait(host_of(base))
        resp = http_get(base + "/robots.txt", timeout=12, max_bytes=200000)
        cls = classify(resp)
        text = None
        if resp["status"] == 200 and resp["body"] and b"<html" not in resp["body"][:600].lower():
            text = resp["body"].decode("utf-8", "replace")[:100000]
        self.m["robots"] = {"class": cls, "status": resp["status"], "text": text}
        if text is not None:
            self._robots()

    def _run(self) -> None:
        m = self.m
        self._robots()
        if self.rp is not None and not self.rp.can_fetch("*", self.home):
            m["status"] = "robots_disallowed"
            return
        home = self.get(self.home, "home")
        m["status"] = home["class"]
        if home["class"] != "ok":
            m["final_home"] = home.get("final_url")
            # a bare-domain home that fails: try the other of www / non-www once (plain request)
            return
        m["final_home"] = home.get("final_url") or self.home
        if host_of(m["final_home"]) != host_of(self.home):
            m["redirected_off_host"] = host_of(m["final_home"])
        hbody = self.body(home)
        scan = scan_html(hbody)
        hint = cms_hint(hbody.decode("utf-8", "replace"))
        picks = pick_links(scan, m["final_home"])
        base = m["final_home"]
        netloc = urlparse(base).scheme + "://" + urlparse(base).netloc
        # --- per-CMS fast paths
        guesses: list[tuple[str, str]] = []
        if "wordpress" in hint and not (picks["mass_times"] and picks["ministries"] and picks["bulletin"]):
            idx = self.get(netloc + "/wp-json/wp/v2/pages?per_page=100&_fields=link,slug,title", "wp_pages",
                           accept="application/json", ext=".json", feed=True)
            if idx and idx["class"] == "ok":
                try:
                    arr = json.loads(self.body(idx))
                    pseudo = Scan()
                    pseudo.links = [{"href": p.get("link", ""), "text": (p.get("title") or {}).get("rendered", ""), "label": ""}
                                    for p in arr if isinstance(p, dict)]
                    extra = pick_links(pseudo, base)
                    for c in picks:
                        for u in extra[c]:
                            if u not in picks[c]:
                                picks[c].append(u)
                except Exception:  # noqa: BLE001
                    pass
        if "ecatholic" in hint:
            if not picks["mass_times"]:
                guesses += [("mass_times", netloc + "/mass-times"), ("mass_times", netloc + "/mass-schedule")]
            if not picks["ministries"]:
                guesses.append(("ministries", netloc + "/ministries"))
            if not picks["bulletin"]:
                guesses += [("bulletin", netloc + "/bulletins"), ("bulletin", netloc + "/bulletin")]
        # --- a landing / door page: fetch the door's target as a schedule candidate and pick links from it too
        door_done: set[str] = set()
        for du in door_links(scan, base):
            r = self.get(du, "mass_times")
            door_done.add(du)
            if r and r["class"] == "ok":
                m["door"] = (m.get("door") or []) + [du]
                sub = pick_links(scan_html(self.body(r)), r.get("final_url") or du)
                for c in picks:
                    picks[c] = sub[c] + [u for u in picks[c] if u not in sub[c]]
            elif r and r["class"] in ("challenge", "blocked_403", "blocked_429"):
                break
        m["picks"] = {c: v[:4] for c, v in picks.items()}
        plan: list[tuple[str, str, bool]] = []
        for cat, (_, quota) in CATS.items():
            for u in picks[cat][:quota]:
                plan.append((cat, u, False))
        plan += [(c, u, True) for c, u in guesses]
        got_cat: dict[str, int] = {}
        done_urls = {self.home, base, home.get("final_url")} | door_done
        n = 1 + len(door_done)
        for cat, u, is_guess in plan:
            if n >= MAX_PAGES:
                break
            if u in done_urls or (is_guess and got_cat.get(cat)):
                continue
            done_urls.add(u)
            r = self.get(u, cat)
            n += 1
            if r and r["class"] == "ok":
                got_cat[cat] = got_cat.get(cat, 0) + 1
            elif r and r["class"] in ("challenge", "blocked_403", "blocked_429"):
                m["note"] = f"stopped: {r['class']} on {cat} page"   # the site starts refusing us: never push harder
                break
        # --- feeds that live on this site
        self._feeds(hint, scan)

    def _feeds(self, hint: set[str], home_scan: Scan) -> None:
        m = self.m
        base = m["final_home"]
        netloc = urlparse(base).scheme + "://" + urlparse(base).netloc
        texts = []
        for p in m["pages"]:
            if p.get("kind") in ("home", "mass_times", "ministries", "bulletin", "calendar", "live", "contact") \
                    and p.get("class") == "ok":
                texts.append(self.body(p).decode("utf-8", "replace"))
        blob = "\n".join(texts)
        ics = []
        for u in re.findall(r"""(?:href|src)=["']((?:webcal|https?)://[^"']+?\.ics(?:\?[^"']*)?|/[^"']*?\.ics(?:\?[^"']*)?)["']""", blob):
            full = urljoin(base, u.replace("webcal://", "https://"))
            if host_of(full) == host_of(base) and full not in ics:
                ics.append(full)
        tribe = bool(re.search(r"tribe-events|tribe_events|/wp-json/tribe/events/v1", blob)) or "wordpress" in hint
        if tribe:
            ws = date.fromisoformat(self.window_start)
            end = ws + timedelta(days=FEED_DAYS - 1)
            api = netloc + "/wp-json/tribe/events/v1/events"
            page = 1
            while page <= 8:
                params = {"start_date": ws.strftime("%Y-%m-%d"), "end_date": end.strftime("%Y-%m-%d 23:59:59"),
                          "per_page": 50, "page": page}
                url = api + "?" + urlencode(params)
                r = self.get(url, "tribe_events", accept="application/json", ext=".json", feed=True)
                if not r or r["class"] != "ok":
                    break
                try:
                    js = json.loads(self.body(r))
                except ValueError:
                    break
                if "events" not in js:
                    break
                if page >= (js.get("total_pages") or 1):
                    break
                page += 1
        for u in ics[:3]:
            self.get(u, "ics", accept="text/calendar,*/*", ext=".ics", feed=True)


# ------------------------------------------------------------------------------------------------ driver
def main() -> int:
    global SITE_SECONDS, USER_AGENT, PLAIN_HTTP_FALLBACK
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-seconds", type=float, default=0, help="stop starting new sites after this long (0 = no limit)")
    ap.add_argument("--retry-failed", action="store_true", help="refetch cached non-ok pages that came from another environment")
    ap.add_argument("--user-agent", default=USER_AGENT, help="default: the honest FindTheChurch name (never a browser's)")
    ap.add_argument("--env", default="sandbox")
    ap.add_argument("--site-seconds", type=float, default=SITE_SECONDS, help="wall-clock cap per site per run")
    ap.add_argument("--window-start", default=date.today().isoformat())
    ap.add_argument("--only", default="", help="comma-separated keys")
    ap.add_argument("--plain-http-fallback", action="store_true",
                    help="laptop pass: if https fails at the network/TLS level, fetch the listed http:// URL (TLS still verified on https)")
    a = ap.parse_args()
    PLAIN_HTTP_FALLBACK = a.plain_http_fallback
    SITE_SECONDS = a.site_seconds
    USER_AGENT = a.user_agent
    sites = json.load(open(a.input))
    only = {x for x in a.only.split(",") if x}
    if only:
        sites = [s for s in sites if s["key"] in only]
    os.makedirs(a.raw_root, exist_ok=True)
    t0 = time.monotonic()
    hard_stop = t0 + (a.max_seconds + SITE_SECONDS if a.max_seconds else 10 ** 9)
    pacer = Pacer(1.0)
    stats = {"done": 0, "skipped": 0, "cut": 0}
    lock = threading.Lock()

    def need(s) -> bool:
        mp = os.path.join(a.raw_root, s["key"].replace("/", "__"), "manifest.json")
        if not os.path.exists(mp):
            return True
        try:
            m = json.load(open(mp))
        except Exception:  # noqa: BLE001
            return True
        return not m.get("complete")

    todo = [s for s in sites if need(s)]
    stats["skipped"] = len(sites) - len(todo)
    print(f"{len(sites)} sites, {len(todo)} to fetch, env={a.env}", flush=True)

    def work(s):
        if a.max_seconds and time.monotonic() - t0 > a.max_seconds:
            return None
        site = Site(s["key"], s["home"], a.raw_root, pacer, a.retry_failed, a.window_start, a.env, hard_stop)
        m = site.run()
        with lock:
            stats["done" if m.get("complete") else "cut"] += 1
            print(f"[{stats['done'] + stats['cut']}/{len(todo)}] {s['key']:42s} {m.get('status', '?'):14s} "
                  f"pages={len(m['pages'])} t={time.monotonic() - t0:.0f}s", flush=True)
        return m

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(work, todo))
    left = len([s for s in sites if need(s)])
    print(f"finished chunk: {stats}, remaining incomplete: {left}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
