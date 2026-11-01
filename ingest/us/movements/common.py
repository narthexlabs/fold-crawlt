"""Shared helpers for national movement directories (lane D): the parish index, name normalisation and matching.

Parish universe = the built Find pages (fold-app public/us/<see>/p/*.json and public/ca/<diocese>/p/*.json), copied
once into data/real/us/movements/parish_index.json (id, name, city, state, zip, see) by build_parish_index.py, so the
Actions runner (which has no fold-app checkout) can match too. Ids are the build's own page ids.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "real" / "us" / "movements"
RAW = ROOT / "data" / "raw" / "us" / "movements"
UA = "FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def save(p: Path, obj, list_key: str | None = None) -> None:
    """Write JSON atomically. With list_key, that list is written one compact record per line (small diffs)."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    if list_key and isinstance(obj.get(list_key), list):
        head = {k: v for k, v in obj.items() if k != list_key}
        body = ",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=False) for r in obj[list_key])
        txt = json.dumps(head, ensure_ascii=False, indent=1)[:-2] + f',\n "{list_key}": [\n' + body + "\n ]\n}\n"
        json.loads(txt)
    else:
        txt = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
    tmp.write_text(txt)
    tmp.replace(p)


def ascii_(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def norm_city(c: str | None) -> str:
    c = ascii_(c or "").lower().replace("&", " and ")
    c = re.sub(r"\bsaint\b|\bste?\b\.?", "st", c)
    c = re.sub(r"\bft\b\.?", "fort", c)
    c = re.sub(r"\bmt\b\.?", "mount", c)
    return re.sub(r"[^a-z0-9]+", " ", c).strip()


STOP_TAIL = r"(?:roman )?(?:catholic )?(?:parish|church|community|chapel|mission|shrine|basilica|cathedral|co-?cathedral|oratory|newman center|council|knights of columbus|kofc|k of c)"
WEAK = {"st", "of", "the", "and", "our", "lady", "holy", "blessed", "parish", "church", "catholic", "roman", "council", "de",
        "la", "del", "los", "las", "san", "santa", "santo", "nuestra", "senora", "virgin", "mary", "sacred", "heart", "most"}


def norm_name(n: str | None, city: str | None = None) -> str:
    """lower-case core of a parish/council name: 'St. Joseph Basilica of Alameda' -> 'st joseph'."""
    s = ascii_(n or "").lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.split(",")[0] if "," in s and len(s.split(",")[0]) > 6 else s
    s = re.sub(r"\bsaints?\b|\bsts?\b\.?|\bste\b\.?", "st", s)
    s = re.sub(r"\bss\b\.?", "st", s)
    s = re.sub(r"\bmt\b\.?", "mount", s)
    s = re.sub(r"\bblvd\b|\bbvm\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(3):
        s = re.sub(rf"\b{STOP_TAIL}\b", " ", s)
        s = re.sub(r"^(the|church of|parish of|catholic church of)\s+", "", s.strip())
        if city:
            c = norm_city(city)
            if c and s.endswith(" " + c) and len(s) > len(c) + 4:
                s = s[: -len(c)].strip()
                s = re.sub(r"\s+(of|in|at)$", "", s)
        s = re.sub(r"\s+", " ", s).strip()
    return s


def strong_tokens(core: str) -> set[str]:
    return {t for t in core.split() if t not in WEAK and len(t) > 1}


def load_parish_index() -> list[dict]:
    d = load(OUT / "parish_index.json", {}) or {}
    return d.get("parishes", [])


def by_state_city(parishes: list[dict]) -> dict:
    ix: dict = {}
    for p in parishes:
        ix.setdefault((p.get("state") or "", norm_city(p.get("city"))), []).append(p)
    return ix


def match(name: str, city: str | None, state: str | None, ix: dict) -> tuple[dict | None, str, str]:
    """(parish, confidence, method). exact = same state+city and same normalised core name, unique; fuzzy = same
    state+city and the council name's distinctive words equal the parish's (or difflib >= 0.9), unique; else unmatched.
    Nothing is attached across cities: a council named for a saint with no city evidence stays unmatched."""
    cands = ix.get((state or "", norm_city(city)), [])
    if not cands:
        return None, "unmatched", "no parish of ours in that city"
    core = norm_name(name, city)
    if not core:
        return None, "unmatched", "empty name"
    exact = [p for p in cands if norm_name(p["name"], p.get("city")) == core]
    if len(exact) == 1:
        return exact[0], "exact", "same city, same name"
    if len(exact) > 1:
        return None, "unmatched", f"ambiguous: {len(exact)} parishes of that name in the city"
    st = strong_tokens(core)
    fuzzy = []
    for p in cands:
        pc = norm_name(p["name"], p.get("city"))
        ps = strong_tokens(pc)
        r = difflib.SequenceMatcher(None, core, pc).ratio()
        if (st and ps and st == ps and (core.startswith("st") == pc.startswith("st"))) or r >= 0.9:
            fuzzy.append((r, p))
    if len(fuzzy) == 1:
        return fuzzy[0][1], "fuzzy", f"same city, distinctive words equal (ratio {fuzzy[0][0]:.2f})"
    if len(fuzzy) > 1:
        return None, "unmatched", f"ambiguous: {len(fuzzy)} fuzzy candidates in the city"
    return None, "unmatched", "no parish of that name in the city"
