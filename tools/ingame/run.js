#!/usr/bin/env node
// Plays animations inside a real RPGReactor game project's battle scene, on the
// machine's real GPU, and reports how they load and run. See README § In-game
// test tool. One fresh headless Chrome (temporary profile) per job; the project
// is served read-only with page.js injected, and nothing in it is changed.
//
//   node tools/ingame/run.js --project <game dir> --out <dir> --ids 317,318 [options]
//   node tools/ingame/run.js --project <game dir> --out <dir> --jobs jobs.json
//
// Options (CLI form applies to every job; a jobs file can set them per job):
//   --mode time|capture    time: first-cast freeze, frame times, end (default)
//                          capture: per-frame pixels of what the player sees
//   --repeat N             run each id N times (time mode), default 1
//   --step N               capture every Nth game frame, default 1
//   --shots N              also save a JPEG every N captured game frames
//   --warmup ID            animation played (and loaded) first to absorb the
//                          runtime's one-time start-up cost; 0 = none, default 1
//   --troop ID             troop to fight, default 1
//   --map prefix=dir       serve effects/<prefix>... from <dir> instead
//   --sub name=file        serve every effects/.../<name> from <file>
//   --override /url=file   serve any URL path from <file> (e.g. /js/reactor_plugins.js)
//   --prepare "<js>"       run in the page after the new game is set up and
//                          before the battle scene is created (runtime only)
//   --preload-id           (no value) add the tested id to the preload plugin's
//                          extraIds before the battle scene is created, so the
//                          cold play is the cast a player sees with
//                          plugins/ReactorEffectPreload.js covering it. Without
//                          it the tested id is normally NOT preloaded (the plugin
//                          only loads what the party, troop and troop events
//                          use) and the freeze is an uncovered cast. Tags get
//                          -pre. The job fails if the plugin is missing or the
//                          effect was not loaded before the cold play.
//   --headed               show Chrome for real GPU performance measurements
//   --port N               default 8901; pick one nobody else is using
//   --chrome <path>        default C:/Program Files/Google/Chrome/Application/chrome.exe
//   --max-frames N         time mode: game frames (Graphics.frameCount, 60 per
//                          second) an animation may play before it counts as
//                          not ending, default 3000. Not browser frames: on a
//                          faster display those tick 2-3 times per game frame.
//   --capture-seconds S    capture mode: the same limit in seconds, default 120
//   --timeout S            per job; default fits the limits above: at least
//                          240, 260 for a default time-mode job
//
// Jobs file: [{ "tag": "...", "id": 317, "mode": "time", "map": {}, "sub": {},
//              "override": {}, "prepare": "...", "preloadId": false, "warmup": 1, "troop": 1, "step": 1, "shots": 0,
//              "maxFrames": 3000, "captureSeconds": 120, "timeout": 240 }]
// Output: <out>/results.jsonl (one line per job) and <out>/<tag>/ for captures.
// A capture job first deletes the frame files (f###.rgba, shot-f###.jpg) an
// earlier run with the same tag left in <out>/<tag>/, so a shorter rerun cannot
// leave stale frames behind; the result records how many (clearedStaleFrames).
// Each results line carries status ok|warn|fail with its failures and warnings
// (tools/ingame/verdict.js). Every job runs even after one fails.
// Exit status: 0 every job ok or warn, 1 at least one job failed (timeout, page
// or engine error, effect load error, animation never started or never ended),
// 2 usage error (bad arguments, missing project/Chrome, busy port).
const fs = require('fs'), http = require('http'), path = require('path');
const { spawn } = require('child_process');
const { verdict } = require('./verdict');

const argv = process.argv.slice(2);
const opts = { multi: { map: [], sub: [], override: [] } };
const FLAGS = ['preload-id', 'headed'];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (!a.startsWith('--')) throw new Error('unexpected argument ' + a);
  if (FLAGS.includes(a.slice(2))) { opts[a.slice(2)] = true; continue; }
  const k = a.slice(2), v = argv[++i];
  if (v === undefined) throw new Error('missing value for ' + a);
  if (k in opts.multi) opts.multi[k].push(v); else opts[k] = v;
}
const need = (k) => { if (!opts[k]) { console.error('missing --' + k); process.exit(2); } return opts[k]; };
const ROOT = path.resolve(need('project'));
const OUT = path.resolve(need('out'));
const port = +(opts.port || 8901);
const chromePath = opts.chrome || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
if (!fs.existsSync(path.join(ROOT, 'index.html'))) { console.error('no index.html in ' + ROOT); process.exit(2); }
if (!fs.existsSync(chromePath)) { console.error('Chrome not found: ' + chromePath); process.exit(2); }

