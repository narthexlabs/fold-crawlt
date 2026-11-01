"""Column-aware text extraction and entry parsing for the Archdiocese of Los Angeles Catholic Directory (PDF).

Used only by los-angeles.py. The directory is a typeset multi-column PDF, so plain `pdftotext` interleaves columns;
this module crops each page into its columns (found from the left edges of the words) and reads them in order.
Requires pdfplumber.
"""
from __future__ import annotations

import collections
import re

import pdfplumber

REGIONS = ["Our Lady of the Angels", "San Fernando", "San Gabriel", "San Pedro", "Santa Barbara"]
REGION_RE = "|".join(re.escape(r) for r in REGIONS)
HDR = re.compile(rf"^(?P<name>.*?)\s*(?P<region>{REGION_RE}) Region D(?P<d>\d+)\s*$")
CITYH = re.compile(r"^(?P<city>[A-ZÑÉÁÍÓÚ][A-ZÑÉÁÍÓÚ0-9 /.'’&-]*?(?: \([^)]*\))?)\s+(?P<county>Los Angeles|Ventura|Santa Barbara) County\s*$")
SKIP = re.compile(r"^(Listing of Parishes|\(Continued\)|Note: For full|The 2025 Archdiocese|\d+ \| The 2025|Archdiocese by City$|by City$|"
                  r"sh resources|he Archdiocese|Eastern Catholic Church)")
LEAD = re.compile(r"^(Pastor|Administrator|Parochial Administrator|Pastor/Administrator|Administrator/Pastor|Parish Life Director|"
                  r"Priest Moderator|Moderator|Co-Pastors?|Pastors)\s*:?\s*(.*)$", re.I)
ADDR_RE = re.compile(r"\bCA\.?,?\s+9\d{4}(?:-\d{4})?\b")
PHONE_RE = re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")


class Pdf:
    def __init__(self, path):
        self.pdf = pdfplumber.open(str(path))

    def n_pages(self) -> int:
        return len(self.pdf.pages)

    def page_text(self, pn: int) -> str:
        return self.pdf.pages[pn - 1].extract_text() or ""

    def find_pages(self, pattern: str, first: int = 1, last: int | None = None, head_chars: int = 260) -> list[int]:
        out = []
        for i in range(first, (last or self.n_pages()) + 1):
            if re.search(pattern, self.page_text(i)[:head_chars], re.I | re.S):
                out.append(i)
        return out

    def bounds(self, pn: int, min_gap: int = 120, frac: float = 0.22) -> list[float]:
        p = self.pdf.pages[pn - 1]
        c = collections.Counter(int(round(w["x0"] / 3.0)) * 3 for w in p.extract_words())
        if not c:
            return [0, p.width]
        mx = max(c.values())
        peaks: list[int] = []
        for x, n in sorted(c.items(), key=lambda t: -t[1]):
            if n >= frac * mx and all(abs(x - q) >= min_gap for q in peaks):
                peaks.append(x)
        peaks.sort()
        b = [max(0, pk - 5) for pk in peaks] + [p.width]
        b[0] = 0
        return b

    def columns(self, pn: int, bounds=None, top: float = 0, bottom: float | None = None) -> list[list[str]]:
        """Lines per column. Words are assigned to a column by their centre, so a heading centred across a boundary
        stays in one piece."""
        p = self.pdf.pages[pn - 1]
        b = bounds or self.bounds(pn)
        words = [w for w in p.extract_words(x_tolerance=1.5) if w["top"] >= top and (bottom is None or w["bottom"] <= bottom)]
        cols = []
        for a, z in zip(b[:-1], b[1:]):
            ws = sorted((w for w in words if a <= (w["x0"] + w["x1"]) / 2 < z), key=lambda w: (round(w["top"]), w["x0"]))
            lines, cur, last_top = [], [], None
            for w in ws:
                if last_top is not None and abs(w["top"] - last_top) > 3:
                    lines.append(" ".join(x["text"] for x in sorted(cur, key=lambda x: x["x0"])))
                    cur = []
                cur.append(w)
                last_top = w["top"] if not cur[:-1] else last_top
            if cur:
                lines.append(" ".join(x["text"] for x in sorted(cur, key=lambda x: x["x0"])))
            cols.append(lines)
        return cols

    def lines(self, pages, skip: re.Pattern | None = None, bounds=None) -> list[tuple[int, str]]:
        out = []
        for pn in pages:
            for col in self.columns(pn, bounds):
                for ln in col:
                    s = ln.strip()
                    if skip and s and skip.search(s):
                        continue
                    out.append((pn, s))
        return out


