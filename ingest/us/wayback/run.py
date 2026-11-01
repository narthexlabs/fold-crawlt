#!/usr/bin/env python3
"""The `wayback` job: A (diocese directories) then B (parish sites), one archive client (<= 1 request/s), commit per batch.

    python3 -u ingest/us/wayback/run.py --minutes 110 [--git] [--a-share 0.35] [--only-a] [--only-b]

Resume = run it again (state: data/real/us/wayback/{dioceses,sites}_state.json, committed; caches data/raw/us/wayback/).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import wblib as WB  # noqa: E402

TRAILER = ("\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_01SXKUYETifPNNjUVPkZ7qG5")


def sh(*cmd) -> int:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).returncode


def out(*cmd) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout


def commit_push(what: str) -> None:
    paths = ["data/real/us/wayback", "data/real/us/sites", "data/real/us/dioceses"]
    sh("git", "add", "--", *paths)
    if not out("git", "diff", "--cached", "--name-only").strip():
        return
    sh("git", "commit", "-q", "-m", f"Wayback recovery ({what}): {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}" + TRAILER)
    for _ in range(2):
        if sh("git", "pull", "--rebase", "--autostash", "-q", "origin", "main") != 0:
            sh("git", "rebase", "--abort")
            sh("git", "merge", "-q", "-X", "theirs", "--no-edit", "origin/main")   # a file on main wins (live ok read)
        if sh("git", "push", "-q", "origin", "HEAD:main") == 0:
            print(f"  pushed ({what})", flush=True)
            return
    print("  push failed twice; will retry at the next batch", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=110)
    ap.add_argument("--git", action="store_true")
    ap.add_argument("--a-share", type=float, default=0.35)
    ap.add_argument("--only-a", action="store_true")
    ap.add_argument("--only-b", action="store_true")
    a = ap.parse_args()
    import dioceses as A
    import sites as B
    t0 = time.time()
    arch = WB.Archive()
    cb = commit_push if a.git else None
    rc = 0
    try:
        if not a.only_b:
            A.run(arch, a.minutes * (1.0 if a.only_a else a.a_share), on_batch=cb)
            if cb:
                cb("dioceses")
        if not a.only_a:
            left = a.minutes - (time.time() - t0) / 60
            B.run(arch, left, on_batch=cb)
    except WB.WbStop as e:
        print("STOP:", e, flush=True)
        rc = 0
    finally:
        if cb:
            cb("final")
    print(f"archive requests: {arch.n}; log: {arch.log[-5:]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
