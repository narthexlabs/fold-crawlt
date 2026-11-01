"""Shared bits for the school / charity / cemetery scrapers.

Raw downloads land in data/raw/schools (gitignored); outputs go to data/real/.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ingest/public

from common import REAL, RAW, SESSION, census_geocode, load_json, now_iso, save_json, _ascii  # noqa: E402,F401

RAW_SCHOOLS = RAW / "schools"
COUNTIES = {"Alameda", "Contra Costa"}

# Catholic schools run by religious orders or independent lay boards, not by the
# diocese or a parish. NCES "type of Catholic school" (P445) is self-reported and
# flips between cycles, so this curated list is what sets diocesan: false.
NON_DIOCESAN = {
    "carondelet": "Sisters of St. Joseph of Carondelet",
    "de la salle high": "De La Salle Christian Brothers",
    "de la salle academy": "De La Salle Christian Brothers (independent)",
    "cristo rey de la salle": "De La Salle Christian Brothers / Cristo Rey Network",
    "saint mary's college high": "De La Salle Christian Brothers",
    "st mary's college high": "De La Salle Christian Brothers",
    "moreau": "Congregation of Holy Cross",
    "holy names high": "Sisters of the Holy Names of Jesus and Mary",
    "salesian": "Salesians of Don Bosco",
    "wood rose": "Independent (Opus Dei spiritual direction)",
    "chesterton academy": "Independent lay Catholic (Chesterton Schools Network)",
}

# CDE lists a public district, not a city. Districts that span one city (or whose
# Catholic schools sit in one city) map to it; used only for name+city matching.
DISTRICT_CITY = {
    "Alameda Unified": "Alameda", "Berkeley Unified": "Berkeley", "Castro Valley Unified": "Castro Valley",
    "Dublin Unified": "Dublin", "Fremont Unified": "Fremont", "Hayward Unified": "Hayward",
    "Livermore Valley Joint Unified": "Livermore", "Newark Unified": "Newark", "Oakland Unified": "Oakland",
    "Piedmont City Unified": "Piedmont", "San Leandro Unified": "San Leandro",
    "San Lorenzo Unified": "San Lorenzo", "Antioch Unified": "Antioch", "John Swett Unified": "Rodeo",
    "Lafayette Elementary": "Lafayette", "Martinez Unified": "Martinez", "Pittsburg Unified": "Pittsburg",
    "Walnut Creek Elementary": "Walnut Creek", "San Ramon Valley Unified": "Danville",
    "Mt. Diablo Unified": "Concord", "West Contra Costa Unified": None,  # Richmond/Pinole/El Cerrito/San Pablo
    "Contra Costa County Office of Education": None,
}


def operator_for(name: str) -> str | None:
    n = school_key(name)
    for k, v in NON_DIOCESAN.items():
        if school_key(k) in n:
            return v
    return None


def school_key(name: str) -> str:
    """Loose key for school names: drop saint/school/catholic/elementary noise."""
    s = _ascii(name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"\bd/b/a\b.*", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(st|saint|sts|school|schools|catholic|elementary|parish|the|of|inc|high|academy|college|preparatory|"
               r"prep|a|lumen|christi)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def download(url: str, dest: Path, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def title_case(s: str | None) -> str | None:
    if not s:
        return s
    t = " ".join(w.capitalize() for w in s.lower().split())
    t = re.sub(r"\bSt\b", "St.", t)
    t = re.sub(r"\b(Po)\b", "PO", t)
    t = re.sub(r"\bOdowd\b", "O'Dowd", t)
    t = re.sub(r"\b(Ne|Nw|Se|Sw)\b", lambda m: m.group(1).upper(), t)
    return t