# ---------------------------------------------------------------------------------------------- city listing
def parse_city_listing(lines: list[tuple[int, str]]) -> list[dict]:
    """'Listing of Parishes in the Archdiocese by City': one block per parish, header 'NAME  <Region> Region D<n>'."""
    L = [(pn, re.sub(r"Re\s+gion", "Region", ln)) for pn, ln in lines if ln and not SKIP.search(ln)]
    consumed: set[int] = set()
    heads: list[tuple[int, str, str, int]] = []  # (index, name, region, deanery)
    for i, (pn, ln) in enumerate(L):
        m = HDR.match(ln)
        if not m:
            continue
        name = m.group("name").strip()
        prev = L[i - 1][1] if i else ""
        prev_core = re.sub(r"\([^)]*\)", "", prev)
        prev_is_caps = bool(prev) and not CITYH.match(prev) and re.search(r"[A-Z]{3}", prev_core) and prev_core.upper() == prev_core \
            and not re.search(r"\d|@|County$", prev_core)
        if not name or name.startswith("Established") or name.startswith("Est."):
            name = prev
            consumed.add(i - 1)
        elif prev_is_caps and (i - 1) not in consumed:
            name = prev + " " + name
            consumed.add(i - 1)
        heads.append((i, name, m.group("region"), int(m.group("d"))))
    city, county = None, None
    # city headers: walk lines, track the latest city before each header
    city_at: dict[int, tuple[str | None, str | None]] = {}
    for i, (pn, ln) in enumerate(L):
        m = CITYH.match(ln)
        if m and not HDR.match(ln):
            city, county = m.group("city"), m.group("county")
        city_at[i] = (city, county)
    out = []
    for k, (i, name, region, d) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(L)
        body = []
        for j in range(i + 1, end):
            if j in consumed or CITYH.match(L[j][1]):
                continue
            body.append(L[j][1])
        c, cty = city_at[i]
        out.append({"name_raw": name, "region": region, "deanery": d, "city_header": c, "county": cty, "pdf_page": L[i][0], "lines": body})
    return out


