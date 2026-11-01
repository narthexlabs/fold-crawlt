"""Shared helpers for the public-data scrapers (name keys, HTTP, geocoding)."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "data" / "real"
RAW = ROOT / "data" / "raw"
MANUAL = Path(__file__).resolve().parent / "manual"

UA = "OaklandDioceseDashboard-research/0.1 (public-data collection; contact matthewdcolt@gmail.com)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get(url: str, timeout: int = 15, **kw) -> requests.Response | None:
    try:
        r = SESSION.get(url, timeout=timeout, **kw)
        return r
    except requests.RequestException:
        return None


def save_json(path: Path, obj) -> None:
    """Write JSON. A data file listed in privacy.FILE_RULES (bulletins, parish profiles, the website crawl, parish
    signals, offerings, the Form 990 extract, ...) is screened for private individuals' names on the way out, so a
    rerun of any ingest script cannot bring them back (CLAUDE.md; see privacy.py)."""
    from privacy import screen_file  # noqa: PLC0415  (privacy reads committed data lazily; no import cycle)
    screen_file(path, obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


# ---------------------------------------------------------------- name keys
# Abbreviations used in the MAP appendix and on GCatholic, expanded before keying.
ABBREV = {
    r"\bimm\b": "immaculate",
    r"\bccop\b": "catholic community of pleasanton",
    r"\bnh\b": "newman hall",
    r"\bhc\b": "help of christians",
    r"\bmary hc\b": "mary help of christians",
    r"\bjoseph worker\b": "joseph the worker",
    r"\bjohn baptist\b": "john the baptist",
    r"\bcatherine siena\b": "catherine of siena",
    r"\blady queen world\b": "our lady queen of the world",
    r"\blady good counsel\b": "our lady of good counsel",
    r"\blady of grace\b": "our lady of grace",
    r"\bour lady guadalupe\b": "our lady of guadalupe",
    r"\bour lady rosary\b": "our lady of the rosary",
    r"\bheart mary\b": "heart of mary",
    r"\bleo\b(?! the great)": "leo the great",
    r"\bjames\b(?! the apostle)": "james the apostle",
}
STOP = r"\b(st|sts|saint|saints|santa|san|church|parish|of|the|and|community|catholic|mission|basilica|cathedral|oratory)\b"
CITY_ALIASES = {"pt richmond": "richmond", "point richmond": "richmond", "crockt/pt costa": "crockett",
                "mission san jose": "fremont"}


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def name_key(name: str) -> str:
    s = _ascii(name or "").lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\bst\.?\s", "st ", s)
    for pat, rep in ABBREV.items():
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^a-z0-9/ -]", " ", s)
    s = re.sub(STOP, " ", s)
    s = re.sub(r"[/ -]+", " ", s).strip()
    return s


def city_key(city: str | None) -> str:
    c = _ascii(city or "").lower().strip()
    c = CITY_ALIASES.get(c, c)
    return re.sub(r"[^a-z]+", "-", c).strip("-")


def canon_key(name: str, city: str | None) -> str:
    return f"{name_key(name)}|{city_key(city)}"


def slug(name: str, city: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{name_key(name)} {city_key(city)}").strip("-")


# ---------------------------------------------------------------- geocoding
_GEO_CACHE = RAW / "geocode_cache.json"


def census_geocode(address: str) -> dict | None:
    cache = load_json(_GEO_CACHE, {})
    if address in cache:
        return cache[address]
    url = ("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address="
           + quote_plus(address) + "&benchmark=Public_AR_Current&format=json")
    r = get(url, timeout=30)
    out = None
    if r is not None and r.ok:
        try:
            m = r.json()["result"]["addressMatches"]
            if m:
                out = {"lat": m[0]["coordinates"]["y"], "lng": m[0]["coordinates"]["x"],
                       "matched_address": m[0]["matchedAddress"], "source_url": url}
        except (ValueError, KeyError):
            pass
    cache[address] = out
    save_json(_GEO_CACHE, cache)
    time.sleep(0.3)
    return out


# ---------------------------------------------------------------- open location code
_OLC = "23456789CFGHJMPQRVWX"


def decode_plus_code(code: str) -> tuple[float, float] | None:
    """Decode a full Open Location Code to its centre (lat, lng)."""
    c = code.upper().replace("+", "").rstrip("0")
    if len(c) < 8 or any(ch not in _OLC for ch in c):
        return None
    lat, lng, res = -90.0, -180.0, 20.0
    pairs = c[:10]
    for i in range(0, len(pairs), 2):
        lat += _OLC.index(pairs[i]) * res
        lng += _OLC.index(pairs[i + 1]) * res
        last_lat, last_lng = res, res
        res /= 20
    if len(c) > 10:
        hlat, hlng = last_lat, last_lng
        for ch in c[10:]:
            hlat /= 5
            hlng /= 4
            v = _OLC.index(ch)
            lat += (v // 4) * hlat
            lng += (v % 4) * hlng
        last_lat, last_lng = hlat, hlng
    return round(lat + last_lat / 2, 6), round(lng + last_lng / 2, 6)
