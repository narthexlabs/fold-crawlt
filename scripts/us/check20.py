"""Independent accuracy check of 20 US parish-site records (docs/review/us-check-2026-11-01.md).

  python3 -I scripts/us/check20.py sample          # print the stratified sample (seed 20261101)
  python3 -I scripts/us/check20.py ours [N..]      # compact view of our rows/groups for chosen sample numbers
  python3 -I scripts/us/check20.py fetch OUTDIR    # fetch each sampled site's schedule page(s) live, save text
  python3 -I scripts/us/check20.py live OUTDIR     # re-fetch every cached page of each site live (raw HTML + manifest)
  python3 -I scripts/us/check20.py liveparse OUTDIR TAG   # run the CURRENT parse_site on those pages -> OUTDIR/parsed_TAG.json

Sample frame: data/real/us/sites/<diocese>/<domain>.json with crawl_status 'ok', schedule_rows and groups non-empty.
Re-check (2026-11-01 evening): sample() returns the FROZEN 20 domains of the first check, looked up by domain in the
current records (the frame itself changed after the re-parse, so seed 20261101 alone no longer picks them);
CHECK20_FROZEN=0 gives the seed-drawn sample again.
Strata: CMS family (cms_guess.cms) x schedule template; round-robin across strata (largest first), at most one site per
diocese. Fetch: honest UA, robots.txt honoured, >=1.1 s between requests to the same host, no JS, no cookies. Text is
written to OUTDIR (scratch, not committed) for a human reading; the comparison itself is done by hand in the review doc.
"""
import collections, glob, html, json, os, random, re, sys, time, urllib.request, urllib.robotparser, urllib.parse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
UA = 'FindTheChurch/0.1 (+https://oakland-diocese-dashboard.vercel.app/about)'
SEED = 20261101
N = 20


def frame():
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, 'data/real/us/sites/*/*.json'))):
        if os.path.basename(f) in ('summary.json', 'progress.json'):
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if isinstance(d, dict) and d.get('crawl_status') == 'ok' and d.get('schedule_rows') and d.get('groups'):
            d['_file'] = os.path.relpath(f, ROOT)
            out.append(d)
    return out


def stratum(d):
    cms = (d.get('cms_guess') or {}).get('cms') or 'unknown'
    st = (d.get('schedule_template') or {}).get('template') or '?'
    return f'{cms}/{st}'


FROZEN = ['ststep.org', 'stjosephmiesville.com', 'strosedillonmt.org', 'hsccatl.com', 'namct.org', 'stjoefullerton.org', 'stvalentinechurch.com', 'olih.org', 'holyspiritlantana.com', 'stxchurch.org', 'stjohnsmilford.com', 'stagnesfreeland.org', 'attleborocatholics.org', 'olphglenview.org', 'st-monica.org', 'holycrosslascruces.org', 'cbsfa.org', 'holynameofjesusparish.com', 'stpiusjax.org', 'stpaultheapostlespartanburg.org']  # the 20 sites of the 2026-11-01 first check, in order


def sample():
    # re-check mode: same parishes as the first check, looked up by domain in the CURRENT records
    cur = {d['domain']: d for d in frame()}
    allrec = {}
    for f in glob.glob(os.path.join(ROOT, 'data/real/us/sites/*/*.json')):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if isinstance(d, dict) and d.get('domain') in FROZEN:
            d['_file'] = os.path.relpath(f, ROOT); allrec[d['domain']] = d
    if os.environ.get('CHECK20_FROZEN', '1') == '1' and all(x in allrec for x in FROZEN[:1]):
        return [allrec[x] for x in FROZEN if x in allrec]
    rng = random.Random(SEED)
    by = collections.defaultdict(list)
    for d in frame():
        by[stratum(d)].append(d)
    for v in by.values():
        rng.shuffle(v)
    order = sorted(by, key=lambda k: -len(by[k]))
    picked, dioceses = [], set()
    while len(picked) < N and any(by.values()):
        for k in order:
            while by[k]:
                d = by[k].pop()
                if d['diocese'] not in dioceses:
                    picked.append(d); dioceses.add(d['diocese']); break
            if len(picked) >= N:
                break
    return picked


