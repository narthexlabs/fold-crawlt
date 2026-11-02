#!/usr/bin/env python3
"""Runner health check for the "National crawl" workflow (narthexlabs/fold-crawlt). Stdlib + the `gh` CLI.

    python3 -I scripts/us/runner_health.py [--root <repo to write into>] [--repo <fold-crawlt clone>] [--rounds 3] [--no-fetch]

Reads the last N crawl ROUNDS (runs of crawl.yml that expanded the sites/wayback matrices; one-job dispatches such as
job=movements are not rounds), and for every job records its window and the work it committed, measured from the
committed data on origin/main of the public clone (never guessed):
  sites (<states>)  data/real/us/sites/<diocese>/*.json files changed by commits "US parish sites: <diocese>" and
                    "US parish sites: re-parse <diocese> (...)" authored inside the job's window, counted only for
                    dioceses whose see_state (data/real/us/dioceses.json) is in the job's state group. Progress/probe
                    commits carry no diocese and are not counted.
  wayback (i/n)     growth in entries of data/real/us/wayback/{sites,dioceses}_state.<i>of<n>.json (this shard's own files)
                    between the last commit before the job started and the last commit before it ended.
  discovermass      growth of data/real/us/discovermass/progress.json totals.records.
  gcatholic         growth of data/real/us/url_sources/gcatholic.json counts.churches.
  commoncrawl       growth of data/real/us/url_sources/commoncrawl.json counts.candidate_domains.
  bulletins         data/real/us/bulletins/** files changed by commits "US parish bulletins: groups for <diocese>".
  anything else     work_done = null (no attributable measure; stated in work_note).

Flags (see docs/RUNNERS.md for what each means): SHORT, IDLE, STUCK, CANCELLED_WAITING, FAILED.
Writes <root>/data/real/coverage/runner_health.json (latest), appends one line to runner_health.jsonl, renders
<root>/docs/RUNNERS.md. Read-only towards GitHub and the clone (only `git fetch` of origin main).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_SLUG = "narthexlabs/fold-crawlt"
WORKFLOW = "crawl.yml"
HERE = Path(__file__).resolve()
NULL_TS = "0001-01-01T00:00:00Z"


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(s: str | None) -> datetime | None:
    if not s or s.startswith("0001-"):
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def run(cmd: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}...: {r.stderr.strip()[:300]}")
    return r.stdout


def gh_json(*args: str):
    return json.loads(run(["gh", *args]))


# ------------------------------------------------------------------------------------------------ GitHub
def load_rounds(n: int) -> list[dict]:
    runs = gh_json("run", "list", "-R", REPO_SLUG, "--workflow", WORKFLOW, "-L", "25",
                   "--json", "databaseId,status,conclusion,createdAt,event,url")
    rounds = []
    for r in runs:
        jobs = []
        page = 1
        while True:
            d = gh_json("api", f"repos/{REPO_SLUG}/actions/runs/{r['databaseId']}/jobs?per_page=100&page={page}")
            jobs += d.get("jobs") or []
            if len(jobs) >= d.get("total_count", 0) or not d.get("jobs"):
                break
            page += 1
        # a round expands the matrices: job names like "sites (NY)" / "wayback (0/6)"
        if not any(j["name"].startswith(("sites (", "wayback (")) for j in jobs):
            continue
        rounds.append(dict(r, jobs=jobs))
        if len(rounds) >= n:
            break
    return rounds


# ------------------------------------------------------------------------------------------------ git
class Git:
    def __init__(self, repo: Path, fetch: bool):
        self.repo = repo
        if fetch:
            try:
                run(["git", "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main"], repo)
            except RuntimeError as e:
                print("git fetch failed (using the refs already here):", e, file=sys.stderr)
        self.ref = "origin/main"
        try:
            run(["git", "rev-parse", "--verify", "-q", self.ref], repo)
        except RuntimeError:
            self.ref = "HEAD"
        self._log = None
        self._show: dict = {}

    def log(self, since: datetime) -> list[dict]:
        """Commits on ref authored since `since`: [{at, subject, files}] (author date = when the job committed)."""
        if self._log is None:
            out = run(["git", "log", self.ref, f"--since={iso(since - timedelta(hours=6))}", "--name-only",
                       "--format=\x1e%aI\x1f%s"], self.repo)
            commits = []
            for block in out.split("\x1e")[1:]:
                head, _, rest = block.partition("\n")
                at, _, subj = head.partition("\x1f")
                commits.append({"at": ts(at), "subject": subj, "files": [x for x in rest.splitlines() if x.strip()]})
            self._log = commits
        return self._log

    def rev_before(self, when: datetime) -> str | None:
        out = run(["git", "rev-list", "-1", f"--before={iso(when)}", self.ref], self.repo).strip()
        return out or None

    def show_json(self, rev: str | None, path: str):
        if not rev:
            return None
        key = (rev, path)
        if key not in self._show:
            try:
                self._show[key] = json.loads(run(["git", "show", f"{rev}:{path}"], self.repo))
            except (RuntimeError, ValueError):
                self._show[key] = None
        return self._show[key]


# ------------------------------------------------------------------------------------------------ work done
def see_states(g: Git) -> dict:
    d = g.show_json(g.ref, "data/real/us/dioceses.json") or {}
    return {s["slug"]: s.get("see_state") for s in d.get("dioceses", [])}


def diocese_state(slug: str, states: dict) -> str | None:
    if slug in states:
        return states[slug]
    if slug.startswith("unassigned-"):
        return slug[-2:].upper()
    return None


def window_commits(g: Git, start: datetime, end: datetime) -> list[dict]:
    lo, hi = start - timedelta(seconds=30), end + timedelta(seconds=60)
    return [c for c in g.log(start) if c["at"] and lo <= c["at"] <= hi]


def work_sites(g: Git, job: dict, start: datetime, end: datetime, states: dict) -> tuple:
    grp = job["name"][len("sites ("):-1]
    want = {x.strip().upper() for x in grp.split(",") if x.strip()}
    if not want:
        return None, "state group not readable from the job name"
    files, dios, unknown = set(), set(), set()
    for c in window_commits(g, start, end):
        s = c["subject"]
        if not s.startswith("US parish sites: ") or s.startswith(("US parish sites: progress", "US parish sites: re-parse progress")):
            continue
        rest = s[len("US parish sites: "):]
        if rest.startswith("re-parse "):
            rest = rest[len("re-parse "):].split(" (")[0]
        if rest.startswith("rendered re-read"):
            continue                                     # the render job's commits, not this one
        dio = rest.strip()
        st = diocese_state(dio, states)
        if st is None:
            unknown.add(dio)
            continue
        if st not in want:
            continue
        dios.add(dio)
        files |= {f for f in c["files"] if f.startswith(f"data/real/us/sites/{dio}/") and f.endswith(".json")}
    note = f"{len(files)} site files, {len(dios)} dioceses"
    if unknown:
        note += f"; {len(unknown)} diocese commits with no known state not counted"
    return len(files), note


def entries(obj) -> int | None:
    return len(obj) if isinstance(obj, dict) else None


def growth(g: Git, start: datetime, end: datetime, path: str, pick) -> tuple:
    a, b = g.rev_before(start), g.rev_before(end + timedelta(seconds=60))
    va, vb = pick(g.show_json(a, path)), pick(g.show_json(b, path))
    if vb is None:
        return None, f"{path} not readable at the job's end"
    return vb - (va or 0), f"{path}: {va if va is not None else 'absent'} -> {vb}"


def work_wayback(g: Git, job: dict, start: datetime, end: datetime) -> tuple:
    shard = job["name"][len("wayback ("):-1].replace("/", "of")
    total, notes = 0, []
    for kind in ("dioceses", "sites"):
        n, note = growth(g, start, end, f"data/real/us/wayback/{kind}_state.{shard}.json", entries)
        if n is None:
            n = 0
            note = f"{kind}_state.{shard}.json not committed yet (0 entries)"
        total += n
        notes.append(f"{kind} +{n}")
    return total, f"state entries added, shard {shard}: " + ", ".join(notes)


def work_bulletins(g: Git, start: datetime, end: datetime) -> tuple:
    files = set()
    for c in window_commits(g, start, end):
        if c["subject"].startswith("US parish bulletins: groups for "):
            files |= {f for f in c["files"] if f.startswith("data/real/us/bulletins/")}
    return len(files), f"{len(files)} bulletin files"


def work_done(g: Git, job: dict, start: datetime | None, end: datetime | None, states: dict) -> tuple:
    if not start:
        return None, "never got a runner"
    end = end or now()
    name = job["name"]
    if name.startswith("sites ("):
        return work_sites(g, job, start, end, states)
    if name.startswith("wayback ("):
        return work_wayback(g, job, start, end)
    if name == "discovermass":
        return growth(g, start, end, "data/real/us/discovermass/progress.json",
                      lambda d: ((d or {}).get("totals") or {}).get("records") if d else None)
    if name == "gcatholic":
        return growth(g, start, end, "data/real/us/url_sources/gcatholic.json",
                      lambda d: ((d or {}).get("counts") or {}).get("churches") if d else None)
    if name == "commoncrawl":
        return growth(g, start, end, "data/real/us/url_sources/commoncrawl.json",
                      lambda d: ((d or {}).get("counts") or {}).get("candidate_domains") if d else None)
    if name == "bulletins":
        return work_bulletins(g, start, end)
    if name == "chain":
        return None, "dispatcher only (starts the next round); no data"
    return None, "no attributable measure for this job"


# ------------------------------------------------------------------------------------------------ assemble
def job_record(g: Git, job: dict, states: dict) -> dict:
    steps = job.get("steps") or []
    got_runner = bool(job.get("runner_name")) or bool(steps)
    started = ts(steps[0].get("started_at")) if steps and steps[0].get("started_at") else (ts(job.get("started_at")) if got_runner else None)
    completed = ts(job.get("completed_at")) if job.get("status") == "completed" else None
    created = ts(job.get("created_at"))
    dur = round(((completed or now()) - started).total_seconds() / 60, 1) if started else None
    wait = round(((started or completed or now()) - created).total_seconds() / 60, 1) if created else None
    rec = {"name": job["name"], "status": job.get("status"), "conclusion": job.get("conclusion") or None,
           "createdAt": iso(created), "startedAt": iso(started), "completedAt": iso(completed),
           "duration_min": dur, "queued_min": wait, "got_runner": got_runner}
    if job.get("conclusion") == "skipped":
        rec["work_done"], rec["work_note"] = None, "skipped (not part of this kind of run)"
        return rec
    try:
        rec["work_done"], rec["work_note"] = work_done(g, job, started, completed, states)
    except RuntimeError as e:
        rec["work_done"], rec["work_note"] = None, f"could not measure: {e}"
    return rec


def flag(rec: dict) -> list[str]:
    f = []
    c, d = rec["conclusion"], rec["duration_min"]
    if c == "skipped":
        return f
    if c == "cancelled" and not rec["got_runner"]:
        f.append("CANCELLED_WAITING")
    elif c in ("failure", "timed_out", "startup_failure") or (c == "cancelled" and rec["got_runner"]):
        f.append("FAILED")
    if rec["status"] == "completed" and c == "success" and d is not None and d < 5 and rec["name"] != "chain":
        f.append("SHORT")
    if rec["status"] == "completed" and d is not None and d >= 20 and rec["work_done"] == 0:   # long jobs commit at the end
        f.append("IDLE")
    return f


def build(rounds: list[dict], g: Git) -> dict:
    states = see_states(g)
    out = []
    for r in rounds:
        jobs = [job_record(g, j, states) for j in r["jobs"]]
        for j in jobs:
            j["flags"] = flag(j)
        out.append({"run_id": r["databaseId"], "url": r["url"], "event": r["event"], "status": r["status"],
                    "conclusion": r["conclusion"] or None, "createdAt": r["createdAt"],
                    "jobs": sorted(jobs, key=lambda j: j["name"])})
    # STUCK: a job (by name) of the newest round that got a runner in neither of the 2 newest rounds, unless a job of that
    # name is running right now in an older round read here (then it is just waiting its turn in its concurrency group)
    if len(out) >= 2:
        busy = {j["name"] for rr in out for j in rr["jobs"] if j["status"] == "in_progress" and j["got_runner"]}
        for j in out[0]["jobs"]:
            nm = j["name"]
            if j["conclusion"] == "skipped" or nm in busy:
                continue
            if not any(x["name"] == nm and x["got_runner"] for rr in out[:2] for x in rr["jobs"]):
                j["flags"].append("STUCK")
    return {"checked_at": iso(now()), "repo": REPO_SLUG, "workflow": WORKFLOW, "git_ref": g.ref,
            "rounds": out, "verdict": verdict(out)}


def table_round(rounds: list[dict]) -> dict | None:
    """The newest round in which at least one crawl job got a runner (the one being worked on)."""
    for r in rounds:
        if any(j["got_runner"] and j["name"] != "chain" for j in r["jobs"]):
            return r
    return rounds[0] if rounds else None


def verdict(rounds: list[dict]) -> str:
    r = table_round(rounds)
    if not r:
        return "no crawl rounds found"
    sites = [j for j in r["jobs"] if j["name"].startswith("sites (")]
    done = [j for j in sites if j["status"] == "completed" and j["conclusion"] == "success"]
    running = [j for j in sites if j["status"] != "completed"]
    parts = []
    if sites and len(done) == len(sites):
        mx = max(j["duration_min"] or 0 for j in done)
        bound = "supply-bound" if mx < 15 else ("time-bound" if mx >= 100 else "mixed")
        parts.append(f"{bound}: {len(done)} crawl jobs finished in <{int(mx) + 1} min")
    elif sites:
        parts.append(f"sites: {len(done)}/{len(sites)} crawl jobs finished, {len(running)} running")
    # wayback shards busy right now, across the rounds read (a shard of an older round can still be running)
    wb = sorted({j["name"] for rr in rounds for j in rr["jobs"] if j["name"].startswith("wayback (")
                 and j["status"] == "in_progress" and j["got_runner"]})
    n = None
    for j in r["jobs"]:
        if j["name"].startswith("wayback (") and "/" in j["name"]:
            n = int(j["name"].rstrip(")").split("/")[1])
    parts.append(f"wayback {len(wb)}{'/' + str(n) if n else ''} shards busy")
    fl = Counter(f for j in r["jobs"] for f in j["flags"])
    bad = {k: v for k, v in fl.items() if k in ("FAILED", "IDLE", "STUCK")}
    if bad:
        parts.append("attention: " + ", ".join(f"{v} {k}" for k, v in sorted(bad.items())))
    return "; ".join(parts)


# ------------------------------------------------------------------------------------------------ render
EXPLAIN = """## What the flags mean

