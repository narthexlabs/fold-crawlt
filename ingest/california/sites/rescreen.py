"""Re-apply the privacy screens to parish-site records already written (data/real/california/sites/<slug>/<domain>.json),
with no network. Used after a screen is tightened, and by `run_diocese.py parse --offline` for the events it carries
over from the previous parse (third-party calendar feeds are not re-read offline).

    python3 -I ingest/california/sites/rescreen.py            # every diocese: drop events the screens now refuse,
                                                               # re-screen group text, report any "Mass for <Name>" left

Event rule: the calendar connector's screen_event (ingest/connectors/calendars/privacy.py, now with the English
"Mass for <Name>" intention form) and then the shared detector (ingest/public/privacy.redact): an event either refuses
is dropped, never stored with a gap. Group and schedule text: parse_site.screen_record (ingest/public/privacy.screen).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ingest" / "public"))
from privacy import redact  # noqa: E402

_spec = importlib.util.spec_from_file_location("cal_privacy", ROOT / "ingest" / "connectors" / "calendars" / "privacy.py")
cal_privacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal_privacy)

OUT = ROOT / "data" / "real" / "california" / "sites"
MEMORIAL = re.compile(r"loving memory|in memoriam|memorial|\brip\b|obituar|funeral", re.I)   # a memorial card names a person


def event_ok(e: dict) -> tuple[bool, str | None, str | None]:
    """(keep, reason, cleaned title). The cleaned title has lay names riding on a public event cut ("<topic>: <name>
    (<place>)" -> "<topic> (<place>)")."""
    keep, why, title, loc = cal_privacy.screen_event(e.get("title"), e.get("location"))
    if not keep:
        return False, why, None
    if redact(title or "")[1] or (e.get("location") and redact(e["location"])[1]):
        return False, "names_person_shared_screen", None
    return True, None, title


def rescreen_events(rec: dict) -> Counter:
    """Drops events the screens now refuse and cuts names the screens now cut; returns the reasons (counts only, titles
    are never kept)."""
    out, why = [], Counter()
    for e in rec.get("events") or []:
        ok, r, title = event_ok(e)
        if ok:
            if title and title != e.get("title"):
                e["title"] = title
                why["name_cut_from_title"] += 1
            out.append(e)
        else:
            why[r] += 1
    if why:
        rec["events"] = out
        ec = dict(rec.get("event_counts") or {})
        ec["stored"] = len(out)
        ex = dict(ec.get("excluded_by_reason") or {})
        for k, v in why.items():
            ex[k] = ex.get(k, 0) + v
        ec["excluded_by_reason"] = ex
        ec["rescreened"] = dict(why)
        rec["event_counts"] = ec
    return why


def strings(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from strings(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


def main() -> int:
    import parse_site as P
    tot, left, files = Counter(), [], 0
    for f in sorted(OUT.glob("*/*.json")):
        if f.name == "summary.json":
            continue
        rec = json.loads(f.read_text("utf-8"))
        before = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        why = rescreen_events(rec)
        tot.update(why)
        imgs = [x for x in rec.get("schedule_images") or [] if not MEMORIAL.search(f"{x.get('alt', '')} {x.get('url', '')}")]
        if "schedule_images" in rec and len(imgs) != len(rec["schedule_images"]):
            tot["memorial_image_dropped"] += len(rec["schedule_images"]) - len(imgs)
            rec["schedule_images"] = imgs
        prev = rec.get("privacy") or {}
        P.screen_record(rec)
        new = {k: v for k, v in rec["privacy"].items() if k != "screened_by" and v}
        rec["privacy"] = dict(prev, **{k: prev.get(k, 0) + v for k, v in new.items()}, **({"rescreened_2026_10_31": new} if new else {}))
        tot.update({f"text_{k}": v for k, v in new.items()})
        for path, s in strings(rec):
            if cal_privacy.mass_for_person(s) and not path.endswith("url"):
                left.append(f"{f.relative_to(ROOT)} {path}")
        if json.dumps(rec, ensure_ascii=False, sort_keys=True) != before:
            f.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", "utf-8")
            files += 1
    print(f"events dropped: {dict(tot)}; files rewritten: {files}")
    print(f'"Mass for <Name>" still present in {len(left)} fields' + ("".join(f"\n  {x}" for x in left[:40])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
