"""Scan the parish-website crawl (data/raw/parish_sites/<id>/*.html, from ingest/public/parish_sites.py) for
ecosystem signals -> data/real/ecosystem/parish_signals.json

Per parish, with the page URL each signal came from:
  * religious_clergy: clergy/religious named with an institute post-nominal ("Fr. Luke Vu, OFM Conv.")
  * kofc_councils:    Knights of Columbus council numbers ("Council #9089", "Council 16028")
  * svdp:             a St. Vincent de Paul conference/ministry mention
  * groups:           lay groups and movements (Cursillo, Legion of Mary, Catholic Daughters, YLI, ICF, ...)

The raw HTML is local only (data/raw is gitignored); rerun parish_sites.py first if it is missing.
Each context is a short window of page text. It goes through the privacy screen (ingest/public/privacy.py, "raw"
mode, applied by save_json): ministry contacts' and other private names become "[name withheld]", and e-mail
addresses, phone numbers and links are removed. Clergy and public staff names stay.

    python3 -I ingest/public/ecosystem/parish_signals.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RAW, REAL, load_json, now_iso, save_json  # noqa: E402

OUT = REAL / "ecosystem" / "parish_signals.json"

# post-nominal regex -> institute key (keys are org ids in registry.py)
POSTNOMINALS = [
    (r"O\.?\s?F\.?\s?M\.?,?\s*(?:Conv|CONV|conv)\.?", "ri-ofm-conv"),
    (r"O\.?\s?F\.?\s?M\.?,?\s*(?:Cap|CAP|cap)\.?", "ri-ofm-cap"),
    (r"O\.?\s?F\.?\s?M\.?", "ri-ofm"),
    (r"C\.?\s?F\.?\s?R\.?", "ri-cfr"),
    (r"O\.?\s?P\.?", "op"),             # Dominican: friar or sister, resolved by title below
    (r"S\.?\s?J\.?", "ri-sj"),
    (r"S\.?\s?V\.?\s?D\.?", "ri-svd"),
    (r"C\.?\s?S\.?\s?P\.?", "ri-csp"),
    (r"S\.?\s?D\.?\s?B\.?", "ri-sdb"),
    (r"O\.?\s?M\.?\s?I\.?", "ri-omi"),
    (r"S\.?\s?de\s?J\.?", "ri-sdej"),
    (r"S\.?\s?H\.?\s?F\.?", "ri-shf"),
    (r"F\.?\s?C\.?\s?J\.?", "ri-fcj"),
    (r"M\.?\s?C\.?", "ri-mc"),
    (r"S\.?\s?N\.?\s?J\.?\s?M\.?", "ri-snjm"),
    (r"C\.?\s?S\.?\s?J\.?", "ri-csj"),
    (r"R\.?\s?S\.?\s?M\.?", "ri-rsm"),
    (r"S\.?\s?N\.?\s?D\.?\s?de\s?N\.?", "ri-snddeN"),
    (r"F\.?\s?S\.?\s?C\.?", "ri-fsc"),
    (r"C\.?\s?S\.?\s?C\.?", "ri-csc"),
    (r"M\.?\s?F\.?", "ri-mf"),
    (r"C\.?\s?Ss\.?\s?R\.?", "ri-cssr"),
    (r"O\.?\s?C\.?\s?D\.?", "ri-ocd"),
    (r"O\.?\s?Carm\.?", "ri-ocarm"),
    (r"C\.?\s?P\.?\s?P\.?\s?S\.?", "ri-cpps"),
    (r"O\.?\s?S\.?\s?F\.?\s?S\.?", "ri-osfs"),
    (r"S\.?\s?S\.?\s?C\.?\s?C\.?", "ri-sscc"),
    (r"O\.?\s?S\.?\s?A\.?", "ri-osa"),
    (r"M\.?\s?M\.?", "ri-mm"),
    (r"S\.?\s?M\.?", "ri-sm"),
    (r"L\.?\s?H\.?\s?C\.?", "ri-lhc"),
    (r"C\.?\s?M\.?\s?C\.?", "ri-cmc"),
    (r"O\.?\s?S\.?\s?F\.?", "ri-osf"),
    (r"R\.?\s?S\.?\s?C\.?\s?J\.?", "ri-rscj"),
]
TITLE = r"(?:Most\s+Rev\.?|Rev\.?\s+Fr\.?|Very\s+Rev\.?|Rev\.?|Fr\.?|Father|Fray|Padre|Msgr\.?|Br\.?|Brother|Sr\.?|Sister|Deacon|Dcn\.?|Bishop)"
NAME = r"[A-Z][\w\.\-'’]+(?:\s+(?:[A-Z][\w\.\-'’]+|de|del|la|van|von)){0,4}"
CLERGY_RE = re.compile(rf"\b({TITLE})\s+({NAME})\s*,\s*(" + "|".join(p for p, _ in POSTNOMINALS) + r")(?![\w])")
# Second pass without the comma, for the longer, unambiguous post-nominals only ("Rev. Fr. Tuan Nguyen OFM, CONV").
LONG_PN = [p for p, k in POSTNOMINALS if k in {"ri-ofm-conv", "ri-ofm-cap", "ri-ofm", "ri-svd", "ri-omi", "ri-sdb", "ri-csp",
                                                "ri-cfr", "ri-shf", "ri-fcj", "ri-snjm", "ri-cssr", "ri-cpps", "ri-osfs"}]
CLERGY_RE2 = re.compile(rf"\b({TITLE})\s+([A-Z][\w\.\-'’]+(?:\s+[A-Z][a-z][\w\.\-'’]*){{0,3}})\s+(" + "|".join(LONG_PN) + r")(?![\w])")

KOFC_RE = re.compile(r"(?i)(knights\s+of\s+columbus|k\s?of\s?c|caballeros\s+de\s+col[oó]n)(?:(?!knights).){0,80}?(?:council|consejo|#)\s*(?:no\.?|number|#)?\s*(\d{3,5})"
                     r"|(?:council|consejo)\s*(?:no\.?|number|#)?\s*(\d{4,5})\b")
SVDP_RE = re.compile(r"(?i)(st\.?|saint|san)\s+vincent\s+de\s+paul|svdp|san\s+vicente\s+de\s+pa[uú]l")
GROUPS = {
    "Knights of Columbus": r"knights\s+of\s+columbus|caballeros\s+de\s+col[oó]n",
    "Catholic Daughters of the Americas": r"catholic\s+daughters",
    "Young Ladies' Institute": r"young\s+ladies'?\s+(grand\s+)?institute|\bYLI\b",
    "Young Men's Institute": r"young\s+men'?s\s+institute|\bYMI\b",
    "Italian Catholic Federation": r"italian\s+catholic\s+federation|\bICF\b",
    "Legion of Mary": r"legion\s+of\s+mary|legi[oó]n\s+de\s+mar[ií]a",
    "Cursillo": r"cursillo",
    "Charismatic Renewal": r"charismatic|carism[aá]tic",
    "Couples for Christ": r"couples\s+for\s+christ|parejas\s+para\s+cristo",
    "Marriage Encounter": r"marriage\s+encounter|encuentro\s+matrimonial",
    "Movimiento Familiar Cristiano": r"movimiento\s+familiar\s+cristiano",
    "Jovenes para Cristo": r"j[oó]venes\s+para\s+cristo",
    "Secular Franciscans": r"secular\s+franciscan|\bOFS\b|\bSFO\b",
    "Lay Carmelites / OCDS": r"secular\s+carmelite|lay\s+carmelite|\bOCDS\b",
    "Lay Dominicans": r"lay\s+dominican",
    "Neocatechumenal Way": r"neocatechumenal|neocatecumenal",
    "Communion and Liberation": r"communion\s+and\s+liberation",
    "Opus Dei": r"opus\s+dei",
    "Focolare": r"focolare",
    "Teams of Our Lady": r"teams\s+of\s+our\s+lady",
    "Rosary Makers": r"rosary\s+makers",
    "Apostleship of Prayer": r"apostleship\s+of\s+prayer",
    "Columbiettes": r"columbiettes",
    "Guadalupanas": r"guadalupan",
    "El Shaddai": r"el\s+shaddai",
    "Gabriel Project": r"gabriel\s+project",
    "Walking with Moms in Need": r"walking\s+with\s+moms",
    "SPRED": r"\bSPRED\b",
    "Serra Club": r"serra\s+club",
    "Order of Malta": r"order\s+of\s+malta|knights\s+of\s+malta",
    "Catholic Worker": r"catholic\s+worker",
    "Ignatian / Jesuit spirituality group": r"ignatian|spiritual\s+exercises",
    "Bereavement / Grief ministry": r"bereavement|grief\s+(ministry|support)",
    "Prison / Detention ministry": r"prison\s+ministry|detention\s+ministry|jail\s+ministry",
    "Health / Mental health ministry": r"mental\s+health\s+ministry|health\s+ministry|health\s+cabinet",
}


def page_urls(sites: dict) -> dict[str, str]:
    out = {}
    for s in sites["records"]:
        for p in s.get("pages") or []:
            if p.get("raw_file"):
                out[Path(p["raw_file"]).name + "|" + s["id"]] = p.get("url") or p.get("requested_url")
    return out


def snippet(txt: str, m: re.Match, w: int = 70) -> str:
    s = txt[max(0, m.start() - w): m.end() + w]
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    sites = load_json(REAL / "parish_sites.json")
    urls = page_urls(sites)
    fetched = sites.get("generated_at") or now_iso()
    out = []
    for s in sites["records"]:
        d = RAW / "parish_sites" / s["id"]
        if not d.exists():
            continue
        rec = {"parish_id": s["id"], "parish_name": s["name"], "crawl_status": s.get("crawl_status"),
               "religious_clergy": [], "kofc_councils": [], "svdp": None, "groups": {}, "fetched_at": s.get("crawled_at") or fetched}
        seen_cl, seen_k = set(), set()
        for f in sorted(d.glob("*.html")):
            url = urls.get(f.name + "|" + s["id"]) or s.get("website")
            soup = BeautifulSoup(f.read_text(errors="ignore"), "html.parser")
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            for m in list(CLERGY_RE.finditer(txt)) + list(CLERGY_RE2.finditer(txt)):
                title, name, pn = m.group(1), m.group(2).strip(), m.group(3)
                key = next(k for p, k in POSTNOMINALS if re.fullmatch(p, pn))
                if key == "op":
                    key = "op-sister-unresolved" if re.match(r"(?i)sr|sister", title) else "ri-op-friars"
                if key == "ri-sj" and "Barber" in name:
                    continue  # the bishop, not parish staff
                k = (name.lower().replace(".", ""), key)
                if k in seen_cl:
                    continue
                seen_cl.add(k)
                rec["religious_clergy"].append({"title": title, "name": name, "postnominal": pn.strip(),
                                                "institute_key": key, "context": snippet(txt, m), "source_url": url})
            for m in KOFC_RE.finditer(txt):
                num = m.group(2) or m.group(3)
                ctx = snippet(txt, m, 60)
                if not num or not re.search(r"(?i)knight|columbus|k\s?of\s?c|caballeros", ctx):
                    continue
                if num in seen_k:
                    continue
                seen_k.add(num)
                rec["kofc_councils"].append({"council_number": num, "context": ctx, "source_url": url})
            if rec["svdp"] is None:
                m = SVDP_RE.search(txt)
                if m:
                    rec["svdp"] = {"context": snippet(txt, m), "source_url": url}
            for g, pat in GROUPS.items():
                if g in rec["groups"]:
                    continue
                m = re.search(pat, txt, flags=re.I if g not in ("Young Ladies' Institute", "Italian Catholic Federation", "Young Men's Institute", "Secular Franciscans", "Lay Carmelites / OCDS", "SPRED") else 0)
                if m:
                    rec["groups"][g] = {"context": snippet(txt, m, 50), "source_url": url}
        out.append(rec)
    save_json(OUT, {
        "source": "data/raw/parish_sites/ (crawl by ingest/public/parish_sites.py; see data/real/README-parish-sites.md)",
        "generated_at": now_iso(), "parishes_scanned": len(out),
        "notes": ["Only the 37 parish sites that crawled OK (plus partial pages of others) have text; absence of a"
                  " signal means 'not seen on the crawled pages', not 'absent from the parish'.",
                  "religious_clergy keys are institute ids in data/real/ecosystem/orgs.json; 'OP' after Sr./Sister is"
                  " left as 'op-sister-unresolved' (several Dominican women's congregations serve here)."],
        "records": out})
    n_cl = sum(len(r["religious_clergy"]) for r in out)
    n_k = sum(len(r["kofc_councils"]) for r in out)
    n_s = sum(1 for r in out if r["svdp"])
    print(f"{len(out)} parishes scanned; {n_cl} religious clergy mentions; {n_k} KofC council numbers; {n_s} SVdP mentions")


if __name__ == "__main__":
    main()