| Flag | Meaning | Action |
|---|---|---|
| SHORT | Finished in under 5 minutes: the job ran out of supply (nothing left to fetch in its group). | None; normal once a group is crawled. |
| IDLE | Finished after 20 minutes or more but committed nothing measurable. | Look at the job log. |
| STUCK | This job name got no runner in either of the last 2 rounds (and is not running from an older round). | Look at the concurrency group / queue. |
| CANCELLED_WAITING | Cancelled before it ever got a runner (no steps, no runner name). | None: informational, see below. |
| FAILED | Failed, timed out, or was cancelled after it had started running. | Look at the job log. |

**CANCELLED_WAITING is not an error.** Each crawl job sits in a concurrency group (`crawl-wayback-<shard>`,
`crawl-discovermass`, ...) with `cancel-in-progress: false`, so a job never interrupts one that is running. GitHub
keeps at most ONE job waiting per group: when a newer round queues the same job, the older waiting one is cancelled.
Because the chain (and the cron) start a new round while the previous round's long jobs (wayback, discovermass,
gcatholic) are still running, the waiting copy from the round in between is always dropped. GitHub reports it as
"cancelled" with a start time equal to its queue time, which looked like a crash on 2026-11-01 (false alarm). The
running job is unaffected and the newest waiting copy starts as soon as it ends.

