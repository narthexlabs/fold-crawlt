"""Offline re-parse of US site folders. Guard: a local raw cache older than the existing record (a newer GitHub crawl)
is not parsed over it."""
import sys, json, os, time
from multiprocessing import Pool
sys.path.insert(0, "/home/claude/wt/laneG4/ingest/us/sites")
import run_state as RS

def one(dio):
    t=time.time()
    try:
        rd = RS.run_diocese()
        inp_f = RS.INPUTS / f"{dio}.json"
        if not inp_f.exists():
            return dio, 0, 0, "no_input"
        inp = json.loads(inp_f.read_text())
        keys, guarded = set(), 0
        for s in inp["sites"]:
            if s["social_only"]:
                continue
            m = RS.manifest(s["key"])
            if not m or not m.get("complete") or m.get("status") in RS.EGRESS:
                continue
            fn = RS.OUT / dio / (s["key"].replace("/", "__") + ".json")
            if fn.exists():
                try:
                    rec = json.loads(fn.read_text())
                except ValueError:
                    rec = {}
                if (rec.get("fetched_at") or "") > (m.get("finished_at") or ""):
                    guarded += 1
                    continue
            keys.add(s["key"])
        if keys:
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                rd.cmd_parse(dio, keys, True)
        return dio, len(keys), guarded, f"{time.time()-t:.0f}s"
    except Exception as e:
        return dio, -1, 0, repr(e)[:200]

if __name__ == "__main__":
    dios = sorted((d for d in os.listdir(RS.OUT) if (RS.OUT / d).is_dir() and d not in sys.argv[2:]), key=lambda d: -len(os.listdir(RS.OUT / d)))
    with Pool(int(sys.argv[1]) if len(sys.argv) > 1 else 8) as p:
        for r in p.imap_unordered(one, dios):
            print(*r, flush=True)
