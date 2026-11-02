#!/usr/bin/env python3
"""Build Wayback site records from the laptop pass's caches (data/raw/us/wayback/laptop/, unpacked from out/batch_NN.zip).

    python3 -I ingest/us/wayback/laptop_build.py [--zip batch_NN.zip ...] [--raw data/raw/us/wayback/laptop] [--rebuild]

--zip unpacks laptop batches first (their laptop_progress.json is kept as laptop_progress_NN.json).

The laptop (ingest/us/wayback/laptop_run.py) fetches only; this replays sites.one() — the runner's own parse-and-merge — on
those caches with NO network: the CDX listing is read from <diocese>/<key>__cdx.json and every capture the page plan asks for
from <diocese>/<key>/manifest.json. Output is therefore the runner's shape: crawl_status "wayback-<capture date>",
crawl_env "wayback", schedule rows needs_review, never overwriting an ok read. A page the replay would have to fetch (not in
the laptop cache) is answered "replay-miss" and counted. State: data/real/us/wayback/sites_state.laptop.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sites as S  # noqa: E402
import wblib as WB  # noqa: E402

ROOT = HERE.parents[2]
STATE = S.OUTW / "sites_state.laptop.json"


class NoNet(WB.Archive):
    """Replay: never touches the network."""

    def __init__(self):
        super().__init__()
        self.miss = 0

    def get(self, url, timeout=60, max_bytes=3_000_000):
        self.miss += 1
        return {"url": url, "status": None, "ctype": "", "final_url": url, "body": b"", "err": "replay-miss", "headers": {}}


def unpack(zp: Path, raw: Path) -> None:
    import re
    import zipfile
    num = (re.findall(r"(\d+)", zp.stem) or ["x"])[-1]
    with zipfile.ZipFile(zp) as z:
        for i in z.infolist():
            name = i.filename
            if name.startswith("/") or ".." in name.split("/") or i.is_dir():
                continue
            out = raw / (f"laptop_progress_{num}.json" if name == "laptop_progress.json" else name)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(i))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw" / "us" / "wayback" / "laptop"))
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--zip", nargs="*", default=[])
    a = ap.parse_args()
    raw = Path(a.raw)
    for zp in a.zip:
        unpack(Path(zp), raw)
    S.RAWW = raw
    sites: dict[str, dict] = {}
    for pf in sorted(raw.glob("laptop_progress*.json")):
        sites.update(json.loads(pf.read_text())["sites"])
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    today = datetime.now(timezone.utc).date().isoformat()
    tally: dict[str, int] = {}
    arch = NoNet()
    for sid, lp in sorted(sites.items()):
        if sid in state and not a.rebuild:
            continue
        f = S.SITES / (sid + ".json")
        if not f.exists():
            continue
        cur = json.loads(f.read_text()).get("crawl_status") or ""
        if cur.startswith("wayback-") and not a.rebuild:      # the runner's shards wrote it meanwhile: keep theirs
            r = {"result": "runner-had-it", "prior_status": lp.get("prior_status")}
        elif lp.get("result") == "fetched":
            m0 = arch.miss
            try:
                r = S.one(arch, f, today)
            except Exception as e:  # noqa: BLE001
                r = {"result": "error", "error": repr(e)[:300]}
            if arch.miss > m0:
                r["replay_miss"] = arch.miss - m0
        else:
            r = {"result": lp.get("result") or "error", "prior_status": lp.get("prior_status")}
        r.update(checked_at=lp.get("finished") or lp.get("updated"), env="laptop")
        state[sid] = r
        tally[r["result"]] = tally.get(r["result"], 0) + 1
    S.OUTW.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    w = [v for v in state.values() if v.get("result") == "written"]
    print(json.dumps({"this_run": tally, "replay_misses": arch.miss, "state_total": len(state), "written_total": len(w),
                      "groups": sum(v.get("groups") or 0 for v in w), "bulletin_sites": sum(1 for v in w if v.get("bulletin")),
                      "schedule_rows_needs_review": sum(v.get("schedule_rows") or 0 for v in w)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