def parse_block(p: dict) -> dict:
    """Pull labelled fields out of one parish block."""
    L = p["lines"]
    out = {k: p[k] for k in ("name_raw", "region", "deanery", "city_header", "county", "pdf_page")}
    addrs: dict[str, str] = {}
    phones, sites, emails, lead = [], [], [], []
    fax = None
    for ln in L:
        m = re.match(r"^Est(?:ablished|\.)? in (\d{4})", ln)
        if m:
            out["established"] = int(m.group(1))
            continue
        if re.match(r"^\((?:Staffed|Served|Administered|Conducted) by", ln):
            out["staffed_by"] = ln.strip("()")
            continue
        m = re.match(r"^(Mail|Office|Church & Rectory|Church|Parish Ministry Center|Parish Center|Rectory|Chapel|Worship site|Church/Rectory|Outreach)\s*:\s*(.*)$", ln, re.I)
        if m and ADDR_RE.search(m.group(2)):
            addrs[m.group(1).lower()] = m.group(2)
            continue
        if ADDR_RE.search(ln) and not re.search(r"@|www\.", ln) and not LEAD.match(ln) and "unlabeled" not in addrs:
            addrs["unlabeled"] = ln
            continue
        if re.match(r"^\(\d{3}\)\s*\d{3}-\d{4}", ln):
            for x in [t.strip() for t in ln.split("|")]:
                if re.match(r"^Fax", x, re.I):
                    fax = re.sub(r"^Fax:?\s*", "", x, flags=re.I)
                elif re.match(r"^\(\d{3}\)", x):
                    phones.append(x)
                elif re.search(r"www\.|https?://|\.(org|com|net|us|edu)\b", x, re.I) and "@" not in x:
                    sites.append(x)
            continue
        if "@" in ln and not LEAD.match(ln) and not re.match(r"^(Deacon|Associate|Business|Admin|In Residence|Supply|School|Adoration|Executive|Office Manager|Director)", ln, re.I):
            emails += re.findall(r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}", ln)
            for x in ln.split("|"):
                if "@" not in x and re.search(r"www\.|https?://", x):
                    sites.append(x.strip())
            continue
        if not sites and re.match(r"^(www\.|https?://)\S+$", ln):
            sites.append(ln)
            continue
        m = LEAD.match(ln)
        if m:
            lead.append((m.group(1).title(), m.group(2).strip()))
            continue
        m = re.match(r"^School\s*:\s*(.*)$", ln, re.I)
        if m:
            out.setdefault("schools", []).append(m.group(1).strip())
            continue
        m = re.match(r"^Mission\s*:\s*(.*)$", ln)
        if m:
            out.setdefault("parochial_missions", []).append(m.group(1).strip())
    out.update(addresses=addrs, phones=phones, fax=fax, sites=sites, emails=emails, leaders=lead)
    return out


# ---------------------------------------------------------------------------------------------- generic entries
def is_caps(line: str) -> bool:
    t = re.sub(r"\([^)]*\)", "", line)
    t = re.sub(r"\b(High School|Academy|Middle School|Co-ed|Girls|Boys|Preparatory|School|Cemetery|Mortuary|Mausoleum|Inc\.|dba|DBA|Campus)\b", "", t)
    letters = [ch for ch in t if ch.isalpha()]
    return len(letters) >= 3 and all(ch.isupper() for ch in letters)


def parse_entries(lines: list[tuple[int, str]]) -> list[dict]:
    """Anchor on address lines ('... CA 9xxxx'); the name is the run of ALL-CAPS lines above; the body runs to the next name."""
    L = lines
    anchors = [i for i, (_, ln) in enumerate(L) if ADDR_RE.search(ln)]
    entries = []
    for i in anchors:
        a_line, j = L[i][1], i
        if not re.match(r"^(P\.? ?O\.? Box|\d|Mail:|Office:|c/o|[A-Z][\w ]*: )", a_line) and i > 0 and L[i - 1][1] \
                and not is_caps(L[i - 1][1]) and not PHONE_RE.search(L[i - 1][1]) and not ADDR_RE.search(L[i - 1][1]):
            a_line, j = L[i - 1][1] + " " + a_line, i - 1
        n, name = j - 1, []
        while n >= 0 and L[n][1] and is_caps(L[n][1]):
            name.insert(0, L[n][1])
            n -= 1
        entries.append({"i": i, "name_lines": name, "name_start": n + 1, "addr": a_line, "pdf_page": L[i][0]})
    for k, e in enumerate(entries):
        end = entries[k + 1]["name_start"] if k + 1 < len(entries) else len(L)
        e["body"] = [ln for _, ln in L[e["i"] + 1:end] if ln]
        e["pre"] = []
    return entries


# ---------------------------------------------------------------------------------------------- name helpers
SMALL = {"of", "the", "and", "de", "del", "y", "a", "at", "in", "on", "for", "to", "by", "with", "dba"}
KEEP_UPPER = {"II", "III", "IV", "USC", "LAC", "LA", "OLA", "UCLA", "LMU", "CSU", "UC", "YMCA", "CYO", "O.F.M.", "S.J.", "TK", "STEM", "KCET",
              "CA", "PA", "HS", "RC", "OSB", "SVDP"}