const pairs = (list) => Object.fromEntries(list.map((s) => { const i = s.indexOf('='); return [s.slice(0, i), path.resolve(s.slice(i + 1))]; }));
const cliShared = {
  mode: opts.mode || 'time', step: opts.step ? +opts.step : undefined, shots: opts.shots ? +opts.shots : undefined,
  warmup: opts.warmup !== undefined ? +opts.warmup : undefined, troop: opts.troop ? +opts.troop : undefined,
  prepare: opts.prepare, preloadId: !!opts['preload-id'], map: pairs(opts.multi.map), sub: pairs(opts.multi.sub), override: pairs(opts.multi.override),
  maxFrames: opts['max-frames'] ? +opts['max-frames'] : undefined,
  captureSeconds: opts['capture-seconds'] ? +opts['capture-seconds'] : undefined,
  timeout: opts.timeout ? +opts.timeout : undefined,
};
for (const k of ['maxFrames', 'captureSeconds', 'timeout']) {
  if (cliShared[k] !== undefined && !(cliShared[k] > 0)) { console.error(`--${k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())} must be a positive number`); process.exit(2); }
}
let jobs;
if (opts.jobs) jobs = JSON.parse(fs.readFileSync(opts.jobs, 'utf8'));
else {
  const ids = need('ids').split(',').map(Number);
  const repeat = +(opts.repeat || 1);
  jobs = [];
  for (let k = 0; k < repeat; k++) for (const id of ids) jobs.push({ tag: `${cliShared.mode}-${id}` + (cliShared.preloadId ? '-pre' : '') + (repeat > 1 ? `-${k}` : ''), id });
  jobs = jobs.map((j) => ({ ...cliShared, ...j }));
}
jobs.forEach((j, n) => { if (!j.tag) j.tag = `job${n}-${j.id}`; if (!j.mode) j.mode = 'time'; });
// The job's own limit decides when it is cut off; the default leaves room for it.
// Time mode: two plays, each up to 3000 game frames of loading plus --max-frames
// of play at 60 fps; capture: 60 s of loading plus --capture-seconds; plus start-up.
const jobTimeout = (j) => j.timeout || Math.max(240, Math.ceil(j.mode === 'capture' ? (j.captureSeconds || 120) + 150 : 2 * (3000 + (j.maxFrames || 3000)) / 60 + 60));
const FRAME_FILE = /^(f\d+\.rgba|shot-f\d+\.jpg)$/;
fs.mkdirSync(OUT, { recursive: true });

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.wasm': 'application/wasm', '.css': 'text/css', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf' };
let job = null, pending = null;
const served = [];

function resolveFile(u) {
  if (job.override && job.override[u]) return job.override[u];
  if (u.startsWith('/effects/')) {
    const base = path.basename(u);
    if (job.sub && job.sub[base]) return job.sub[base];
    if (job.map) for (const [pre, dir] of Object.entries(job.map)) {
      if (u.startsWith('/effects/' + pre)) return path.join(dir, u.slice(('/effects/' + pre).length));
    }
  }
  const f = path.resolve(path.join(ROOT, u === '/' ? 'index.html' : u));
  return f.startsWith(ROOT) ? f : null;
}

const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]);
  if (req.method === 'POST' && u.startsWith('/__')) {
    const parts = []; req.on('data', (d) => parts.push(d)); req.on('end', () => {
      res.end('ok');
      const body = Buffer.concat(parts);
      const dir = path.join(OUT, job.tag);
      const frame = String(req.headers['x-frame'] || '').padStart(3, '0');
      if (u === '/__frame') { fs.mkdirSync(dir, { recursive: true }); fs.writeFileSync(path.join(dir, 'f' + frame + '.rgba'), body); }
      else if (u === '/__shot') { fs.mkdirSync(dir, { recursive: true }); fs.writeFileSync(path.join(dir, 'shot-f' + frame + '.jpg'), Buffer.from(body.toString().split(',')[1], 'base64')); }
      else if (u === '/__result' && pending) pending(JSON.parse(body.toString()));
    });
    return;
  }
  if (u === '/__ingame.js') { res.writeHead(200, { 'Content-Type': 'text/javascript' }); return res.end(fs.readFileSync(path.join(__dirname, 'page.js'))); }
  if (u === '/__job.json') { res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end(JSON.stringify(job)); }
  const f = resolveFile(u);
  if (!f) { res.writeHead(403); return res.end(); }
  fs.readFile(f, (err, data) => {
    if (err) { res.writeHead(404); return res.end(); }
    if (u.startsWith('/effects/')) served.push(u);
    if (u === '/' || u === '/index.html') data = Buffer.from(data.toString().replace('</body>', '<script src="/__ingame.js"></script></body>'));
    res.writeHead(200, { 'Content-Type': MIME[path.extname(f).toLowerCase()] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    res.end(data);
  });
});

