"""Wayback-verified rule (Matthew, 2026-11-01 16:24: "once Claude has read the archived page and confirmed").

A parish site recovered from the Wayback Machine (crawl_status "wayback-<date>") counts as VERIFIED when
  (a) its schedule rows come from a trusted template read: the same bar as a live site (the 'site-' trusted-template
      list in fold-app data/real/california/merged/README.md: the template passed its grade and the site's parse is
      "high"; parse_site stores this as schedule_template.trusted_candidate), AND
  (b) at least one row is a Sunday or Saturday-vigil Mass (has_sunday), AND
  (c) at least one group was read.
Then its schedule rows get needs_review = false and review_reason
"wayback capture <date>, trusted template <name>; confirmed Sunday Mass line", and keep archive_url / capture_date so
a page can say "from an archived copy dated ...". Untrusted reads keep needs_review = true (times unverified).

The flip of needs_review is GATED: it happens only when FOLD_WAYBACK_VERIFIED=1 (default off) until an audit of the
newly verified records (fold-app docs/review/wayback-check-2026-11-01.md) shows an error rate of 15% or less. The grade
itself (record["wayback"]["grade"]) is always written; it changes nothing else, so older records stay valid.
Stdlib only; fold-app keeps a copy in scripts/us/wayback_regrade.py (keep the two in step).
"""
from __future__ import annotations

import os
import re

RULE = "wayback-verified-2026-11-01"


def enabled() -> bool:
    return os.environ.get("FOLD_WAYBACK_VERIFIED", "0") == "1"


def _has_sunday(rec: dict) -> bool:
    if "has_sunday" in rec:
        return bool(rec.get("has_sunday"))
    for r in rec.get("schedule_rows") or []:
        if not isinstance(r, dict) or (r.get("kind") or "mass") != "mass":
            continue
        if r.get("day") == "Sunday" or (r.get("day") == "Saturday" and ((r.get("time") or "") >= "14:00"
                                                                        or re.search(r"vigil|anticipat", r.get("raw") or "", re.I))):
            return True
    return False


def capture_date(rec: dict) -> str | None:
    w = rec.get("wayback") or {}
    if w.get("capture_date"):
        return w["capture_date"]
    m = re.match(r"wayback-(\d{4}-\d\d-\d\d)", str(rec.get("crawl_status") or ""))
    return m.group(1) if m else None


def grade(rec: dict, passed: set | None = None) -> dict:
    """The rule's three tests for one record. `passed` (optional) = templates that passed their bar, used only when the
    record predates schedule_template.trusted_candidate."""
    st = rec.get("schedule_template") or {}
    tpl = st.get("template")
    trusted = bool(st.get("trusted_candidate"))
    if not trusted and passed is not None and "trusted_candidate" not in st:
        pq = st.get("parse_quality") or {}
        trusted = tpl in passed and pq.get("score") == "high" and (pq.get("discovermass") or {}).get("sunday") is not False
    sunday = _has_sunday(rec)
    groups = len(rec.get("groups") or [])
    return {"rule": RULE, "template": tpl, "trusted_template": trusted, "has_sunday": sunday, "groups": groups,
            "verified": bool(trusted and sunday and groups >= 1), "capture_date": capture_date(rec)}


def apply(rec: dict, on: bool | None = None, passed: set | None = None) -> dict:
    """Write rec["wayback"]["grade"]; when the gate is on and the record passes, clear needs_review on its schedule rows.
    Only for wayback records; returns the grade (None for any other record)."""
    if not str(rec.get("crawl_status") or "").startswith("wayback-"):
        return None
    g = grade(rec, passed)
    on = enabled() if on is None else on
    g["applied"] = bool(on and g["verified"])
    rec.setdefault("wayback", {})["grade"] = g
    date = g["capture_date"]
    for r in rec.get("schedule_rows") or []:
        if g["applied"]:
            r["needs_review"] = False
            r["review_reason"] = f"wayback capture {date}, trusted template {g['template']}; confirmed Sunday Mass line"
        else:
            r["needs_review"] = True
            if not r.get("review_reason") or "trusted template" in str(r.get("review_reason")):
                r["review_reason"] = f"wayback capture {date}: read by a person before it is shown"
        r.setdefault("capture_date", date)
    return g
