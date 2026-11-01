"""Rank the media feed automatically: judge each item, score it, balance the feed, set its auto status.

    python3 ingest/public/media/feed_build.py          # gather candidates -> data/real/media/feed.json
    python3 ingest/public/media/rank.py                # judge + score + diversity + auto status (rewrites feed.json)
    python3 ingest/public/media/rank.py --overrides data/raw/media/_override_log.json   # also export training rows
    python3 ingest/public/media/feed_sql.py OUT_DIR    # SQL to load it (keeps human overrides)

Design, weights and the reasoning behind them: docs/design/media-ranking.md. Weights: rank_config.json.

Judges (one interface, `judge(items) -> list[dict]`):
  * LLMJudge   - Anthropic Messages API; needs ANTHROPIC_API_KEY. Sends title, description, organization and type,
                 duration, language and (when retrievable) YouTube captions; asks for the JSON in JUDGMENT_SPEC.
  * HeuristicJudge - deterministic keyword/metadata fallback with the same output shape (judged_by: heuristic).
Judgments are cached in data/real/media/judgments.json by item id, so reruns do not re-pay. An LLM judgment is never
replaced by a heuristic one; a heuristic judgment is replaced as soon as a key is available (or with --rejudge).
"""
from __future__ import annotations

import collections
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MEDIA = ROOT / "data" / "real" / "media"
CFG = json.loads((HERE / "rank_config.json").read_text())
NOW = datetime.now(timezone.utc)
HEURISTIC_VERSION = "h4"

CONTENT_TYPES = ["teaching", "witness", "liturgy_special", "liturgy_routine", "news", "event_promo", "school_internal",
                 "fundraising", "other"]
BREADTHS = ["one_parish", "deanery", "diocese", "universal_church"]
PILLARS = ["sunday_mass", "works_of_mercy", "missionary_disciples"]
MINISTRIES = ["sustain", "build", "heal", "empower"]

JUDGMENT_SPEC = """Return a JSON array, one object per item, in the same order, each exactly:
{"id": "<item id>",
 "content_type": one of teaching|witness|liturgy_special|liturgy_routine|news|event_promo|school_internal|fundraising|other,
 "breadth": one of one_parish|deanery|diocese|universal_church  (who would care: one community only, or any Catholic),
 "substance": 0-1 (does it teach, inspire, or show the Church doing something real),
 "timelessness": 0-1 (still worth watching in a year),
 "completeness": 0-1 (a whole piece vs a fragment, teaser or promo),
 "production": 0-1 (watchable sound/picture/structure, judged from metadata),
 "pillars": subset of ["sunday_mass","works_of_mercy","missionary_disciples"],
 "appeal_ministries": subset of ["sustain","build","heal","empower"]  (Sustain Our Family = charity, food, housing;
     Build Our Future = vocations, seminarians, clergy; Heal Our Members = counseling, sick, grief, prisons;
     Empower Our Mission = youth, schools, faith formation),
 "language": ISO 639-1 code,
 "safety": {"names_private_person": bool, "sensitive": bool, "note": short string or null},
 "highlight": null or {"start_s": int, "end_s": int, "why": short string}  (only when captions with timestamps were
     given and one part - e.g. a homily inside a Mass - is clearly the best 2-15 minutes),
 "reason": one plain sentence a parishioner would understand, saying why this is or is not worth featuring}"""

SYSTEM = ("You judge items for a Catholic diocese's public media feed. Judge substance, not popularity: a seminary "
          "video that teaches an idea is excellent (teaches, timeless, interesting to Catholics at any parish); a "
          "school's spirit-week video is fun but internal to one school (school_internal); a routine Sunday or daily "
          "Mass livestream is liturgy_routine; a special liturgy (ordination, All Souls, confirmation, Chrism Mass, "
          "a feast, a clergy funeral) is liturgy_special. Never rank parishes against each other. Flag any title or "
          "description that names a private person (parishioner, student, deceased lay person, donor). "
          "Answer with JSON only.")


