#!/usr/bin/env node
// Compares capture-mode runs from tools/ingame/run.js frame by frame.
//
//   node tools/ingame/compare.js --out <run.js out dir> --base <tag> [--noise <tag>] [--threshold 8] [--allow-unrecorded] <tag> [<tag> ...]
//
// For every captured frame, counts pixels whose largest channel differs from the
// base run by more than --threshold (0..255) at the capture's half resolution.
// --noise names a second run of identical files: its counts are the run-to-run
// floor, and a frame only counts as different when it beats the floor by 200 px.
//
// Every capture (base, noise and each run) is checked before any pixel is
// counted, against its own line in results.jsonl. It is UNUSABLE when the
// record is missing or says the job failed (timeout, page/engine/load error,
// animation never ended), when a frame file is empty, not width x height x 4
// bytes, or differs in size from the base's, when a frame the record says was
// captured is absent, or when an extra frame file is present (stale capture
// left from an earlier run with the same tag). Runs whose battle backdrop
// differs from the base (sceneMean, a headless loading failure) are UNUSABLE
// too and must be rerun. An unusable run gets no pixel counts (null), so it can
// never read as "0 px different"; an unusable base or noise run makes every
// comparison unusable.
//
// --allow-unrecorded accepts a capture with no results.jsonl line (a folder
// assembled by hand). Its frames still get every file check, and the frame
// step is inferred from the file names, so a frame lost in the middle is still
// caught; whether the run reached its end cannot be known, so the run carries
// a warning saying so instead of a problem.
//
// Two usable captures can still differ in length (an edit that ends the effect
// sooner or later). That is a real result, not bad evidence: the run is marked
// LENGTH DIFFERS, and the frames only one side has are listed, not counted.
//
// Prints one line per run and writes compare.json (valid: false when anything
// is unusable). Exit 0 every comparison usable, 1 any capture unusable, 2 usage.
const fs = require('fs'), path = require('path');
const { verdict } = require('./verdict');
const argv = process.argv.slice(2), opts = { tags: [] };
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--allow-unrecorded') opts.allowUnrecorded = true;
  else if (argv[i].startsWith('--')) opts[argv[i].slice(2)] = argv[++i]; else opts.tags.push(argv[i]);
}
const usage = (msg) => { console.error((msg ? msg + '\n' : '') + 'usage: compare.js --out <dir> --base <tag> [--noise <tag>] [--threshold 8] [--allow-unrecorded] <tag>...'); process.exit(2); };
if (!opts.out || !opts.base) usage();
if (!opts.tags.length && !opts.noise) usage('no run to compare (or --noise to measure the floor alone)');
const OUT = path.resolve(opts.out), TH = +(opts.threshold === undefined ? 8 : opts.threshold), MARGIN = 200;
if (!(TH >= 0 && TH <= 255)) usage('--threshold must be 0..255');
if (!fs.existsSync(OUT)) usage('no such folder ' + OUT);
const results = {};
const rf = path.join(OUT, 'results.jsonl');
// Later lines win: a rerun with the same tag appends a newer record.
if (fs.existsSync(rf)) for (const l of fs.readFileSync(rf, 'utf8').split('\n')) if (l.trim()) { const r = JSON.parse(l); results[r.tag] = r; }