def title_case(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()

    def one(tok: str, first: bool, prev: str = "") -> str:
        if not tok:
            return tok
        if tok.lower() == "la" and prev.lower() == "de":
            return "la"
        core = re.sub(r"[^\w.]", "", tok)
        if core.upper() in KEEP_UPPER and not tok.startswith(("“", '"')):
            return tok
        low = tok.lower()
        if low in SMALL and not first:
            return low
        if re.match(r"^st\.?$", low):
            return "St." if low.endswith(".") else "St"
        if low.startswith("(") and len(low) > 1:
            return "(" + one(tok[1:], True)
        parts = re.split(r"([’'/-])", low)
        res = []
        for idx, part in enumerate(parts):
            if part in "’'/-" and part:
                res.append(part)
            elif part:
                if idx > 0 and parts[idx - 1] in "’'" and part in ("s", "t", "d", "ll"):
                    res.append(part)
                else:
                    res.append(part[:1].upper() + part[1:])
        return "".join(res)
    toks = s.split(" ")
    return " ".join(one(t, i == 0, toks[i - 1] if i else "") for i, t in enumerate(toks))


# ---------------------------------------------------------------------------------------------- missions, chapels, centers
def parse_missions(pdf: Pdf, pn: int) -> list[dict]:
    """'Parochial Missions, Chapels, and Centers' (three columns; entry = name [phone], address, '(parent church)')."""
    out = []
    for col in pdf.columns(pn, [0, 211, 394, 603], top=58):
        city, name_buf, cur, group_parent, group_phone = None, [], None, None, None
        i, n = 0, len(col)
        while i < n:
            ln = col[i].strip()
            i += 1
            if not ln or re.search(r"Parochial Missions|Chapels, and|and Centers|The 2025 Archdiocese", ln):
                continue
            if is_caps(ln) and not re.search(r"\d", ln) and not PHONE_RE.search(ln):
                city, name_buf, cur = ln.title().replace(" / ", "/"), [], None
                group_parent = group_phone = None
                continue
            m = re.match(r"^All with (.*)$", ln)
            if m:
                group_parent = m.group(1)
                continue
            if re.match(r"^\(\d{3}\)\s*\d{3}-\d{4}$", ln) and not name_buf:
                group_phone = ln
                continue
            if ADDR_RE.search(ln):
                street = ln
                if name_buf and re.match(r"^\d", name_buf[-1]):  # street on its own line, 'city, CA zip' here
                    street = name_buf.pop() + " " + ln
                name = " ".join(name_buf).strip()
                ph = PHONE_RE.search(name)
                phone = ph.group(0) if ph else group_phone
                name = re.sub(r"\s*\(\d{3}\)\s*\d{3}-\d{4}", "", name).strip()
                if not name:
                    continue
                cur = {"name": re.sub(r"\s+", " ", name), "address_line": street, "phone": phone, "city_header": city,
                       "parent": group_parent, "pdf_page": pn}
                out.append(cur)
                name_buf = []
                continue
            if cur is not None and ln.startswith("(") and not PHONE_RE.search(ln) and cur.get("parent") in (None, group_parent):
                cur["parent"] = ln.strip("()") if not cur.get("parent") or cur["parent"] == group_parent else cur["parent"]
                if ln.endswith(")") is False and i < n and ")" in col[i]:
                    cur["parent"] += " " + col[i].strip().rstrip(")")
                    i += 1
                if cur["parent"]:
                    cur["parent"] = cur["parent"].strip("()")
                name_buf = []
                cur = None
                continue
            if cur is not None and not ln.startswith("("):
                cur = None
            if ln.startswith("(") and not PHONE_RE.search(ln):  # a parent line with no entry open: ignore
                continue
            name_buf.append(ln)
    return out


# ---------------------------------------------------------------------------------------------- eastern catholic churches
RITE_RE = re.compile(r"^(ARMENIAN|ASSYRIAN|BYZANTINE|CHALDEAN|COPTIC|ETHIOPIAN|ERITREAN|MARONITE|MELKITE|ROMANIAN|RUSSIAN|SYRIAC|SYRO|UKRAINIAN|ANTIOCHENE)[A-Z \-]*$")
E_HDR = re.compile(rf"^(?P<name>.*?)\s*(?P<tail>(?:{REGION_RE}) Region D(?P<d>\d+)|Diocese of [A-Za-z ]+?)\s*$")


def parse_eastern(pdf: Pdf, pages) -> list[dict]:
    L = [(pn, ln) for pn, ln in pdf.lines(pages, bounds=[0, 301, 603]) if ln and not re.search(r"^(Eastern Catholic|The 2025 Archdiocese|\(Continued\)|\d+ \| The 2025)", ln)]
    heads = []
    rite = None
    for i, (pn, ln) in enumerate(L):
        if RITE_RE.match(ln) and len(ln) < 40:
            rite = ln
            L[i] = (pn, "")
            continue
        m = E_HDR.match(ln)
        if m and (m.group("tail").startswith("Diocese of") or m.group("d")) and len(ln) < 160:
            name = m.group("name").strip()
            j = i
            if name.startswith("("):
                k = i - 1
                prev = []
                while k >= 0 and len(prev) < 2 and L[k][1] and (is_caps(L[k][1]) or re.match(r"^[A-Z .'-]+(\(|$)", L[k][1])):
                    prev.insert(0, L[k][1])
                    L[k] = (L[k][0], "")
                    k -= 1
                name = " ".join(prev) + " " + name
            elif not name or name.startswith("Est."):
                # name on the previous non-empty line(s)
                k = i - 1
                while k >= 0 and not L[k][1]:
                    k -= 1
                name = (L[k][1] + (" " + name if name and not name.startswith("Est.") else "")).strip()
                L[k] = (L[k][0], "")
                est = re.search(r"Est\. in (\d{4})", ln)
            heads.append({"i": i, "name": name, "tail": m.group("tail"), "rite": rite, "pn": pn,
                          "region": re.sub(r" Region D\d+", "", m.group("tail")) if m.group("d") else None,
                          "deanery": int(m.group("d")) if m.group("d") else None})
    out = []
    for k, h in enumerate(heads):
        end = heads[k + 1]["i"] if k + 1 < len(heads) else len(L)
        body = [ln for _, ln in L[h["i"]:end] if ln]
        # the previous parish's tail text may include the next header's name line; trim lines equal to next name
        blk = {"name_raw": h["name"], "region": h["region"] or "", "deanery": h["deanery"] or 0, "city_header": None, "county": None,
               "pdf_page": h["pn"], "lines": body[1:] if body else []}
        f = parse_block(blk)
        f.update(rite=h["rite"], tail=h["tail"])
        out.append(f)
    return out


# ---------------------------------------------------------------------------------------------- ethnic liturgies (languages)
def parse_ethnic_liturgies(pdf: Pdf, pages, cities: set[str]) -> list[dict]:
    """Directory of Ethnic Liturgies: language heading (CAPS), city heading, then 'Parish  (phone)' lines.
    Returns [{language, city, name, phone}]."""
    out = []
    lang, city, buf = None, None, []
    for pn in pages:
        top = 0
        if pn == pages[0]:
            hw = [w for w in pdf.pdf.pages[pn - 1].extract_words() if w["text"] == "Ethnic" and w["x0"] > 150]
            top = (hw[0]["top"] - 2) if hw else 0
        for col in pdf.columns(pn, [0, 211, 394, 603], top=top):
            for raw in col:
                ln = raw.strip()
                if not ln or re.search(r"^(Directory of Ethnic|\(Continued\)|The 2025 Archdiocese|\d+ \| The 2025|Office of Ethnic|Vicar:|Executive Board|Treasurer|Vicar’s|\(See )", ln):
                    continue
                if is_caps(ln) and not PHONE_RE.search(ln) and len(ln) < 60:
                    if buf:
                        buf = []
                    lang = re.sub(r"\s+", " ", ln).title()
                    city = None
                    continue
                if lang is None:
                    continue
                if re.match(r"^\((Interpreted|Mass in|Latin|Spanish|Armenian|Syro|Chaldean)", ln) and not PHONE_RE.search(ln):
                    buf.append(ln)
                    continue
                m = re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", ln)
                if m:
                    nm = " ".join(buf + [ln[:m.start()].strip()]).strip()
                    out.append({"language": lang, "city": city, "name": re.sub(r"\s+", " ", nm).strip(" /"), "phone": C_norm(m.group(0))})
                    buf = []
                    continue
                key = re.sub(r"[^a-z]", "", ln.lower())
                if key in cities:
                    city, buf = ln, []
                else:
                    buf.append(ln)
    return out


def C_norm(p: str) -> str:
    m = re.search(r"\((\d{3})\)\s*(\d{3})-(\d{4})", p)
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}" if m else p


