// Usage (offline; needs only node and the TypeScript compiler, no npm install):
//   tsc --outDir /tmp/findjs --rootDir lib --module commonjs --moduleResolution node --target ES2022 --skipLibCheck \
//       --ignoreDeprecations 6.0 --ignoreConfig lib/find.ts          (any tsc 5+; e.g. /opt/npm-tools/node_modules/typescript/bin/tsc)
//   FIND_JS=/tmp/findjs/find.js node scripts/california/check15.js public/ca ingest/california/sites/templates/truth/review15-seed20261031.json
// Re-run of the 15-parish Mass-time check (docs/review/ca-check-2026-10-31.md, seed 20261031) on public/ca/<slug>/p/<id>.json:
// the card's rows through lib/find.ts preferCuratedRows + timesOn (as DioceseView.Card), weekly slots over Nov 8-14 2026,
// monthly rules once; truth = ingest/california/sites/templates/truth/review15-seed20261031.json. Language: none = English.
const F = require(process.env.FIND_JS || '/tmp/findjs/find.js'); const fs = require('fs'); const path = require('path');
const root = process.argv[2]; const truth = JSON.parse(fs.readFileSync(process.argv[3])).parishes;
const canon = (l) => { if (!l) return 'English'; const f = l.toLowerCase(); if (/bilingual|\/|&| and /.test(f)) return 'Bilingual'; if (/espa|spanish/.test(f)) return 'Spanish'; if (/viet/.test(f)) return 'Vietnamese'; return l[0].toUpperCase() + l.slice(1).toLowerCase(); };
const okLang = (a, b) => a === 'Bilingual' || b === 'Bilingual' || canon(a) === canon(b);
let T = { correct: 0, wrong: 0, missing: 0, mis: 0 }; const per = [];
for (const [pid, tr] of Object.entries(truth)) {
  const slug = pid.split('--')[0]; const f = path.join(root, slug, 'p', pid + '.json');
  const d = JSON.parse(fs.readFileSync(f)); const rows = F.preferCuratedRows(d.rows ?? []);
  const shown = new Map(); // key -> language
  for (let i = 0; i < 7; i++) { // Nov 8 (Sun) .. Nov 14 (Sat)
    const iso = `2026-11-${String(8 + i).padStart(2, '0')}`; const ld = F.localDate(iso);
    for (const t of F.timesOn(rows, ld, ['mass'])) {
      if (t.note && /month|first|second|third|fourth|last|\d(st|nd|rd|th)/i.test(t.note)) continue; // monthly: counted below
      const h = String(Math.floor(t.minutes / 60)).padStart(2, '0'), m = String(t.minutes % 60).padStart(2, '0');
      const k = `w|${ld.dow}|${h}:${m}`; if (!shown.has(k) || (!shown.get(k) && t.language)) shown.set(k, t.language);
    }
  }
  // monthly rows: day text with an ordinal, once per (weekday, nth, time)
  for (const r of rows) { if (F.publicKind ? F.publicKind(r) !== 'mass' : r.kind !== 'mass') continue; const rule = F.rowRule(r); if (!rule || !rule.nth) continue;
    for (const dd of rule.days) for (const n of rule.nth) shown.set(`m|${dd}|${n}|${(r.time || '').slice(0, 5)}`, r.language); }
  const tk = new Map(); for (const [dow, t, l] of tr.w) tk.set(`w|${dow}|${t}`, l); for (const [dow, n, t] of tr.m) tk.set(`m|${dow}|${n}|${t}`, null);
  const neutral = new Set((tr.neutral_m || []).map(([dow, n, t]) => `m|${dow}|${n}|${t}`));
  const s = { pid, correct: 0, wrong: 0, missing: 0, mis: 0, notes: [] };
  for (const [k, l] of shown) { if (neutral.has(k)) continue; if (tk.has(k)) { if (okLang(l, tk.get(k))) s.correct++; else { s.mis++; s.notes.push(`LANG ${k} ${l} vs ${tk.get(k)}`); } } else { s.wrong++; s.notes.push(`WRONG ${k} ${l ?? ''}`); } }
  for (const [k, l] of tk) if (!shown.has(k)) { s.missing++; s.notes.push(`MISSING ${k} ${l ?? ''}`); }
  for (const x of ['correct', 'wrong', 'missing', 'mis']) T[x] += s[x]; per.push(s);
}
const union = T.correct + T.wrong + T.missing + T.mis;
for (const s of per) console.log(`${s.pid.padEnd(62)} ok ${s.correct} wrong ${s.wrong} missing ${s.missing} lang ${s.mis}  ${s.notes.join('; ')}`);
console.log(`TOTAL errors ${T.wrong + T.missing + T.mis} / ${union} = ${(100 * (T.wrong + T.missing + T.mis) / union).toFixed(1)}%  (wrong ${T.wrong}, missing ${T.missing}, mislabelled ${T.mis}); parishes with an error: ${per.filter((s) => s.wrong + s.missing + s.mis).length} of ${per.length}`);