async function runOne(j) {
  job = j; served.length = 0;
  let cleared = 0;
  const capDir = path.join(OUT, j.tag);
  if (j.mode === 'capture' && fs.existsSync(capDir)) {
    for (const f of fs.readdirSync(capDir)) if (FRAME_FILE.test(f)) { fs.unlinkSync(path.join(capDir, f)); cleared++; }
  }
  const profile = fs.mkdtempSync(path.join(OUT, '.chrome-'));
  const chrome = spawn(chromePath, [...(opts.headed ? [] : ['--headless=new']), '--use-angle=d3d11', '--ignore-gpu-blocklist', '--enable-gpu', '--no-first-run',
    '--no-default-browser-check', '--window-size=1280,720', '--user-data-dir=' + profile, ...(opts.headed ? ['--app=' + `http://127.0.0.1:${port}/`] : [`http://127.0.0.1:${port}/`])], { stdio: 'ignore' });
  chrome.once('error', (e) => { if (pending) pending({ runnerError: 'Chrome failed: ' + e.message }); });
  let timer;
  const result = await Promise.race([
    new Promise((r) => (pending = r)),
    new Promise((r) => { timer = setTimeout(() => r({ timeout: true, timeoutSeconds: jobTimeout(j) }), jobTimeout(j) * 1000); }),
  ]);
  clearTimeout(timer); pending = null;
  await new Promise((r) => { chrome.once('exit', r); try { chrome.kill(); } catch (e) { r(); } setTimeout(r, 5000); });
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  return { tag: j.tag, ...result, ...(opts.headed ? {headed: true} : {}), servedEffectFiles: served.slice(), ...(cleared ? { clearedStaleFrames: cleared } : {}) };
}

server.on('error', (e) => { console.error(e.code === 'EADDRINUSE' ? `port ${port} is busy: pick another with --port` : e); process.exit(2); });
server.listen(port, '127.0.0.1', async () => {
  const outFile = path.join(OUT, 'results.jsonl');
  const tally = { ok: 0, warn: 0, fail: 0 };
  for (const j of jobs) {
    let r;
    try { r = await runOne(j); } catch (e) { pending = null; r = { tag: j.tag, runnerError: String((e && e.message) || e) }; }
    Object.assign(r, verdict(r));
    tally[r.status]++;
    fs.appendFileSync(outFile, JSON.stringify(r) + '\n');
    let line = r.runnerError ? 'RUNNER ERROR ' + r.runnerError : r.timeout ? 'TIMEOUT' : r.pageError ? 'PAGE ERROR ' + r.pageError.slice(0, 300) : 'played';
    if (r.cold) line += `  freeze ${r.cold.worstFrameDuringLoadMs} ms (${r.cold.loadedBefore ? 'preloaded' : 'not preloaded'}), load ${r.cold.loadMs} ms, ${r.cold.gameFrames} frames, ended ${r.cold.ended}, frames>20ms ${r.cold.framesOver20ms}`;
    if (r.capture) line += `  ${r.capture.frames} frames captured, ended ${r.capture.ended}`;
    if (r.clearedStaleFrames) line += `  (deleted ${r.clearedStaleFrames} frame file(s) left by an earlier run of this tag)`;
    if (r.preload) line += `  preload ${r.preload.requested.length} effect(s), battle ready after ${r.preload.readyMs} ms${r.preload.timedOut ? ' (timed out)' : ''}`;
    if (r.errors && r.errors.length) line += '  LOAD ERRORS ' + r.errors.join(', ');
    if (r.engineErrors && r.engineErrors.length) line += '  ENGINE ERRORS ' + r.engineErrors.length;
    if (r.status !== 'ok') line += `  => ${r.status.toUpperCase()}: ` + r.failures.concat(r.warnings).join('; ');
    console.log(j.tag.padEnd(28), line);
  }
  console.log(`INGAME: ${jobs.length} job(s), ${tally.ok} ok, ${tally.warn} warn, ${tally.fail} failed`);
  server.close(); process.exit(tally.fail ? 1 : 0);
});
