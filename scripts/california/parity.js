// Usage (offline; needs only node and the TypeScript compiler, no npm install):
//   tsc --outDir /tmp/findjs --rootDir lib --module commonjs --moduleResolution node --target ES2022 --skipLibCheck \
//       --ignoreDeprecations 6.0 --ignoreConfig lib/find.ts          (any tsc 5+; e.g. /opt/npm-tools/node_modules/typescript/bin/tsc)
//   FIND_JS=/tmp/findjs/find.js node scripts/california/parity.js public/ca
const F = require(process.env.FIND_JS || '/tmp/findjs/find.js'); const fs = require('fs'); const path = require('path');
const root = process.argv[2] || 'public/ca'; let n = 0, diff = 0; const ex = [];
for (const d of fs.readdirSync(root)) { const p = path.join(root, d, 'p'); if (d === 'oakland' || !fs.existsSync(p)) continue;
  for (const f of fs.readdirSync(p)) { const j = JSON.parse(fs.readFileSync(path.join(p, f))); n++;
    const a = JSON.stringify(j.rows.map(r => [r.day, r.time, r.source]).sort()); const b = JSON.stringify(F.preferCuratedRows(j.rows).map(r => [r.day, r.time, r.source]).sort());
    if (a !== b) { diff++; if (ex.length < 3) ex.push(j.id); } } }
console.log('cards', n, 'client != merge', diff, ex);
