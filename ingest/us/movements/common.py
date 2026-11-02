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


# ---- 2026-11-02: one matcher for every directory (councils, fraternities, program parishes, divisions) ----------------
CHURCHY = re.compile(r"\b(st|saint|santa|san|church|parish|our lady|holy|cathedral|basilica|chapel|sacred|christ|immaculate|"
                     r"blessed|mary|shrine|nuestra|sagrado|corpus|trinity|nativity|assumption|resurrection|annunciation|"
                     r"visitation|transfiguration|epiphany|ascension|incarnation|catholic)\b", re.I)


def norm_name2(n: str | None, city: str | None = None) -> str:
    """norm_name plus possessives: "St. Mary's" / "ST MARYS" -> "st mary" (applied to both sides, so it stays symmetric)."""
    s = re.sub(r"(\w)['’]s\b", r"\1s", n or "")
    core = norm_name(s, city)
    return re.sub(r"\b(st [a-z]{3,}?)s\b", r"\1", core)


def km(a_lat, a_lng, b_lat, b_lng) -> float:
    import math
    p = math.pi / 180
    x = (b_lng - a_lng) * p * math.cos((a_lat + b_lat) * p / 2)
    y = (b_lat - a_lat) * p
    return 6371.0 * math.hypot(x, y)


def norm_street(s: str | None) -> str | None:
    """'1323 Quincy St NE' -> '1323 quincy'; None without a house number."""
    m = re.match(r"\s*(\d+[a-z]?)\s+(?:(?:n|s|e|w|north|south|east|west)\.?\s+)?([a-z0-9]+)", ascii_(s or "").lower())
    return f"{m.group(1)} {m.group(2)}" if m else None