# ---------------------------------------------------------------------------------------------- name-line heuristics (entity sections)
NAME_WORD2 = {"High", "Academy", "Middle", "Preparatory", "Secondary", "School", "Prep", "Junior", "Elementary", "Campus"}


def is_name_line(ln: str) -> bool:
    if not ln or re.search(r"\d|@|www\.|:\s", ln) or PHONE_RE.search(ln):
        return False
    toks = ln.split()
    if not toks:
        return False
    up = lambda t: len(re.sub(r"[^A-Za-z]", "", t)) >= 2 and re.sub(r"[^A-Za-z]", "", t).isupper()
    if len(toks) == 1:
        return up(toks[0]) and len(toks[0]) >= 4
    if toks[0].upper() in ("ST.", "STS.") and len(toks) >= 2 and up(toks[1]):
        return True
    return (up(toks[0]) and (up(toks[1]) or toks[1] in NAME_WORD2 or toks[1].strip("(") in ("(Co-ed)",))) or is_caps(ln)


def entries_by_address(lines: list[tuple[int, str]], city_names: set[str] | None = None) -> list[dict]:
    """Like parse_entries, but name = up to three name-like lines above the address; a lone city heading line is split off."""
    L = lines
    anchors = [i for i, (_, ln) in enumerate(L) if ADDR_RE.search(ln)]
    entries = []
    for i in anchors:
        a_line, j = L[i][1], i
        if not re.match(r"^(P\.? ?O\.? Box|\d|Mail:|Office:|c/o|[A-Z][\w ]*: |Newman)", a_line) and i > 0 and L[i - 1][1] \
                and not is_name_line(L[i - 1][1]) and not PHONE_RE.search(L[i - 1][1]) and not ADDR_RE.search(L[i - 1][1]):
            a_line, j = L[i - 1][1] + " " + a_line, i - 1
        n, name = j - 1, []
        while n >= 0 and L[n][1] and len(name) < 4 and is_name_line(L[n][1]):
            name.insert(0, L[n][1])
            n -= 1
        city_hdr = None
        if len(name) > 1 and (city_names is None or re.sub(r"[^a-z]", "", name[0].lower()) in city_names):
            city_hdr, name = name[0], name[1:]
        elif len(name) > 1 and re.fullmatch(r"[A-Z ]+", name[0]) and len(name[0].split()) <= 3 and not re.search(r"SCHOOL|CENTER|HOUSE|SEMINARY|CEMETERY|HIGH|ACADEMY", name[0]):
            city_hdr, name = name[0], name[1:]
        entries.append({"i": i, "name_lines": name, "name_start": n + 1, "city_header": city_hdr, "addr": a_line, "pdf_page": L[i][0]})
    for k, e in enumerate(entries):
        end = entries[k + 1]["name_start"] if k + 1 < len(entries) else len(L)
        e["body"] = [ln for _, ln in L[e["i"] + 1:end] if ln]
    return entries


