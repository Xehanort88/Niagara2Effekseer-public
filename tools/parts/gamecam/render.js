#!/usr/bin/env node
// Drive tools/parts/batch.html under headless Chrome and collect one thumbnail
// per part.
//
// The page does all the work; this is a static file server that also accepts
// the results. No CDP, no browser-automation dependency: Chrome is pointed at a
// URL and the run is over when the page says it is.
//
// Chrome needs --enable-unsafe-swiftshader here. Headless has no GPU, and
// without the flag WebGL is refused outright rather than falling back, which
// looks exactly like every effect drawing nothing.

const fs = require('fs');
const http = require('http');
const path = require('path');
const { spawn } = require('child_process');

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

// This copy lives one folder deeper than tools/parts/render.js, so the repo root is three levels up.
// (Two levels pointed the default harness at tools/tools/render-harness: every library request 404'd and the
// page never reported, so the run hung silently until killed.)
const ROOT = path.resolve(__dirname, '..', '..', '..');
const partsDir = path.resolve(arg('parts', path.join(ROOT, 'out', 'parts', 'all')));
const harnessDir = path.resolve(arg('harness', path.join(ROOT, 'tools', 'render-harness')));
for (const f of ['effekseer.min.js', 'effekseer.wasm']) {
  if (!fs.existsSync(path.join(harnessDir, f))) {
    console.error('no ' + f + ' in ' + harnessDir + ': pass --harness <dir holding the Effekseer web runtime>, e.g. a game project\'s js/libs');
    process.exit(2);
  }
}
// A page that never reports (a runtime it cannot start, an effect it cannot load) must not hang the run.
const timeoutSeconds = parseInt(arg('timeout', '900'), 10);
const outDir = path.resolve(arg('out', partsDir));
const port = parseInt(arg('port', '8741'), 10);
const limit = parseInt(arg('limit', '0'), 10);
// Thumbnail edge in pixels; the stage renders at 320, so 320 is full size.
const thumbSize = parseInt(arg('thumb', '160'), 10);
const chromePath = arg('chrome', 'C:/Program Files/Google/Chrome/Application/chrome.exe');

const thumbDir = path.join(outDir, 'thumbs');
fs.mkdirSync(thumbDir, { recursive: true });

// The manifest normally sits beside the parts, but source effects inside a
// corpus are served straight from it, and nothing may be written there.
const manifestPath = path.resolve(arg('manifest', path.join(partsDir, 'manifest.json')));
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
let parts = manifest.parts.filter((p) => p.efkefc);
// The page loads '/parts/' + efkefc, and the runtime resolves the effect's
// textures relative to that URL: a Windows backslash breaks both.
for (const p of parts) p.efkefc = p.efkefc.split('\\').join('/');
if (limit) parts = parts.slice(0, limit);
console.log('rendering ' + parts.length + ' part(s) from ' + partsDir);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.png': 'image/png',
  '.efkefc': 'application/octet-stream',
  '.efkmodel': 'application/octet-stream',
  '.efkmat': 'application/octet-stream',
};

function serveFile(res, file) {
  fs.readFile(file, (err, buf) => {
    if (err) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream' });
    res.end(buf);
  });
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch (e) { resolve({}); }
    });
  });
}

const results = [];
let finished = null;
const started = Date.now();