def text_of(raw):
    raw = re.sub(r'(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>', ' ', raw)
    raw = re.sub(r'(?i)<br\s*/?>|</(p|div|li|tr|h\d|td|th)>', '\n', raw)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', raw))
    lines = [re.sub(r'[ \t\xa0]+', ' ', l).strip() for l in t.split('\n')]
    return '\n'.join(l for l in lines if l)


def fetch(outdir):
    os.makedirs(outdir, exist_ok=True)
    last, robots = {}, {}
    only = set(sys.argv[3:])
    for i, d in enumerate(sample(), 1):
        if only and str(i) not in only:
            continue
        urls = []
        for p in d.get('pages', []):
            if p.get('category') in ('mass_times', 'confession', 'ministries') and p['url'] not in urls:
                urls.append(p['url'])
        for r in d['schedule_rows']:
            if r.get('source_url') and r['source_url'] not in urls:
                urls.insert(0, r['source_url'])
        for u in urls[:5]:
            host = urllib.parse.urlsplit(u).netloc
            if host not in robots:
                rp = urllib.robotparser.RobotFileParser()
                try:  # fetch robots.txt with our own UA (robotparser's default UA is refused by some WAFs)
                    req = urllib.request.Request(f'https://{host}/robots.txt', headers={'User-Agent': UA})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        rp.parse(r.read().decode('utf-8', 'replace').splitlines())
                except Exception:
                    rp.parse([])  # missing/unreadable robots.txt: no rules
                robots[host] = rp
                last[host] = time.time()
            if not robots[host].can_fetch(UA, u):
                print('robots-disallow', u); continue
            wait = 1.1 - (time.time() - last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            last[host] = time.time()
            try:
                req = urllib.request.Request(u, headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=25) as r:
                    body = r.read().decode('utf-8', 'replace')
                st = 'ok'
            except Exception as e:
                body, st = '', f'error {e}'
            fn = os.path.join(outdir, f'{i:02d}-{d["domain"].replace("/", "_")}-{abs(hash(u)) % 10**6}.txt')
            with open(fn, 'w') as fh:
                fh.write(f'URL {u}\nSTATUS {st}\n\n{text_of(body)}')
            print(i, d['domain'], st[:60], u)


RAW_US = os.path.join(ROOT, '..', 'laneG4', 'data', 'raw', 'us', 'sites')  # gitignored crawl cache (lane G4 worktree)
SKIP_KINDS = ('tribe_events', 'ics', 'wp_pages')


def live(outdir):
    """Independent re-check (2026-11-01 08:xx): fetch each site's cached page list live so parse_site can run on today's HTML."""
    import gzip, hashlib
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(outdir, exist_ok=True)

    def one(rec):
        key = rec['domain']
        mp = os.path.join(os.environ.get('RAW_US', RAW_US), key.replace('/', '__'), 'manifest.json')
        if os.path.exists(mp):
            m = json.load(open(mp)); src = [p for p in m['pages'] if p.get('class') == 'ok' and p['kind'] not in SKIP_KINDS]
        else:
            m = {'key': key, 'home': rec['home_url'], 'final_home': rec.get('final_url'), 'status': 'ok'}
            src = [{'url': p['url'], 'kind': p['category']} for p in rec['pages'] if p.get('class') == 'ok' and p['category'] not in SKIP_KINDS]
        d = os.path.join(outdir, key.replace('/', '__')); os.makedirs(d, exist_ok=True)
        host = urllib.parse.urlsplit(src[0]['url']).netloc if src else ''
        rp = urllib.robotparser.RobotFileParser()
        try:
            with urllib.request.urlopen(urllib.request.Request(f'https://{host}/robots.txt', headers={'User-Agent': UA}), timeout=20) as r:
                rp.parse(r.read().decode('utf-8', 'replace').splitlines())
        except Exception:
            rp.parse([])
        delay, last, pages = min(30.0, max(1.1, float(rp.crawl_delay(UA) or 0))), 0.0, []
        for i, p in enumerate(src):
            if not rp.can_fetch(UA, p['url']):
                continue
            time.sleep(max(0.0, delay - (time.time() - last))); last = time.time()
            q, body = dict(p), b''
            try:
                with urllib.request.urlopen(urllib.request.Request(p['url'], headers={'User-Agent': UA}), timeout=25) as r:
                    body = r.read(); q.update(status=r.status, final_url=r.geturl())
            except Exception as e:  # noqa: BLE001 - recorded, never retried harder
                q['status'] = getattr(e, 'code', None)
            if body:
                q['file'] = f"{i:02d}_{hashlib.sha1(p['url'].encode()).hexdigest()[:12]}.html.gz"
                with gzip.open(os.path.join(d, q['file']), 'wb') as fh:
                    fh.write(body)
            q['class'] = 'ok' if body else 'fail'
            q['fetched_at'] = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
            pages.append(q)
        m2 = dict(m, pages=pages, env='sandbox-live-recheck')
        json.dump(m2, open(os.path.join(d, 'manifest.json'), 'w'), indent=1)
        return key, sum(q['class'] == 'ok' for q in pages), len(src)

    with ThreadPoolExecutor(10) as ex:  # one thread per site = one request at a time per host
        for r in ex.map(one, sample()):
            print(*r, flush=True)


def liveparse(outdir, tag):
    import pathlib
    sys.path.insert(0, os.path.join(ROOT, 'ingest', 'california', 'sites'))
    import parse_site as P
    res = {}
    for rec in sample():
        key = rec['domain']; d = pathlib.Path(outdir) / key.replace('/', '__')
        inp = os.path.join(os.environ.get('RAW_US', RAW_US), '_inputs', f"{rec['diocese']}.json")  # the crawl's own input entry
        info = next((s for s in json.load(open(inp))['sites'] if s['key'] == key), None) if os.path.exists(inp) else None
        info = info or {'key': key, 'home': rec['home_url'], 'slug': rec['diocese'], 'names': rec.get('names', []),
                        'cities': rec.get('cities', []), 'directory_ids': [], 'discovermass_ids': []}
        r = P.parse_site(d, json.load(open(d / 'manifest.json')), info, time.strftime('%Y-%m-%d'))
        r.pop('_pages', None); P.screen_record(r); res[key] = r
    json.dump(res, open(os.path.join(outdir, f'parsed_{tag}.json'), 'w'), indent=1, ensure_ascii=False)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'sample'
    if cmd == 'sample':
        for i, d in enumerate(sample(), 1):
            print(i, d['diocese'], d['domain'], stratum(d), len(d['schedule_rows']), len(d['groups']), d['_file'])
    elif cmd == 'ours':  # compact view of our rows and groups for the hand comparison
        only = set(sys.argv[2:])
        for i, d in enumerate(sample(), 1):
            if only and str(i) not in only:
                continue
            print(f'== {i} {d["domain"]} {d.get("names")} {stratum(d)}')
            rows = collections.defaultdict(list)
            for r in d['schedule_rows']:
                rows[(r['kind'], r.get('language') or '')].append(f"{(r.get('day') or '?')[:3]} {r.get('time')}")
            for k, v in rows.items():
                print('  ', k, '; '.join(v))
            print('   GROUPS:', ' | '.join(g['name'] for g in d['groups']))
    elif cmd == 'fetch':
        fetch(sys.argv[2])
    elif cmd == 'live':
        live(sys.argv[2])
    elif cmd == 'liveparse':
        liveparse(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'current')