def elementary_entries(lines: list[tuple[int, str]]) -> list[dict]:
    """Parochial/non-parochial elementary schools: 'NAME' / 'Enrollment: N (grades)' / address / phone | web / e-mail / Principal / Region."""
    L = lines
    out = []
    idx = [i for i, (_, ln) in enumerate(L) if ln.startswith("Enrollment:")]
    for k, i in enumerate(idx):
        n, name = i - 1, []
        while n >= 0 and L[n][1] and len(name) < 3 and (is_name_line(L[n][1]) or re.search(r"SCHOOL|ACADEMY|ELEMENTARY|MIDDLE", L[n][1])):
            if re.match(r"^\(.*\)$", L[n][1]):
                break
            name.insert(0, L[n][1])
            n -= 1
        city = None
        if len(name) > 1 and re.fullmatch(r"[A-Z ]+", name[0]) and not re.search(r"SCHOOL|ACADEMY|ELEMENTARY|MIDDLE", name[0]):
            city, name = name[0], name[1:]
        end = (idx[k + 1] - len(name) - 2) if k + 1 < len(idx) else len(L)
        body = [ln for _, ln in L[i:max(end, i + 1) + 1] if ln]
        # stop the body where the next entry's header begins
        nxt = idx[k + 1] if k + 1 < len(idx) else len(L)
        body = []
        for j in range(i, nxt):
            ln = L[j][1]
            if j > i + 1 and ln and is_name_line(ln) and j + 1 < nxt + 1 and L[j + 1][1].startswith("Enrollment:") if j + 1 < len(L) else False:
                break
            if j > i + 1 and re.fullmatch(r"[A-Z ]{4,}", ln or "") and j + 1 < len(L) and (L[j + 1][1].startswith("Enrollment:") or is_name_line(L[j + 1][1])):
                break
            if ln:
                body.append(ln)
        out.append({"name_lines": name, "city_header": city, "body": body, "pdf_page": L[i][0]})
    return out


