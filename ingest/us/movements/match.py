"""Attach movement records to our parishes and write data/real/us/movements/by_parish.json.

    python3 -I ingest/us/movements/match.py
Reads every parish-level source file (kofc.json, paradisus_dei.json, secular_franciscans.json, aoh.json), adds a `match`
field to each record ({parish_id, parish_name, parish_city, state, confidence exact|fuzzy|unmatched, method}) and writes
by_parish.json: parish id -> groups [{name, category, subcategory, audience, language, source, label, meeting_time, url,
source_url, fetched_at, confidence, dedupe}]. Only exact and fuzzy matches are attached; unmatched records stay in the
source file for a human or a later pass. Matching: common.Matcher (same name in the same city / ZIP, distinctive words,
same street address, or a church meeting place at the parish's own map pin).
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

LABEL = {"kofc": "From the Knights of Columbus's directory",
         "paradisus_dei": "From Paradisus Dei's group finder (That Man Is You! and other programs)",
         "ofs": "From the Secular Franciscans' (OFS-USA) fraternity map",
         "aoh": "From the Ancient Order of Hibernians' division directory"}
PAGE = {"kofc": "https://www.kofc.org/get-involved/find-a-council/",
        "paradisus_dei": "https://programs.paradisusdei.org/search",
        "ofs": "https://www.secularfranciscansusa.org/map-of-local-fraternities/",
        "aoh": "https://aoh.com/aoh-directory/"}
# Paradisus Dei program tag -> (group name, category, subcategory, audience, language, dedupe pattern)
PD = {"TMIY": ("That Man Is You!", "men", "that_man_is_you", "men", "en", r"that man is you|\btmiy\b"),
      "EHET": ("EHET (men's program in Spanish, Paradisus Dei)", "men", "ehet", "men", "es", r"\behet\b"),
      "SHE": ("She Shall Be Called Woman", "women", "she_shall_be_called_woman", "women", "en", r"she shall be called woman"),
      "Mysteries": ("Mysteries of the Rosary (Paradisus Dei series)", "prayer_devotion", "mysteries_of_the_rosary", "all", "en",
                    r"mysteries of the rosary"),
      "Choice Wine": ("The Choice Wine (Paradisus Dei marriage series)", "marriage_family", "the_choice_wine", "all", "en",
                      r"choice wine")}
PLACEHOLDER = re.compile(r"^(\[name withheld\]|required|tbd|tba|none|n/?a|varies|various|unknown|online|zoom)\b", re.I)
PLACE_TAIL = re.compile(r"\b(parish|church)?\s*(center|centre|hall|social hall|activity center|ministry center|life center|"
                        r"school|rectory|office|offices|annex|gym|library|meeting room|room \w+|lower level|basement|chapel)\s*$", re.I)


def title(s: str) -> str:
    s = re.sub(r"\b(knights of columbus|k\s*of\s*c|kofc)\b", " ", s or "", flags=re.I).strip(" -,")
    small = {"of", "the", "and", "de", "la", "del", "y"}
    w = (s or "").lower().split()
    return " ".join(x if (i and x in small) else (x[:1].upper() + x[1:]) for i, x in enumerate(w))


NOT_PARISH = re.compile(r"\b(seminary|friary|monastery|convent|motherhouse|hospital|medical|university|college|nursing|"
                        r"retirement|senior living|assisted living|manor|villa|retreat|hermitage|abbey|priory|novitiate)\b", re.I)


def place(n: str | None) -> str | None:
    """a meeting place as a parish name: placeholders dropped, 'Holy Rosary Parish Center' -> 'Holy Rosary Parish'."""
    n = (n or "").strip()
    if not n or PLACEHOLDER.match(n):
        return None
    if NOT_PARISH.search(n):   # a seminary / friary / retreat house is not the parish of the same saint: keep it whole
        return n
    for _ in range(2):
        m = PLACE_TAIL.search(n)
        if m and len(n) - len(m.group(0)) > 4:
            n = n[: m.start()].strip(" ,-") + (" Parish" if m.group(1) else "")
    return n


def web(u: str | None) -> str | None:
    if u and not u.lower().startswith("http"):
        u = "https://" + u
    return u or None


def stamp(r: dict, p, conf: str, how: str) -> None:
    r["match"] = {"parish_id": p["id"] if p else None, "parish_name": p["name"] if p else None,
                  "parish_city": p.get("city") if p else None, "state": r.get("state"), "confidence": conf, "method": how}


def row(src: str, name: str, cat: str, sub: str, aud: str, lang: str | None, meets, url, r: dict, conf: str, dedupe: str | None) -> dict:
    return {"name": name, "category": cat, "subcategory": sub, "audience": aud, "language": lang, "source": src,
            "label": LABEL[src], "meeting_time": meets, "url": url, "source_url": PAGE[src], "api_url": r.get("source_url"),
            "fetched_at": r.get("fetched_at"), "confidence": conf, "dedupe": dedupe}


def main() -> None:
    parishes = c.load_parish_index()
    M = c.Matcher(parishes)
    by_id = {p["id"]: p for p in parishes}
    by_parish: dict = {}
    stats: dict = {}
    gained: dict = {}

    def add(pid: str, g: dict, src: str) -> None:
        by_parish.setdefault(pid, []).append(g)
        gained.setdefault(src, set()).add(pid)

    # 1. Knights of Columbus councils (type R)
    d = c.load(c.OUT / "kofc.json")
    if d:
        cnt = Counter()
        for r in d["records"]:
            if (r.get("type") or "R") != "R":   # college councils (type C) belong to a campus, not a parish
                r["match"] = {"parish_id": None, "confidence": "unmatched", "method": "college council (type C)"}
                cnt["college"] += 1
                continue
            alts = [x for x in re.split(r"[/,&]", r.get("location") or "") if x.strip()]
            p, conf, how = M.match(r.get("councilName"), r.get("state"), r.get("city"), alts, r.get("zipCode"))
            stamp(r, p, conf, how)
            cnt[conf] += 1
            if p:
                add(p["id"], row("kofc", f"Knights of Columbus Council {r['councilNumber']} ({title(r['councilName'])})",
                                 "fraternal_orders", "knights_of_columbus", "men", None, None, web(r.get("webSite")), r, conf, None), "kofc")
        c.save(c.OUT / "kofc.json", d, list_key="records")
        stats["kofc"] = dict(cnt)

    # 2. Paradisus Dei program parishes (That Man Is You!, She Shall Be Called Woman, ...)
    d = c.load(c.OUT / "paradisus_dei.json")
    if d:
        cnt = Counter()
        for r in d["records"]:
            p, conf, how = M.match(r.get("name"), r.get("state"), r.get("city"), (), r.get("zip"), r.get("lat"), r.get("lng"), r.get("address"))
            stamp(r, p, conf, how)
            cnt[conf] += 1
            if p:
                for tag in r.get("programs") or []:
                    if tag in PD:
                        nm, cat, sub, aud, lang, pat = PD[tag]
                        add(p["id"], row("paradisus_dei", nm, cat, sub, aud, lang, None, None, r, conf, pat), "paradisus_dei")
        c.save(c.OUT / "paradisus_dei.json", d, list_key="records")
        stats["paradisus_dei"] = dict(cnt)

    # 3. Secular Franciscan fraternities (meeting place + address)
    d = c.load(c.OUT / "secular_franciscans.json")
    if d:
        cnt = Counter()
        for r in d["records"]:
            pl = place(r.get("meeting_place"))
            alt = M.city_in(r.get("address") or "", r.get("state") or "")
            p, conf, how = M.match(pl, r.get("state"), r.get("city"), [alt] if alt else (), r.get("zip"), r.get("lat"), r.get("lng"), r.get("street"))
            if p and pl and NOT_PARISH.search(pl) and conf != "exact":
                p, conf, how = None, "unmatched", f"meets at a non-parish house ({pl}); only an exact match would count"
            stamp(r, p, conf, how)
            cnt[conf] += 1
            if p:
                fr = re.sub(r"-[^-]+-[A-Z]{2}$", "", r.get("fraternity") or "").strip(" -")
                add(p["id"], row("ofs", f"Secular Franciscan Order: {fr} Fraternity" if fr else "Secular Franciscan Order fraternity",
                                 "fraternal_orders", "secular_franciscans", "all", None, None, None, r, conf,
                                 r"secular franciscan|\bofs\b|third order of st\.? francis|franciscan (secular|lay|third)"), "ofs")
        c.save(c.OUT / "secular_franciscans.json", d, list_key="records")
        stats["ofs"] = dict(cnt)

    # 4. Ancient Order of Hibernians divisions (meeting place + address)
    d = c.load(c.OUT / "aoh.json")
    if d:
        cnt = Counter()
        for r in d["records"]:
            pl = place(r.get("meeting_place"))
            if not (pl or r.get("street")):
                r["match"] = {"parish_id": None, "confidence": "unmatched", "method": "no meeting place listed"}
                cnt["no_place"] += 1
                continue
            p, conf, how = M.match(pl, r.get("state"), r.get("city"), (), r.get("zip"), None, None, r.get("street"))
            if p and pl and NOT_PARISH.search(pl) and conf != "exact":
                p, conf, how = None, "unmatched", f"meets at a non-parish house ({pl}); only an exact match would count"
            if p and not pl:   # a bare street address with no place name: only an exact street match counts
                conf = conf if how == "same street address" else "unmatched"
                p = p if conf != "unmatched" else None
            stamp(r, p, conf, how)
            cnt[conf] += 1
            if p:
                add(p["id"], row("aoh", f"Ancient Order of Hibernians: {r['division']}", "fraternal_orders",
                                 "ancient_order_of_hibernians", "men", None, r.get("meeting_time"), r.get("website"), r, conf,
                                 r"ancient order of hibernians|\baoh\b|hibernian"), "aoh")
        c.save(c.OUT / "aoh.json", d, list_key="records")
        stats["aoh"] = dict(cnt)

    first = [pid for pid in by_parish if not by_id.get(pid, {}).get("has_groups")]
    per_src = {s: {"parishes": len(v), "parishes_without_other_groups": sum(1 for x in v if not by_id.get(x, {}).get("has_groups"))}
               for s, v in gained.items()}
    c.save(c.OUT / "by_parish.json", {
        "built_at": c.now_iso(), "built_by": "ingest/us/movements/match.py",
        "note": "parish id (Find build page id) -> groups read from national movement directories; exact/fuzzy matches only",
        "stats": {"by_source": stats, "parishes_by_source": per_src, "parishes_with_groups": len(by_parish),
                  "parishes_gaining_first_group": len(first)},
        "parishes": dict(sorted(by_parish.items()))})
    print(stats); print(per_src); print(len(by_parish), "parishes;", len(first), "gain their first group")


if __name__ == "__main__":
    main()
