"""Shared helpers for the civic-context ingests (ACS, geometry, catchments, neighbours).

Paths, parish loading, distance math and ACS margin-of-error arithmetic live here so every script
in ingest/public/civic/ uses the same conventions.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REAL = ROOT / "data" / "real"
OUT = REAL / "civic"
RAW = ROOT / "data" / "raw" / "civic"

sys.path.insert(0, str(HERE.parent))  # ingest/public, for common.py
from common import UA  # noqa: E402

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})

COUNTIES = {"06001": "Alameda", "06013": "Contra Costa"}
MILE_KM = 1.609344
EARTH_KM = 6371.0088


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def save_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        txt = json.dumps(obj, indent=1, ensure_ascii=False)
    path.write_text(txt + "\n", encoding="utf-8")


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def download(url: str, dest: Path, refresh: bool = False, timeout: int = 600, tries: int = 3) -> Path:
    """Stream a file to disk (skips if already present unless refresh)."""
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for i in range(tries):
        try:
            with SESSION.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            tmp.replace(dest)
            return dest
        except requests.RequestException as e:  # pragma: no cover - network
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"download failed: {url}: {last}")


# ---------------------------------------------------------------- parishes
def load_parishes(include_extra: bool = True) -> list[dict]:
    """The 75 parishes/missions/campus ministries in the 2026 diocesan directory, with coordinates.

    Coordinates come from data/real/parishes_directory.json (Census geocoder). The 7 records the
    directory geocode missed are filled from data/real/civic/parish_geocode_fill.json (built by
    geocode_fill.py), and each record says where its coordinates came from.
    """
    d = load_json(REAL / "parishes_directory.json")["records"]
    fj = load_json(OUT / "parish_geocode_fill.json", {}) or {}
    fill = {r["id"]: r for r in fj.get("records", [])}
    out = []
    for r in d:
        lat, lng, src = r.get("lat"), r.get("lng"), r.get("geo_source")
        if lat is None and r["id"] in fill and fill[r["id"]].get("lat") is not None:
            f = fill[r["id"]]
            lat, lng, src = f["lat"], f["lng"], f["geo_source"]
        out.append({"id": r["id"], "name": r["name"], "kind": r.get("kind"), "address": r.get("address"),
                    "city": r.get("city"), "zip": r.get("zip"), "lat": lat, "lng": lng, "geo_source": src,
                    "deanery": r.get("deanery"), "region": r.get("region"), "languages_directory": r.get("languages") or [],
                    "mass_times_text": r.get("mass_times_text"), "source_url": r.get("source_url"),
                    "in_2026_directory": True})
    if include_extra:
        for e in fj.get("extra_parishes", []):
            out.append({"id": e["id"], "name": e["name"], "kind": "parish", "address": e["address"], "city": e["city"],
                        "zip": e["zip"], "lat": e["lat"], "lng": e["lng"], "geo_source": e["geo_source"],
                        "deanery": None, "region": None, "languages_directory": [], "mass_times_text": None,
                        "source_url": e["source_url"], "in_2026_directory": False, "status_note": e["status_note"]})
    return out


# ---------------------------------------------------------------- distance / projection
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Accepts scalars or numpy arrays."""
    import numpy as np
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(a))


# Local equirectangular projection centred on the diocese (error < 0.1% over ~100 km); used only
# for Voronoi construction and area-style geometry, never for reported distances.
LAT0 = 37.85
KX = 111.32 * math.cos(math.radians(LAT0))
KY = 110.574


def to_xy(lng, lat):
    return lng * KX, lat * KY


def to_lnglat(x, y):
    return x / KX, y / KY


# ---------------------------------------------------------------- ACS margin-of-error arithmetic
# Census special MOE codes: -555555555 = estimate is controlled (MOE not applicable, treat as 0);
# -222222222/-333333333/-666666666/-888888888/-999999999 = not available.
def clean_moe(m):
    if m is None:
        return None
    if m == -555555555:
        return 0.0
    if m < 0:
        return None
    return float(m)


def clean_est(e):
    if e is None:
        return None
    if e in (-666666666, -999999999, -888888888, -222222222, -333333333):
        return None
    return float(e)


def moe_sum(parts: list[tuple[float, float]]) -> float | None:
    """MOE of a sum of estimates: root-sum-of-squares (ACS General Handbook ch. 8). Per Census guidance,
    when several components are zero only the largest zero-estimate MOE is included."""
    zero_moes = [m for e, m in parts if e == 0 and m is not None]
    nonzero = [m for e, m in parts if e != 0]
    if any(m is None for m in nonzero):
        return None
    sq = sum(m * m for m in nonzero)
    if zero_moes:
        sq += max(zero_moes) ** 2
    return math.sqrt(sq)


def moe_prop(num: float, num_moe: float, den: float, den_moe: float) -> float | None:
    """MOE of a proportion num/den where num is a subset of den (ACS Handbook formula; falls back to the
    ratio formula when the radicand is negative)."""
    if not den or num is None or num_moe is None or den_moe is None:
        return None
    p = num / den
    rad = num_moe ** 2 - (p ** 2) * (den_moe ** 2)
    if rad < 0:
        rad = num_moe ** 2 + (p ** 2) * (den_moe ** 2)
    return math.sqrt(rad) / den


def cv(est, moe) -> float | None:
    """Coefficient of variation (SE/estimate), SE = MOE/1.645."""
    if not est or moe is None:
        return None
    return (moe / 1.645) / est


def reliability(est, moe) -> str:
    c = cv(est, moe)
    if c is None:
        return "n/a"
    if c <= 0.15:
        return "high"
    if c <= 0.30:
        return "medium"
    return "low"