const server = http.createServer(async (req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);

  if (req.method === 'POST' && url === '/result') {
    const r = await readBody(req);
    if (r.png) {
      const b64 = r.png.slice(r.png.indexOf(',') + 1);
      fs.writeFileSync(path.join(thumbDir, r.id + '.png'), Buffer.from(b64, 'base64'));
      r.thumb = 'thumbs/' + r.id + '.png';
    }
    delete r.png;
    // Filmstrip frames, when the manifest asked for them, are written beside
    // the thumbnail as <id>-f<frame>.png and replaced by their file names.
    if (Array.isArray(r.strip)) {
      r.strip = r.strip.map((shot) => {
        const file = 'thumbs/' + r.id + '-f' + shot.frame + '.png';
        if (shot.png) {
          const b64 = shot.png.slice(shot.png.indexOf(',') + 1);
          fs.writeFileSync(path.join(outDir, file), Buffer.from(b64, 'base64'));
        }
        return { frame: shot.frame, lit: shot.lit, file };
      });
    }
    results.push(r);
    if (results.length % 100 === 0) {
      const rate = results.length / ((Date.now() - started) / 1000);
      console.log('  ' + results.length + '/' + parts.length +
                  '  ' + rate.toFixed(1) + '/s  eta ' +
                  Math.round((parts.length - results.length) / rate) + 's');
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('{"ok":true}');
    return;
  }

  if (req.method === 'POST' && url === '/done') {
    const body = await readBody(req);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('{"ok":true}');
    if (finished) finished(body);
    return;
  }

  if (url === '/' || url === '/batch.html') return serveFile(res, path.join(__dirname, 'batch.html'));
  if (url === '/manifest') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ parts }));
    return;
  }
  if (url.startsWith('/harness/')) return serveFile(res, path.join(harnessDir, url.slice(9)));
  if (url.startsWith('/parts/')) return serveFile(res, path.join(partsDir, url.slice(7)));

  res.writeHead(404);
  res.end('not found');
});

// Every run gets a throwaway Chrome profile in the temp folder, 20-30 MB each.
// Left behind, they added up to tens of GB, so it goes when the run ends, once
// Chrome has let go of its files. A process killed outright (taskkill /F) still
// leaks its profile; nothing inside the process can run then.
let profile = null;
let chrome = null;

function removeProfile() {
  if (!profile) return;
  const dir = profile;
  profile = null;
  try { fs.rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); }
  catch (e) { console.error('could not remove ' + dir + ': ' + e.message); }
}

function shutdown(code) {
  server.close();
  if (!chrome || chrome.exitCode !== null || chrome.signalCode !== null) {
    removeProfile();
    process.exit(code);
  }
  const done = () => { removeProfile(); process.exit(code); };
  chrome.once('exit', done);
  setTimeout(done, 5000).unref();
  try { chrome.kill(); } catch (e) {}
}

process.on('exit', removeProfile);
process.on('SIGINT', () => shutdown(130));
process.on('SIGTERM', () => shutdown(143));

server.listen(port, '127.0.0.1', () => {
  console.log('serving on http://127.0.0.1:' + port);
  setTimeout(() => {
    console.error('TIMEOUT: the page did not report within ' + timeoutSeconds + ' s (' + results.length + ' of ' + parts.length +
      ' parts returned); nothing written. Check --harness (' + harnessDir + ') and the effect files, or raise --timeout.');
    shutdown(1);
  }, timeoutSeconds * 1000).unref();

  profile = fs.mkdtempSync(path.join(require('os').tmpdir(), 'efkparts-'));
  chrome = spawn(chromePath, [
    '--headless=new',
    '--disable-gpu',
    '--enable-unsafe-swiftshader',
    '--no-sandbox',
    '--no-first-run',
    '--disable-dev-shm-usage',
    '--user-data-dir=' + profile,
    'http://127.0.0.1:' + port + '/batch.html?thumb=' + thumbSize,
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  chrome.stderr.on('data', (d) => {
    const s = d.toString();
    if (/error|fail/i.test(s) && !/GPU|Vulkan|dbus|gles/i.test(s)) process.stderr.write('chrome: ' + s);
  });

  new Promise((resolve) => { finished = resolve; }).then((summary) => {
    const drew = results.filter((r) => r.rendered).length;
    const blank = results.filter((r) => !r.rendered && !r.error && !r.loadError).length;
    const bad = results.filter((r) => r.error || r.loadError);

    fs.writeFileSync(path.join(outDir, 'render-report.json'), JSON.stringify({
      generated: new Date().toISOString(),
      seconds: Math.round((Date.now() - started) / 1000),
      requested: parts.length,
      returned: results.length,
      drew, blank, failed: bad.length,
      results,
    }, null, 1));

    console.log('');
    console.log('  drew      ' + drew);
    console.log('  blank     ' + blank);
    console.log('  failed    ' + bad.length);
    console.log('  seconds   ' + Math.round((Date.now() - started) / 1000));
    for (const b of bad.slice(0, 15)) console.log('    ' + b.id + '  ' + (b.error || b.loadError));
    if (summary && summary.error) console.log('  page error: ' + summary.error);

    shutdown(bad.length ? 1 : 0);
  });
});