const gcd = (a, b) => (b ? gcd(b, a % b) : a);
const frameName = (f) => 'f' + String(f).padStart(3, '0');
// Reads and checks one capture. Returns { frames, problems, warnings, expectBytes }.
function load(tag) {
  const problems = [], warnings = [], frames = {};
  const rec = results[tag];
  const dir = path.join(OUT, tag);
  if (!rec) {
    if (opts.allowUnrecorded) warnings.push('no results.jsonl record: whether the run reached its end (its last frames) is unverified');
    else problems.push('no results.jsonl record, so the capture cannot be shown complete (--allow-unrecorded compares it anyway)');
  } else {
    const v = verdict(rec);
    for (const f of v.failures) problems.push('job failed: ' + f);
    if (!rec.capture && !v.failures.length) problems.push('record is not a capture-mode run');
  }
  if (!fs.existsSync(dir)) { problems.push('no capture folder ' + dir); return { frames, problems, warnings }; }
  for (const f of fs.readdirSync(dir).sort()) if (f.endsWith('.rgba')) frames[f.slice(0, -5)] = fs.readFileSync(path.join(dir, f));
  const names = Object.keys(frames);
  if (!names.length) problems.push('no frame files');
  const cap = rec && rec.capture;
  const expectBytes = cap && Array.isArray(cap.size) ? cap.size[0] * cap.size[1] * 4 : null;
  for (const n of names) {
    const len = frames[n].length;
    if (len === 0) problems.push(`${n}: empty frame file`);
    else if (len % 4) problems.push(`${n}: ${len} bytes is not whole RGBA pixels`);
    else if (expectBytes !== null && len !== expectBytes) problems.push(`${n}: ${len} bytes, expected ${expectBytes} (${cap.size[0]}x${cap.size[1]} RGBA)`);
  }
  const sizes = new Set(names.map((n) => frames[n].length));
  if (sizes.size > 1) problems.push(`frame files differ in size (${[...sizes].join(', ')} bytes)`);
  let last = null, step = null;
  if (cap && cap.frames >= 0 && cap.step > 0) { last = cap.frames; step = cap.step; }
  else if (!rec && names.length) {
    // No record: frames run from f000 in equal steps, so infer the step.
    const bad = names.filter((n) => !/^f\d+$/.test(n));
    if (bad.length) problems.push(`unexpected frame file name(s) ${bad.slice(0, 6).join(' ')}`);
    const nums = names.filter((n) => /^f\d+$/.test(n)).map((n) => +n.slice(1));
    if (nums.length) { last = Math.max(...nums); step = nums.reduce(gcd, 0) || 1; }
  }
  if (last !== null) {
    const expected = [];
    for (let f = 0; f <= last; f += step) expected.push(frameName(f));
    const lost = expected.filter((n) => !frames[n]), extra = names.filter((n) => !expected.includes(n));
    if (lost.length) problems.push(`${lost.length} captured frame(s) missing on disk (${lost.slice(0, 6).join(' ')}${lost.length > 6 ? ' ...' : ''})`);
    if (extra.length) problems.push(`${extra.length} frame file(s) the record does not account for, stale from an earlier run? (${extra.slice(0, 6).join(' ')}${extra.length > 6 ? ' ...' : ''})`);
  }
  return { frames, problems, warnings, expectBytes };
}
function count(a, b) {
  if (a.length !== b.length || a.length === 0 || a.length % 4) throw new Error(`frame sizes ${a.length} and ${b.length} bytes cannot be compared`);
  let n = 0;
  for (let i = 0; i < a.length; i += 4) {
    const d = Math.max(Math.abs(a[i] - b[i]), Math.abs(a[i + 1] - b[i + 1]), Math.abs(a[i + 2] - b[i + 2]));
    if (d > TH) n++;
  }
  return n;
}
function backdropOff(tag) {
  const a = results[opts.base] && results[opts.base].sceneMean, b = results[tag] && results[tag].sceneMean;
  if (!a || !b) return false;
  return a.some((v, i) => Math.abs(v - b[i]) > 2);
}
// Problems with a pair of individually usable captures that make counts meaningless.
function pairProblems(a, b, tag) {
  const p = [];
  const sa = new Set(Object.values(a.frames).map((x) => x.length)), sb = new Set(Object.values(b.frames).map((x) => x.length));
  if ([...sb].some((s) => !sa.has(s))) p.push(`frame size ${[...sb].join('/')} bytes differs from the base's ${[...sa].join('/')}`);
  if (backdropOff(tag)) p.push('battle backdrop differs from the base (headless load failure): rerun');
  return p;
}
const brief = (list) => list.slice(0, 3).join('; ') + (list.length > 3 ? `; +${list.length - 3} more` : '');

const report = { base: opts.base, noise: opts.noise || null, threshold: TH, valid: true, runs: {} };
const base = load(opts.base);
report.baseProblems = base.problems;
report.baseWarnings = base.warnings;
if (base.warnings.length) console.log(`base (${opts.base}) WARNING: ${brief(base.warnings)}`);
if (base.problems.length) { report.valid = false; console.log(`base (${opts.base}) UNUSABLE: ${brief(base.problems)}`); }