Work done is measured from committed data on `origin/main` of narthexlabs/fold-crawlt inside each job's window (never
guessed; `null` = no attributable measure). Code: `scripts/us/runner_health.py` (public repo).
"""


def fmt(v, suf=""):
    return "–" if v is None else f"{v}{suf}"


def render(rep: dict, history: list[dict]) -> str:
    r = table_round(rep["rounds"])
    L = ["# Runners: National crawl health", "",
         f"Checked {rep['checked_at']} (UTC) from GitHub Actions runs of `{WORKFLOW}` in {REPO_SLUG}. "
         "Refreshed every 10 minutes by the floor loop.", "",
         f"**Verdict:** {rep['verdict']}", ""]
    if r:
        L += [f"## Round {r['run_id']} ({r['event']}, created {r['createdAt']}, {r['status']})", "",
              f"[run on GitHub]({r['url']})", "",
              "| Job | Ran (min) | Waited (min) | Outcome | Work done | Flags |", "|---|---:|---:|---|---|---|"]
        for j in r["jobs"]:
            if j["conclusion"] == "skipped":
                continue
            out = j["conclusion"] or j["status"]
            wd = fmt(j["work_done"])
            note = (j.get("work_note") or "").replace("|", "/")
            L.append(f"| {j['name']} | {fmt(j['duration_min'])} | {fmt(j['queued_min'])} | {out} | {wd}"
                     f"{' (' + note + ')' if note else ''} | {' '.join(j['flags']) or '–'} |")
        L.append("")
    others = [x for x in rep["rounds"] if x is not r]
    if others:
        L += ["## Earlier rounds read in this check", "", "| Round | Created | Status | Jobs ran | Flags |", "|---|---|---|---:|---|"]
        for x in others:
            ran = sum(1 for j in x["jobs"] if j["got_runner"])
            fl = Counter(f for j in x["jobs"] for f in j["flags"])
            L.append(f"| [{x['run_id']}]({x['url']}) | {x['createdAt']} | {x['conclusion'] or x['status']} | {ran} | "
                     f"{', '.join(f'{k} x{v}' for k, v in sorted(fl.items())) or '–'} |")
        L.append("")
    L += ["## History of flags (newest first, last 30 checks)", "", "| Checked | Round | Verdict | Flags |", "|---|---|---|---|"]
    for h in list(reversed(history))[:30]:
        fl = ", ".join(f"{k} x{v}" for k, v in sorted((h.get("flags") or {}).items())) or "–"
        L.append(f"| {h['checked_at']} | {h.get('round') or '–'} | {h['verdict']} | {fl} |")
    L += ["", EXPLAIN]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE.parents[2]), help="repo root to write outputs into")
    ap.add_argument("--repo", default=str(HERE.parents[2]), help="local clone of narthexlabs/fold-crawlt (for git log)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)
    g = Git(Path(a.repo), not a.no_fetch)
    rep = build(load_rounds(a.rounds), g)
    cov = root / "data" / "real" / "coverage"
    cov.mkdir(parents=True, exist_ok=True)
    (cov / "runner_health.json").write_text(json.dumps(rep, indent=1) + "\n")
    tr = table_round(rep["rounds"])
    line = {"checked_at": rep["checked_at"], "round": tr["run_id"] if tr else None, "verdict": rep["verdict"],
            "flags": dict(Counter(f for j in (tr["jobs"] if tr else []) for f in j["flags"])),
            "flagged": sorted(f"{j['name']}:{'+'.join(j['flags'])}" for j in (tr["jobs"] if tr else []) if j["flags"])}
    hist_f = cov / "runner_health.jsonl"
    with hist_f.open("a") as fh:
        fh.write(json.dumps(line) + "\n")
    history = [json.loads(x) for x in hist_f.read_text().splitlines() if x.strip()]
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "RUNNERS.md").write_text(render(rep, history))
    print(rep["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
