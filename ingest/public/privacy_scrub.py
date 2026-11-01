"""Apply the privacy screen (privacy.py) to data files already committed, in place, and report counts per file.

    python3 -I ingest/public/privacy_scrub.py            # screen data/real/** in place; print counts per file
    python3 -I ingest/public/privacy_scrub.py --check    # change nothing; exit 1 if any file would change
    python3 -I ingest/public/privacy_scrub.py --diff     # also print each changed field, before -> after
                                                         # (local review only: it prints the original text)

The files and fields screened are privacy.FILE_RULES and privacy.RULES. The ingest scripts apply the same rules when
they write these files (common.save_json calls privacy.screen_file), so a rerun does not bring names back.
data/seed/ and data/briefings/ are rebuilt from the screened files by scripts/build_real_seed.py and
scripts/build_briefings.py. Counts never include the names themselves.
"""
from __future__ import annotations

import argparse
import collections
import copy
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy  # noqa: E402

ROOT = privacy.ROOT


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if a.get(k) != b.get(k):
                yield from _diff(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                yield from _diff(x, y, f"{path}[{i}]")
    elif isinstance(a, list) and isinstance(b, list):
        kept = [json.dumps(y, sort_keys=True) for y in b]
        for x in a:
            if json.dumps(x, sort_keys=True) not in kept:
                yield path + "[-]", x, None
    else:
        yield path, a, b


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 if any file would change")
    ap.add_argument("--diff", action="store_true", help="print each changed field (local review only)")
    a = ap.parse_args()
    total = collections.Counter()
    changed = 0
    print(f"{'file':58s} names  intentions  people  junk  contacts")
    for pat, _ in privacy.FILE_RULES:
        for f in sorted(glob.glob(str(ROOT / pat))):
            p = Path(f)
            obj = json.loads(p.read_text(encoding="utf-8"))
            before = copy.deepcopy(obj)
            cnt = privacy.screen_file(p, obj)
            if obj == before:
                continue
            changed += 1
            total.update(cnt)
            rel = p.relative_to(ROOT).as_posix()
            print(f"{rel:58s} {cnt['names_withheld']:5d}  {cnt['intentions_withheld']:10d}  {cnt['people_withheld']:6d}  "
                  f"{cnt['junk_rows_dropped']:4d}  {cnt['contacts_removed']:8d}")
            if a.diff:
                for path, x, y in _diff(before, obj):
                    print(f"    {path}\n      - {json.dumps(x, ensure_ascii=False)[:300]}\n      + {json.dumps(y, ensure_ascii=False)[:300]}")
            if not a.check:
                p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{'total (' + str(changed) + ' files)':58s} {total['names_withheld']:5d}  {total['intentions_withheld']:10d}  "
          f"{total['people_withheld']:6d}  {total['junk_rows_dropped']:4d}  {total['contacts_removed']:8d}")
    return 1 if (a.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