let noise = null, noisePer = null;
if (opts.noise) {
  noise = load(opts.noise);
  const problems = noise.problems.slice();
  if (!problems.length && !base.problems.length) {
    problems.push(...pairProblems(base, noise, opts.noise));
    const bn = Object.keys(base.frames), nn = Object.keys(noise.frames);
    // Identical files must play identically; a length change means the floor is not a floor.
    if (bn.length !== nn.length || bn.some((f) => !noise.frames[f])) problems.push(`noise run has ${nn.length} frame(s), base has ${bn.length}: identical runs must match`);
  }
  if (problems.length || base.problems.length) {
    report.valid = false;
    report.noiseFloor = { status: 'UNUSABLE', problems, worstPx: null, totalPx: null, frames: null };
    console.log(`noise floor (${opts.noise}) UNUSABLE: ${brief(problems.length ? problems : ['base unusable'])}`);
  } else {
    noisePer = {};
    for (const f in base.frames) noisePer[f] = count(base.frames[f], noise.frames[f]);
    const v = Object.values(noisePer);
    report.noiseFloor = { status: 'ok', problems: [], warnings: noise.warnings, worstPx: Math.max(...v), totalPx: v.reduce((s, x) => s + x, 0), frames: v.length, backdrop: 'ok' };
    console.log(`noise floor (${opts.noise}): worst ${report.noiseFloor.worstPx} px, total ${report.noiseFloor.totalPx} px over ${v.length} frames` +
      (noise.warnings.length ? `  WARNING: ${brief(noise.warnings)}` : ''));
  }
}

for (const tag of opts.tags) {
  const run = load(tag);
  const problems = run.problems.slice();
  if (!problems.length && !base.problems.length) problems.push(...pairProblems(base, run, tag));
  const r = { status: 'ok', problems, warnings: run.warnings, frames: null, worstFrame: null, worstPx: null, totalPx: null, framesAboveNoise: null,
    framesOnlyInBase: [], framesOnlyInRun: [], backdrop: backdropOff(tag) ? 'DIFFERENT' : 'ok' };
  report.runs[tag] = r;
  if (problems.length || base.problems.length) {
    r.status = 'UNUSABLE'; report.valid = false;
    console.log(tag.padEnd(36), 'UNUSABLE: ' + brief(problems.length ? problems : ['base unusable']));
    continue;
  }
  const per = {};
  for (const f in base.frames) if (run.frames[f]) per[f] = count(base.frames[f], run.frames[f]); else r.framesOnlyInBase.push(f);
  r.framesOnlyInRun = Object.keys(run.frames).filter((f) => !base.frames[f]);
  // A shorter capture without a record may simply have been cut off.
  const lengthDiffers = r.framesOnlyInBase.length || r.framesOnlyInRun.length;
  if (lengthDiffers && (!results[tag] || !results[opts.base])) {
    r.status = 'UNUSABLE'; report.valid = false;
    r.problems.push(`length differs from the base and ${!results[tag] ? tag : opts.base} has no record, so a cut-off capture cannot be ruled out`);
    console.log(tag.padEnd(36), 'UNUSABLE: ' + brief(r.problems));
    continue;
  }
  const entries = Object.entries(per);
  const worst = entries.reduce((m, e) => (e[1] > m[1] ? e : m), [null, -1]);
  Object.assign(r, { frames: entries.length, worstFrame: worst[0], worstPx: entries.length ? worst[1] : null, totalPx: entries.reduce((s, e) => s + e[1], 0) });
  if (noisePer) r.framesAboveNoise = entries.filter(([f, n]) => n - noisePer[f] > MARGIN).map(([f]) => f);
  if (lengthDiffers) r.status = 'LENGTH DIFFERS';
  const over = r.framesAboveNoise;
  console.log(tag.padEnd(36), `worst ${r.worstPx} px at ${r.worstFrame}, total ${r.totalPx} over ${r.frames} frames` +
    (over ? `, ${over.length} frame(s) above noise` + (over.length ? ` (${over.slice(0, 8).join(' ')}${over.length > 8 ? ' ...' : ''})` : '') : '') +
    (r.status === 'LENGTH DIFFERS' ? `  LENGTH DIFFERS: ${r.framesOnlyInBase.length} frame(s) only in base, ${r.framesOnlyInRun.length} only in this run, not counted` : '') +
    (r.warnings.length ? `  WARNING: ${brief(r.warnings)}` : ''));
}
fs.writeFileSync(path.join(OUT, 'compare.json'), JSON.stringify(report, null, 1));
if (!report.valid) console.log('COMPARE: evidence UNUSABLE for at least one capture; see compare.json problems');
process.exit(report.valid ? 0 : 1);
