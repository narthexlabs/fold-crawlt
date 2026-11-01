#!/usr/bin/env python3
"""Polite plain fetch for a machine other than the sandbox (Matthew's laptop VM; stdlib only, python3 >= 3.8).

Used when a diocese's site refuses the sandbox: the capture is taken from the Internet Archive (Wayback Machine),
which is the project's documented fallback for blocked sites (docs/design/scaling-california.md step 4b).

  python3 laptop_fetch.py urls.tsv [budget_seconds]      # lines: name<TAB>url

For each url it asks web.archive.org for the capture nearest to today (`/web/<today>id_/<url>`, original bytes), at
least 1.2 s apart, backs off 20/40/60 s on connection errors, never retries a block, skips names already saved,
and writes raw/<name>.json = {url, final_url (carries the capture timestamp), status, blocked, body, fetched_at}.
The default urllib User-Agent is used; nothing is spoofed. A site that answers 403 / a challenge page is recorded and left.
Each call must stay under the host tool's time limit, so run with a budget and repeat until it prints "left 0".
"""
import datetime
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

RAW = pathlib.Path(__file__).parent / "raw"
RAW.mkdir(exist_ok=True)
_last: dict[str, float] = {}


def get(url: str, delay: float = 1.2) -> dict:
    host = url.split("/")[2]
    w = delay - (time.time() - _last.get(host, 0))
    if w > 0:
        time.sleep(w)
    _last[host] = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=45) as r:
            body, st, final = r.read().decode("utf-8", "replace"), r.status, r.geturl()
    except urllib.error.HTTPError as e:
        body, st, final = e.read().decode("utf-8", "replace"), e.code, url
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": None, "blocked": f"error {type(e).__name__}: {e}", "body": "", "final_url": None}
    low = body[:3000].lower()
    blocked = None
    if st in (401, 403, 429) or "just a moment" in low or "incapsula" in low or "attention required" in low:
        blocked = f"HTTP {st}" + (" challenge" if "just a moment" in low or "incapsula" in low else "")
    return {"url": url, "status": st, "blocked": blocked, "final_url": final, "body": body,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def wayback(url: str, stamp: str | None = None) -> dict:
    stamp = stamp or datetime.date.today().strftime("%Y%m%d")
    r = get(f"https://web.archive.org/web/{stamp}id_/{url}")
    r["requested"] = url
    return r


def save(rec: dict, name: str | None = None) -> str:
    name = name or hashlib.sha1(rec["url"].encode()).hexdigest()[:16]
    (RAW / f"{name}.json").write_text(json.dumps(rec))
    return name


def done(name: str) -> bool:
    p = RAW / f"{name}.json"
    return p.exists() and json.loads(p.read_text()).get("status") == 200


def main() -> None:
    lst = sys.argv[1]
    budget = float(sys.argv[2]) if len(sys.argv) > 2 else 100
    t0, saved = time.time(), 0
    rows = [ln.rstrip("\n").split("\t") for ln in open(lst) if ln.strip()]
    for name, url in rows:
        if done(name):
            continue
        if time.time() - t0 > budget:
            print("budget reached")
            break
        for attempt in range(3):
            r = wayback(url)
            if r["status"] == 200 or (r["status"] and r["status"] != 429):
                break
            time.sleep(20 * (attempt + 1))
        if r["status"] == 200:
            save(r, name)
            saved += 1
        print(r["status"], r["blocked"], len(r["body"]), (r.get("final_url") or "")[28:42], name, flush=True)
    print("saved", saved, "left", sum(1 for n, _ in rows if not done(n)))


if __name__ == "__main__":
    main()