class Matcher:
    """Attach a directory record to one of our parishes. Order: same name in the same city/ZIP (exact), then distinctive
    words / near-identical / one name a prefix of the other, unique (fuzzy), then the same street address (exact), then the
    same spot (<= 0.1 km) when the meeting place is a church (fuzzy). Ambiguity is never guessed; unmatched stays in the
    source file."""

    def __init__(self, parishes: list[dict]):
        self.ps = parishes
        self.city: dict = {}
        self.zip: dict = {}
        self.grid: dict = {}
        self.cities_by_state: dict = {}
        for p in parishes:
            st = p.get("state") or ""
            nc = norm_city(p.get("city"))
            self.city.setdefault((st, nc), []).append(p)
            if nc:
                self.cities_by_state.setdefault(st, set()).add(nc)
            if p.get("zip"):
                self.zip.setdefault(p["zip"], []).append(p)
            if p.get("lat") is not None:
                self.grid.setdefault((round(p["lat"], 1), round(p["lng"], 1)), []).append(p)
            p["_core"] = norm_name2(p.get("name"), p.get("city"))
            p["_street"] = norm_street(p.get("street"))

    def city_in(self, text: str, state: str) -> str | None:
        """the longest of our city names (in that state) that the address text ends with, e.g. '... Washington DC 20017'."""
        t = norm_city(re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", text or ""))
        t = re.sub(rf"\b{(state or '').lower()}\b\s*$", "", t).strip()
        best = None
        for nc in self.cities_by_state.get(state or "", ()):
            if (t == nc or t.endswith(" " + nc)) and (best is None or len(nc) > len(best)):
                best = nc
        return best

    def near(self, lat, lng, r_km: float) -> list[tuple[float, dict]]:
        out = []
        for dy in (-0.1, 0, 0.1):
            for dx in (-0.1, 0, 0.1):
                for p in self.grid.get((round(lat + dy, 1), round(lng + dx, 1)), []):
                    d = km(lat, lng, p["lat"], p["lng"])
                    if d <= r_km:
                        out.append((d, p))
        return sorted(out, key=lambda x: x[0])

    def match(self, name: str | None, state: str | None, city: str | None = None, alt_cities=(), zip_: str | None = None,
              lat=None, lng=None, street: str | None = None) -> tuple[dict | None, str, str]:
        st = state or ""
        city_pool: dict = {}
        for cty in [city, *alt_cities]:
            for p in self.city.get((st, norm_city(cty)), []) if cty else []:
                city_pool[p["id"]] = p
        wide: dict = dict(city_pool)
        z = (zip_ or "")[:5]
        for p in self.zip.get(z, []) if z else []:
            wide[p["id"]] = p
        if lat is not None and lng is not None:
            for _, p in self.near(float(lat), float(lng), 3.0):
                if (p.get("state") or "") == st or not st:
                    wide[p["id"]] = p
        if not wide:
            return None, "unmatched", "no parish of ours in that city / ZIP / within 3 km"
        core = norm_name2(name, city)
        if core:
            # the named city first; the ZIP / 3 km ring only when the city has no parish of that name at all
            for pool, where in ((list(city_pool.values()), "same city"), (list(wide.values()), "same ZIP or within 3 km")):
                if not pool:
                    continue
                res = self._by_name(core, pool, lat, lng, where)
                if res[0] or res[2].startswith("ambiguous"):
                    return res
        ns = norm_street(street)
        if ns:
            same = [p for p in wide.values() if p["_street"] == ns]
            if len(same) == 1:
                return same[0], "exact", "same street address"
        if lat is not None and lng is not None and (not name or CHURCHY.search(ascii_(name))):
            nb = self.near(float(lat), float(lng), 0.1)
            if len(nb) == 1 or (len(nb) > 1 and nb[1][0] > 3 * max(nb[0][0], 0.01)):
                return nb[0][1], "fuzzy", f"meets at a church {nb[0][0] * 1000:.0f} m from this parish's map pin"
        return None, "unmatched", "no parish of that name / address in the city or ZIP"

    def _by_name(self, core: str, pool: list[dict], lat, lng, where: str) -> tuple[dict | None, str, str]:
        exact = [p for p in pool if p["_core"] == core]
        if len(exact) == 1:
            return exact[0], "exact", f"{where}, same name"
        if len(exact) > 1:
            pick = self._nearest(exact, lat, lng)
            if pick:
                return pick, "fuzzy", f"{len(exact)} parishes of that name nearby; the nearest is under 1 km"
            pick = self._listed_twice(exact)
            if pick:
                return pick, "exact", f"{where}, same name; the other page of that name has no address (the church listed twice)"
            return None, "unmatched", f"ambiguous: {len(exact)} parishes of that name ({where})"
        stt = strong_tokens(core)
        fz = []
        for p in pool:
            pc = p["_core"]
            ps = strong_tokens(pc)
            r = difflib.SequenceMatcher(None, core, pc).ratio()
            prefix = (len(core) >= 6 and len(pc) >= 6 and (pc.startswith(core + " ") or core.startswith(pc + " "))
                      and core.startswith("st") == pc.startswith("st"))
            if (stt and ps and stt == ps and core.startswith("st") == pc.startswith("st")) or r >= 0.9 or prefix:
                fz.append(p)
        if len(fz) == 1:
            return fz[0], "fuzzy", f"{where}, distinctive words equal / near-identical / one name extends the other"
        if len(fz) > 1:
            pick = self._nearest(fz, lat, lng)
            if pick:
                return pick, "fuzzy", f"{len(fz)} similar names nearby; the nearest is under 1 km"
            if len({p["_core"] for p in fz}) == 1:
                pick = self._listed_twice(fz)
                if pick:
                    return pick, "fuzzy", f"{where}, similar name; the other page of that name has no address (the church listed twice)"
            return None, "unmatched", f"ambiguous: {len(fz)} similar names ({where})"
        return None, "unmatched", f"no parish of that name ({where})"

    RITE = re.compile(r"ukrain|byzantin|maronit|melkit|syro|chaldea|armenia|ruthenia|copt|romania|slovak|greek|eparch|"
                      r"polish national|vietnam|korea|filipin|chinese|hispanic|spanish|latin mass|traditional|fssp|sspx", re.I)

    @classmethod
    def _listed_twice(cls, ps: list[dict]) -> dict | None:
        """The Find build sometimes has the same church twice: the directory's page (with an address) and a name-only page
        from the national website list. With exactly one addressed page and the rest name-only (no street, ZIP or pin),
        and no rite / language word in any of the names, the addressed page is the church."""
        if any(cls.RITE.search(p.get("name") or "") for p in ps):
            return None
        if len({(p.get("state"), norm_city(p.get("city"))) for p in ps}) != 1:
            return None
        addressed = [p for p in ps if p.get("street") or p.get("zip") or p.get("lat") is not None]
        if len(addressed) == 1 and all(not (p.get("street") or p.get("zip") or p.get("lat") is not None) for p in ps if p is not addressed[0]):
            return addressed[0]
        return None

    @staticmethod
    def _nearest(ps: list[dict], lat, lng) -> dict | None:
        if lat is None or lng is None:
            return None
        d = sorted(((km(float(lat), float(lng), p["lat"], p["lng"]), p) for p in ps if p.get("lat") is not None), key=lambda x: x[0])
        if d and d[0][0] <= 1.0 and (len(d) == 1 or d[1][0] > 2 * d[0][0]):
            return d[0][1]
        return None