def heading_entries(lines: list[tuple[int, str]]) -> list[dict]:
    """Entries that open with an ALL-CAPS organisation heading (1-2 lines), optional mixed-case centre-name lines, then the address.
    Used for campus ministry and houses of formation."""
    out, caps, mixed, cur, seen_addr = [], [], [], None, False
    for pn, ln in lines:
        if not ln:
            continue
        if is_caps(ln) and not PHONE_RE.search(ln) and not ADDR_RE.search(ln) and len(ln) < 70:
            if seen_addr or cur is None and not caps and not mixed:
                if seen_addr:
                    caps, mixed, cur, seen_addr = [], [], None, False
            caps.append(ln)
            continue
        if ADDR_RE.search(ln) and not seen_addr:
            street = ln
            if mixed and re.match(r"^\d", mixed[-1]):
                street = mixed.pop() + " " + ln
            elif mixed and re.match(r"^(Newman Club|Chalon Campus|Doheny Campus)", mixed[-1]) is None and not re.match(r"^\d|^[A-Z][a-z]+,? CA", ln):
                pass
            cur = {"org_lines": list(caps), "mixed": [m for m in mixed if not m.startswith("(")], "paren": [m for m in mixed if m.startswith("(")],
                   "addr": street, "body": [], "pdf_page": pn}
            out.append(cur)
            seen_addr = True
            continue
        if seen_addr and cur is not None:
            cur["body"].append(ln)
        else:
            mixed.append(ln)
    return out