# --------------------------------------------------------------------------------------------- judges
class HeuristicJudge:
    """Keyword and metadata rules. Weak at substance (it cannot watch the video), honest about it in `reason`."""
    name = "heuristic"

    TEACH = re.compile(r"\b(explains?|explained|what is|what are|why (do|does|is|are|did|should|can)|how (to|do|can|does)|"
                       r"is it a sin|lecture|lesson|catechesis|catequesis|theolog|bible study|scripture|church teaching|"
                       r"q ?& ?a|faq|teaches?|teaching|explainer|doctrine|sacrament of|catechism|lectio|examen|"
                       r"apologetics|homily|homil[ií]a|reflection|reflexi[oó]n|meditation|retreat talk|talk:|"
                       r"part \d+ of|episode|ep\.? ?\d+|podcast|series)\b|\?\s*(\||$)", re.I)
    WITNESS = re.compile(r"\b(story|stories|testimony|testimonio|journey|vocation|calling|my path|spotlight|meet |"
                         r"interview|entrevista|profile|sister'?s story|reflects|witness|mission trip|serving|volunteer|"
                         r"service project|immersion|pilgrimage|peregrinaci|jubilarian|100th|years of)\b", re.I)
    SPECIAL = re.compile(r"\b(ordination|ordained|ordains|confirmation|confirmaci[oó]n|solemnity|jubilee|jubileo|"
                         r"dedication|installation|chrism|easter vigil|christmas|navidad|simbang|guadalupe|all saints|"
                         r"all souls|d[ií]a de (los )?muertos|first communion|primera comuni[oó]n|rite of election|"
                         r"white mass|red mass|blue mass|religious jubilarians|final vows|first vows|profession|"
                         r"funeral mass for (bishop|fr\.?|father|msgr|deacon|sister)|corpus christi procession|"
                         r"eucharistic (congress|revival|procession)|las posadas|holy thursday|good friday|palm sunday|"
                         r"ash wednesday|pentecost sunday|solemn mass)\b", re.I)
    SCHOOL_INTERNAL = re.compile(r"\b(spirit week|rally|homecoming|club rush|sports talk|varsity|jv|football|basketball|"
                                 r"soccer|volleyball|baseball|softball|game|playoffs?|dance|prom|back[- ]to[- ]school|"
                                 r"maker faire|capstone|vapa|senior|freshman|class of|giving week|take off|huddle|"
                                 r"#\w+|preview video|open house|shadow day|admissions?|21 acres|make your impact)\b", re.I)
    FUNDRAISING = re.compile(r"\b(appeal|giving (day|week|tuesday)|gala|auction|donat|campaign|fundrais|capital|"
                             r"raffle|golf tournament|crab feed|stewardship)\b", re.I)
    EVENT = re.compile(r"\b(join us|register|registration|save the date|coming up|upcoming|invit|tickets|rsvp|sign up|"
                       r"this (sunday|saturday|week)|announcing)\b", re.I)
    PROMO = re.compile(r"\b(promo|trailer|teaser|preview|#shorts|short)\b", re.I)
    DATEISH = re.compile(r"\b(\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?|20\d\d \d\d \d\d|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2})\b", re.I)
    SENSITIVE = re.compile(r"\b(abuse|abused|lawsuit|bankruptcy|chapter 11|settlement|scandal|allegation|accused|"
                           r"ice raid|deport|shooting|killed|arrest)\b", re.I)
    UNIVERSAL_ORG = {"seminary", "higher_education", "religious_institute", "diocese", "media"}
    PILLAR_RX = {
        "sunday_mass": re.compile(r"\b(mass|misa|eucharist|liturg|sunday|domingo|homily|homil|choir|coro|music|hymn|"
                                  r"sacrament|adoration|worship|lector|altar)\b", re.I),
        "works_of_mercy": re.compile(r"\b(food|pantry|meal|homeless|shelter|hunger|poor|poverty|charity|caridad|refugee|"
                                     r"immigra|prison|jail|detention|sick|hospital|healing|grief|mercy|vincent|svdp|"
                                     r"service|volunteer|housing|clothing|justice|care for|seniors?)\b", re.I),
        "missionary_disciples": re.compile(r"\b(formation|formaci|youth|j[oó]venes|young adult|ocia|rcia|catechesis|"
                                           r"catequesis|bible|scripture|evangeli|mission|vocation|seminar|retreat|"
                                           r"discipl|teach|explains|faith|prayer|pray|saint|discern|marriage|family)\b", re.I),
    }
    MINISTRY_FROM_TAGS = {"sustain": "sustain", "build": "build", "heal": "heal", "empower": "empower"}

    def judge(self, items: list[dict], captions: dict[str, str] | None = None) -> list[dict]:
        return [self._one(it) for it in items]

    def _one(self, it: dict) -> dict:
        t = it.get("title") or ""
        s = it.get("summary") or ""
        text = f"{t} {s}"
        ot = it.get("org_type") or "other"
        kind = it["kind"]
        dur = it.get("duration_s")
        why = []
        # content type
        if it.get("prefilter") == "routine_liturgy" and not self.SPECIAL.search(re.sub(r"(sunday|week) (after|of) pentecost", "", t, flags=re.I)):
            ct = "liturgy_routine"; why.append("a routine Mass stream")
        elif self.SPECIAL.search(t):
            ct = "liturgy_special" if re.search(r"\b(mass|misa|liturg|ordination|confirmation|vigil|rite|procession|vows|profession|dedication|installation)\b", t, re.I) or kind == "video" else "event_promo"
            why.append("a special liturgy or feast" if ct == "liturgy_special" else "an event for a feast")
        elif ot == "school" and not re.search(r"\b(mass|liturg|retreat|service|faith|catholic|theolog|prayer|campus ministry|immersion)\b", text, re.I):
            ct = "school_internal"; why.append("school life, mostly of interest to that school")
        elif self.FUNDRAISING.search(t):
            ct = "fundraising"; why.append("a fundraising message")
        elif kind in ("event", "bulletin_item") or self.EVENT.search(t):
            ct = "event_promo"; why.append("an invitation to an event")
        elif self.TEACH.search(t):
            ct = "teaching"; why.append("teaches or explains something of the faith")
        elif self.WITNESS.search(text):
            ct = "witness"; why.append("tells a story of faith or service")
        elif kind == "news":
            ct = "news"; why.append("a news post")
        elif kind == "episode":
            ct = "teaching"; why.append("a podcast episode")
        else:
            ct = "other"; why.append("could not tell what it is from the title")
        # breadth
        if ct in ("liturgy_routine", "school_internal"):
            br = "one_parish"
        elif ct == "teaching":
            br = "universal_church" if ot in self.UNIVERSAL_ORG or re.search(r"\b(catholic|explains|church)\b", t, re.I) else "diocese"
        elif ct == "liturgy_special":
            br = "diocese" if ot in ("diocese", "seminary") or re.search(r"\b(ordination|chrism|bishop)\b", t, re.I) else "deanery"
        elif ot in ("diocese", "seminary", "charity", "media"):
            br = "diocese"
        elif ct == "witness":
            br = "universal_church" if ot in self.UNIVERSAL_ORG else "diocese"
        elif ct in ("event_promo", "fundraising"):
            br = "deanery" if ot == "parish" else "diocese"
        else:
            br = "one_parish" if ot in ("parish", "school") else "diocese"
        base_sub = {"teaching": .8, "witness": .7, "liturgy_special": .65, "news": .5, "event_promo": .35,
                    "school_internal": .3, "fundraising": .35, "liturgy_routine": .25, "other": .4}[ct]
        sub = base_sub + (.08 if len(s) > 80 else 0) + (.05 if dur and 120 <= dur <= 1800 else 0)
        tim = {"teaching": .85, "witness": .7, "liturgy_special": .45, "news": .3, "event_promo": .1,
               "school_internal": .3, "fundraising": .25, "liturgy_routine": .1, "other": .35}[ct]
        if self.DATEISH.search(t):
            tim -= .2
        if kind in ("event", "bulletin_item"):
            comp = .5
        elif self.PROMO.search(t) or (dur is not None and dur < 60):
            comp = .35
        elif re.search(r"\b(pt\.?|part) ?\d", t, re.I):
            comp = .6
        else:
            comp = .8 if kind in ("video", "episode") else .7
        prod = .35 if it.get("livestream") else .6
        if it.get("thumbnail_url"):
            prod += .15
        if re.search(r"^\s*(live with restream|untitled|capture|img|stream)\b", t, re.I) or len(t) < 8:
            prod -= .25
        pillars = [p for p, rx in self.PILLAR_RX.items() if rx.search(text)]
        if ct in ("liturgy_special", "liturgy_routine") and "sunday_mass" not in pillars:
            pillars.insert(0, "sunday_mass")
        if ct == "teaching" and "missionary_disciples" not in pillars:
            pillars.append("missionary_disciples")
        if ot in ("charity",) and "works_of_mercy" not in pillars:
            pillars.append("works_of_mercy")
        mins = [m for m in MINISTRIES if m in (it.get("tags") or [])]
        sens = bool(self.SENSITIVE.search(text))
        reason = f"Heuristic: {why[0]}; {br.replace('_', ' ')} interest" + (" (flagged sensitive: a person should look)" if sens else "") + "."
        return {
            "content_type": ct, "breadth": br, "substance": _clip01(sub), "timelessness": _clip01(tim),
            "completeness": _clip01(comp), "production": _clip01(prod), "pillars": pillars, "appeal_ministries": mins,
            "language": it.get("language") or "und",
            "safety": {"names_private_person": False, "sensitive": sens, "note": None},
            "highlight": None, "reason": reason, "judged_by": "heuristic", "judge_version": HEURISTIC_VERSION,
        }


class LLMJudge:
    """Anthropic Messages API. Batches items; validates and clamps every field; falls back per item on failure."""
    name = "llm"

    def __init__(self, key: str, fallback: HeuristicJudge):
        self.key, self.fallback = key, fallback
        self.cfg = CFG["llm"]

    def _payload(self, it: dict, captions: dict[str, str]) -> dict:
        return {"id": it["id"], "title": it["title"], "description": (it.get("summary") or "")[:800],
                "organization": it.get("org_name"), "organization_type": it.get("org_type"), "kind": it["kind"],
                "published": (it.get("published_at") or "")[:10], "duration_s": it.get("duration_s"),
                "livestream": bool(it.get("livestream")), "language_guess": it.get("language"),
                "captions": (captions.get(it["id"]) or "")[: self.cfg["caption_chars"]] or None}

    def judge(self, items: list[dict], captions: dict[str, str] | None = None) -> list[dict]:
        captions = captions or {}
        out: list[dict] = []
        n = self.cfg["batch_size"]
        for i in range(0, len(items), n):
            batch = items[i:i + n]
            got = self._call(batch, captions)
            for it in batch:
                j = got.get(it["id"])
                out.append(self._normalize(j, it) if j else self.fallback._one(it))
        return out

    def _call(self, batch: list[dict], captions: dict[str, str]) -> dict[str, dict]:
        body = json.dumps({
            "model": self.cfg["model"], "max_tokens": self.cfg["max_tokens"], "system": SYSTEM,
            "messages": [{"role": "user", "content": JUDGMENT_SPEC + "\n\nItems:\n" +
                          json.dumps([self._payload(x, captions) for x in batch], ensure_ascii=False)}],
        }).encode()
        req = urllib.request.Request(self.cfg["endpoint"], data=body, method="POST", headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                m = re.search(r"\[.*\]", text, re.S)
                arr = json.loads(m.group(0)) if m else []
                return {x.get("id"): x for x in arr if isinstance(x, dict)}
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
                print(f"  llm batch failed ({e}); attempt {attempt + 1}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        return {}

    def _normalize(self, j: dict, it: dict) -> dict:
        ct = j.get("content_type") if j.get("content_type") in CONTENT_TYPES else "other"
        br = j.get("breadth") if j.get("breadth") in BREADTHS else "one_parish"
        hl = j.get("highlight")
        if isinstance(hl, dict) and isinstance(hl.get("start_s"), (int, float)) and isinstance(hl.get("end_s"), (int, float)) \
                and 0 <= hl["start_s"] < hl["end_s"]:
            hl = {"start_s": int(hl["start_s"]), "end_s": int(hl["end_s"]), "why": str(hl.get("why") or "")[:200]}
        else:
            hl = None
        sf = j.get("safety") if isinstance(j.get("safety"), dict) else {}
        return {
            "content_type": ct, "breadth": br,
            **{k: _clip01(j.get(k)) for k in ("substance", "timelessness", "completeness", "production")},
            "pillars": [p for p in (j.get("pillars") or []) if p in PILLARS],
            "appeal_ministries": [m for m in (j.get("appeal_ministries") or []) if m in MINISTRIES],
            "language": str(j.get("language") or it.get("language") or "und")[:3],
            "safety": {"names_private_person": bool(sf.get("names_private_person")), "sensitive": bool(sf.get("sensitive")),
                       "note": (str(sf.get("note"))[:200] if sf.get("note") else None)},
            "highlight": hl, "reason": str(j.get("reason") or "")[:300],
            "judged_by": f"llm:{self.cfg['model']}", "judge_version": CFG["version"],
        }


def _clip01(v) -> float:
    try:
        return round(min(max(float(v), 0.0), 1.0), 3)
    except (TypeError, ValueError):
        return 0.5


def fetch_captions(items: list[dict]) -> tuple[dict[str, str], str]:
    """YouTube auto-captions through the public timedtext endpoint. Returns ({id: "[mm:ss] text ..."}, status note).
    As of 2026-10-29 the endpoint answers 200 with an empty body unless the request carries a signed player token,
    so this usually returns nothing; it stops after 5 empty answers in a row."""
    out, empty = {}, 0
    for it in items:
        m = re.search(r"youtube\.com/watch\?v=([\w-]{11})", it.get("url") or "")
        if not m or it["kind"] != "video":
            continue
        url = f"https://www.youtube.com/api/timedtext?v={m.group(1)}&lang=en&kind=asr&fmt=json3"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                raw = r.read()
        except (urllib.error.URLError, TimeoutError):
            raw = b""
        if not raw.strip():
            empty += 1
            if empty >= 5 and not out:
                return out, "timedtext returned empty bodies (needs a signed player token); captions skipped"
            continue
        try:
            ev = json.loads(raw).get("events", [])
            parts = []
            for e in ev:
                txt = "".join(s.get("utf8", "") for s in e.get("segs", []) or []).strip()
                if txt:
                    ms = int(e.get("tStartMs", 0)) // 1000
                    parts.append(f"[{ms // 60}:{ms % 60:02d}] {txt}")
            out[it["id"]] = " ".join(parts)
        except (json.JSONDecodeError, AttributeError):
            continue
        time.sleep(0.5)
    return out, f"captions for {len(out)} videos"


# --------------------------------------------------------------------------------------------- signals and score
def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def signals(it: dict, j: dict, first_in_window: bool) -> dict:
    special = j["content_type"] == "liturgy_special"
    dur = it.get("duration_s")
    if it["kind"] == "video":
        if dur is None:
            ds, band = CFG["duration_unknown"], "unknown"
        else:
            b = next(b for b in CFG["duration_bands"] if dur <= b["max_s"])
            ds, band = (b.get("liturgy_special_score", b["score"]) if special else b["score"]), b["label"]
    else:
        ds, band = CFG["non_video_duration_score"].get(it["kind"], 0.6), it["kind"]
    when = _dt(it.get("published_at"))
    age = max((NOW - when).total_seconds() / 86400, 0.5) if when else None
    if it.get("views") is not None and age:
        vel = min(math.log1p(it["views"] / age) / math.log1p(CFG["view_velocity_full_at_per_day"]), 1.0)
    else:
        vel = CFG["view_velocity_unknown"]
    st = CFG["stream_score"]
    stream = (st["livestream_liturgy_special"] if special else st["livestream"]) if it.get("livestream") else st["upload"]
    w = CFG["signal_weights"]
    s = (w["duration"] * ds + w["thumbnail"] * (1.0 if it.get("thumbnail_url") else 0.0)
         + w["description"] * (1.0 if (it.get("summary") or "").strip() else 0.0)
         + w["view_velocity"] * vel + w["upload_not_stream"] * stream)
    # freshness: timeless items decay slowly (half-life up to a year); upcoming events by distance to the date
    fr = CFG["freshness"]
    if when and when > NOW:
        days = (when - NOW).total_seconds() / 86400
        fresh = fr["upcoming_factor"] * 0.5 ** (days / fr["upcoming_half_life_days"])
        hl = fr["upcoming_half_life_days"]
    else:
        hl = fr["half_life_min_days"] + (fr["half_life_max_days"] - fr["half_life_min_days"]) * j["timelessness"]
        fresh = 0.5 ** ((age or 365) / hl)
    fresh = max(fresh, fr["floor"])
    return {"duration_band": band, "duration_score": round(ds, 3), "view_velocity": round(vel, 3),
            "stream_score": stream, "has_thumbnail": bool(it.get("thumbnail_url")),
            "has_description": bool((it.get("summary") or "").strip()), "signals_score": round(s, 3),
            "freshness": round(fresh, 3), "half_life_days": round(hl), "rarity_boost": first_in_window}


def judgment_score(j: dict) -> float:
    w = CFG["judgment_weights"]
    return (w["substance"] * j["substance"] + w["timelessness"] * j["timelessness"] + w["completeness"] * j["completeness"]
            + w["production"] * j["production"] + w["breadth"] * CFG["breadth_value"][j["breadth"]])


def final_score(it: dict, j: dict, sg: dict) -> float:
    b = CFG["blend"]
    base = b["judgment"] * judgment_score(j) + b["signals"] * sg["signals_score"]
    mult = CFG["content_type_multiplier"][j["content_type"]] * CFG["kind_weight"].get(it["kind"], 0.8)
    mult *= CFG["org_scope_weight"].get(it.get("org_type") or "", CFG["org_scope_weight"]["default"])
    if sg["rarity_boost"]:
        mult *= CFG["rarity"]["boost"]
    return round(base * mult * sg["freshness"], 4)


# --------------------------------------------------------------------------------------------- main
def load_cache() -> dict:
    f = MEDIA / "judgments.json"
    return json.loads(f.read_text()).get("items", {}) if f.exists() else {}


def main() -> None:
    feed = json.loads((MEDIA / "feed.json").read_text())
    items = feed["items"]
    cache = load_cache()
    key = os.environ.get("ANTHROPIC_API_KEY")
    heur = HeuristicJudge()
    judge = LLMJudge(key, heur) if key else heur
    rejudge = "--rejudge" in sys.argv
    need = []
    for it in items:
        c = cache.get(it["id"])
        if rejudge or c is None:
            need.append(it)
        elif c["judged_by"] == "heuristic" and (key or c.get("judge_version") != HEURISTIC_VERSION):
            need.append(it)
    cap_note = "captions not attempted (heuristic judge)"
    caps: dict[str, str] = {}
    if key and need:
        caps, cap_note = fetch_captions(need)
    print(f"judge={'llm' if key else 'heuristic'}; judging {len(need)} of {len(items)} (cache {len(cache)}); {cap_note}")
    for it, j in zip(need, judge.judge(need, caps)):
        j["judged_at"] = NOW.replace(microsecond=0).isoformat()
        cache[it["id"]] = j

    # rarity: an organization's first item in the window (by date) gets a boost
    by_org = collections.defaultdict(list)
    for it in items:
        if it.get("published_at"):
            by_org[it["org_id"]].append(it)
    first_ids = set()
    win = CFG["rarity"]["window_days"]
    for xs in by_org.values():
        xs.sort(key=lambda x: x["published_at"])
        last = None
        for x in xs:
            d = _dt(x["published_at"])
            if last is None or (d - last).days > win:
                first_ids.add(x["id"])
            last = d

    off = set(CFG["off_diocesan_feed"])
    for it in items:
        j = cache[it["id"]]
        sg = signals(it, j, it["id"] in first_ids)
        it["judgment"] = {k: j[k] for k in ("content_type", "breadth", "substance", "timelessness", "completeness",
                                            "production", "pillars", "appeal_ministries", "language", "safety", "reason")}
        it["judged_by"] = j["judged_by"]
        it["content_type"] = j["content_type"]
        it["signals"] = sg
        it["score"] = final_score(it, j, sg)
        hl = j.get("highlight")
        it["clip_start_s"], it["clip_end_s"], it["clip_source"] = (hl["start_s"], hl["end_s"], "suggested") if hl else (None, None, None)
        it["feed_scope"] = "org_only" if j["content_type"] in off else "diocese"
        it["excluded_reason"] = None
        if j["content_type"] == "school_internal":
            it["excluded_reason"] = "school_internal: shown on the school's own page, not the diocesan feed"
        elif j["content_type"] == "liturgy_routine":
            it["excluded_reason"] = "routine_liturgy: a regular Mass stream; demoted and kept off the diocesan feed"
        elif j["safety"]["names_private_person"]:
            it["excluded_reason"] = "privacy: the judge thinks it names a private person; a person should look"
        elif j["safety"]["sensitive"]:
            it["excluded_reason"] = "sensitive: held for a person to look at"

    # diversity pass: cap per org per ISO week, then a greedy re-order that rotates org types and pillars
    dv = CFG["diversity"]
    elig = sorted((x for x in items if not x["excluded_reason"]), key=lambda x: (-x["score"], x["id"]))
    wk = collections.Counter()
    capped = []
    for x in elig:
        d = _dt(x["published_at"]) or NOW
        k = (x["org_id"], *d.isocalendar()[:2])
        if wk[k] >= dv["cap_per_org_per_week"] and not x.get("pinned"):
            x["excluded_reason"] = f"cap: already {dv['cap_per_org_per_week']} items from this organization that week"
            continue
        wk[k] += 1
        capped.append(x)
    order, pool = [], capped[:]
    while pool:
        recent_t = [o.get("org_type") for o in order[-dv["org_type_window"]:]]
        recent_o = [o["org_id"] for o in order[-dv["org_window"]:]]
        recent_p = [(o["judgment"]["pillars"] or ["none"])[0] for o in order[-dv["pillar_window"]:]]
        best, best_v = None, -1.0
        for x in pool[:60]:  # pool is score-sorted; looking 60 deep is plenty
            v = x["score"] * dv["same_org_type_penalty"] ** recent_t.count(x.get("org_type")) \
                * dv["same_org_penalty"] ** recent_o.count(x["org_id"]) \
                * dv["same_pillar_penalty"] ** recent_p.count((x["judgment"]["pillars"] or ["none"])[0])
            if v > best_v:
                best, best_v = x, v
        order.append(best)
        pool.remove(best)

    # auto status
    a = CFG["auto_status"]
    nowiso = NOW.isoformat()
    feat, feat_org = [], collections.Counter()
    for x in sorted(order, key=lambda x: -x["score"]):
        if len(feat) >= a["feature_count"]:
            break
        if (x["kind"] in a["feature_kinds"] and x.get("thumbnail_url") and x["score"] >= a["feature_threshold"]
                and (x["published_at"] or "") <= nowiso and feat_org[x["org_id"]] < a["feature_max_per_org"]):
            feat.append(x["id"])
            feat_org[x["org_id"]] += 1
    rank = {x["id"]: i + 1 for i, x in enumerate(order)}
    for it in items:
        it["rank"] = rank.get(it["id"])
        if it["id"] in feat:
            it["auto_status"] = "featured"
        elif it["rank"] and it["score"] >= a["approve_threshold"]:
            it["auto_status"] = "approved"
        else:
            it["auto_status"] = "candidate"
            if not it["excluded_reason"]:
                it["excluded_reason"] = f"below threshold: score {it['score']:.2f} < {a['approve_threshold']}"
        j = it["judgment"]
        bits = [f"{j['content_type'].replace('_', ' ')}", f"{j['breadth'].replace('_', ' ')} interest",
                f"substance {j['substance']:.2f}", f"timeless {j['timelessness']:.2f}", it["signals"]["duration_band"]]
        if it["signals"]["rarity_boost"]:
            bits.append("first from this organization in 60 days")
        it["rank_reason"] = f"{j['reason']} [{'; '.join(bits)}; score {it['score']:.2f}]"

    feed["ranked_by"] = {"script": "ingest/public/media/rank.py", "config_version": CFG["version"],
                         "judge": "llm:" + CFG["llm"]["model"] if key else "heuristic", "captions": cap_note,
                         "ranked_at": NOW.replace(microsecond=0).isoformat()}
    feed["rank_counts"] = {
        "auto_status": dict(collections.Counter(x["auto_status"] for x in items)),
        "content_type": dict(collections.Counter(x["content_type"] for x in items)),
        "judged_by": dict(collections.Counter(x["judged_by"] for x in items)),
        "excluded_reason": dict(collections.Counter((x["excluded_reason"] or "on feed").split(":")[0] for x in items)),
    }
    items.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0, -x["score"]))
    (MEDIA / "feed.json").write_text(json.dumps(feed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    keep = {x["id"] for x in items}
    (MEDIA / "judgments.json").write_text(json.dumps({
        "note": "Cache of content judgments by media item id (rank.py). judged_by says which judge made each one.",
        "updated_at": NOW.replace(microsecond=0).isoformat(),
        "items": {k: v for k, v in sorted(cache.items()) if k in keep},
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    export_overrides(items)
    print(json.dumps(feed["rank_counts"], indent=1))


def export_overrides(items: list[dict]) -> None:
    """Human overrides (from media_item_log, exported to a JSON file) -> training rows for a future learned model.
    Each row: the item's features and judgment, what the algorithm said, and what the person chose."""
    if "--overrides" not in sys.argv:
        return
    src = Path(sys.argv[sys.argv.index("--overrides") + 1])
    log = json.loads(src.read_text()) if src.exists() else []
    by = {x["id"]: x for x in items}
    rows = []
    for r in sorted(log, key=lambda r: r.get("changed_at") or ""):
        it = by.get(r["item_id"])
        if not it:
            continue
        rows.append({"item_id": r["item_id"], "changed_at": r.get("changed_at"), "human_status": r["new_status"],
                     "previous_status": r.get("old_status"), "note": r.get("note"),
                     "auto_status": it["auto_status"], "score": it["score"], "rank": it["rank"],
                     "features": {k: it.get(k) for k in ("kind", "org_type", "duration_s", "views", "livestream", "language",
                                                         "title", "published_at")},
                     "judgment": it["judgment"], "judged_by": it["judged_by"], "signals": it["signals"]})
    (MEDIA / "overrides.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"overrides.jsonl: {len(rows)} rows")


if __name__ == "__main__":
    main()
