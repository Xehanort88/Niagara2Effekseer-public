// vidframes.js: frames of a video at chosen times, without ffmpeg.
//
//   node tools/video/vidframes.js <video> <out folder> <step seconds | t1,t2,...> [--width 640] [--port 8831]
//        [--chrome <exe>] [--timeout 600]
//
// Headless Chrome plays the video (H.264/VP9/AV1 MP4 or WebM, whatever Chrome decodes), seeks to each time, draws
// the frame onto a canvas `--width` px wide and POSTs a PNG back. Files: s<seconds>_f<frame>.png, frame =
// round(t x fps). fps is read from the playing video (requestVideoFrameCallback media-time steps) and written with
// the duration and size to <out>/meta.json. Step 1 gives the 1 fps sheet AGENTS.md asks for before judging a clip.
// Chrome: --chrome, else N2E_CHROME, else the Windows install, else Playwright's Chromium (as tools/ingame/run.js).
// It only kills the Chrome it starts. Written 2026-11-07 when the ffmpeg copy AGENTS.md named was swept away.
const fs = require('fs'), http = require('http'), path = require('path');
const { spawn } = require('child_process');

const pos = [], opts = {};
for (let i = 2; i < process.argv.length; i++) {
  const a = process.argv[i];
  if (a.startsWith('--')) opts[a.slice(2)] = process.argv[++i]; else pos.push(a);
}
const [video, out, spec] = pos;
if (!video || !out || !spec) {
  console.error('usage: node tools/video/vidframes.js <video> <out folder> <step seconds | t1,t2,...> [--width W] [--port P] [--chrome EXE]');
  process.exit(2);
}
const width = +(opts.width || 640), port = +(opts.port || 8831), timeout = +(opts.timeout || 600);
function findChrome() {
  const found = ['C:/Program Files/Google/Chrome/Application/chrome.exe'];
  try { found.push(require('playwright').chromium.executablePath()); } catch (e) { /* no Playwright here */ }
  return found.find((p) => fs.existsSync(p)) || found[0];
}
const chromePath = opts.chrome || process.env.N2E_CHROME || findChrome();
if (!fs.existsSync(video)) { console.error('no video: ' + video); process.exit(2); }
if (!fs.existsSync(chromePath)) { console.error('Chrome not found: ' + chromePath); process.exit(2); }
if (!/^[\d.]+$/.test(spec) && !/^[\d.]+(,[\d.]+)+$/.test(spec)) { console.error('bad times: ' + spec); process.exit(2); }
fs.mkdirSync(out, { recursive: true });
const mime = /\.webm$/i.test(video) ? 'video/webm' : 'video/mp4';

const page = `<!doctype html><body style="margin:0;background:#000"><video id=v muted playsinline src="/video"></video>
<canvas id=c></canvas><script>
const v = document.getElementById('v'), c = document.getElementById('c'), spec = ${JSON.stringify(spec)}, W = ${width};
const post = (u, body) => fetch(u, { method: 'POST', body });
v.addEventListener('loadedmetadata', async () => {
  let fps = 0;                                    // media-time steps while playing a dozen frames
  if (v.requestVideoFrameCallback) {
    const ts = [];
    await new Promise((res) => { const cb = (n, m) => { ts.push(m.mediaTime); if (ts.length < 12) v.requestVideoFrameCallback(cb); else res(); };
      v.requestVideoFrameCallback(cb); v.play(); });
    v.pause();
    const d = ts.slice(1).map((t, i) => t - ts[i]).filter((x) => x > 0).sort((a, b) => a - b);
    fps = d.length ? 1 / d[Math.floor(d.length / 2)] : 0;
  }
  const dur = v.duration, H = Math.round(W * v.videoHeight / v.videoWidth);
  c.width = W; c.height = H; const g = c.getContext('2d');
  const times = spec.includes(',') ? spec.split(',').map(Number)
    : (() => { const s = +spec, r = []; for (let t = 0; t <= dur + 1e-6; t += s) r.push(+t.toFixed(4)); return r; })();
  await post('/meta', JSON.stringify({ duration: dur, fps, width: v.videoWidth, height: v.videoHeight, count: times.length }));
  for (const t of times) {
    await new Promise((res) => { v.addEventListener('seeked', res, { once: true }); v.currentTime = Math.min(t, dur - 0.001); });
    g.drawImage(v, 0, 0, W, H);
    const blob = await new Promise((res) => c.toBlob(res, 'image/png'));
    const f = fps ? Math.round(t * fps) : 0;
    await post('/frame?name=' + encodeURIComponent('s' + t.toFixed(3).padStart(7, '0') + '_f' + String(f).padStart(4, '0') + '.png'), blob);
  }
  await post('/done', 'ok ' + times.length + ' frame(s)');
});
v.addEventListener('error', () => post('/done', 'video error ' + (v.error && v.error.code) + ' (a codec Chrome cannot play?)'));
</script>`;

let chrome, finish, status = 1;
const done = new Promise((r) => (finish = r));
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (req.method === 'GET' && u.pathname === '/') { res.writeHead(200, { 'content-type': 'text/html' }); return res.end(page); }
  if (req.method === 'GET' && u.pathname === '/video') {
    const st = fs.statSync(video), range = req.headers.range;
    if (range) {
      const [a, b] = range.replace('bytes=', '').split('-'); const s = +a, e = b ? +b : st.size - 1;
      res.writeHead(206, { 'content-type': mime, 'accept-ranges': 'bytes', 'content-range': `bytes ${s}-${e}/${st.size}`, 'content-length': e - s + 1 });
      return fs.createReadStream(video, { start: s, end: e }).pipe(res);
    }
    res.writeHead(200, { 'content-type': mime, 'accept-ranges': 'bytes', 'content-length': st.size });
    return fs.createReadStream(video).pipe(res);
  }
  const chunks = []; req.on('data', (d) => chunks.push(d)); req.on('end', () => {
    const body = Buffer.concat(chunks);
    if (u.pathname === '/meta') { fs.writeFileSync(path.join(out, 'meta.json'), body); console.log('meta', body.toString()); }
    else if (u.pathname === '/frame') fs.writeFileSync(path.join(out, path.basename(u.searchParams.get('name'))), body);
    else if (u.pathname === '/done') { const s = body.toString(); console.log('done', s); status = s.startsWith('ok') ? 0 : 1; finish(); }
    res.end('ok');
  });
});
server.on('error', (e) => { console.error(e.code === 'EADDRINUSE' ? `port ${port} is busy: pick another with --port` : e); process.exit(2); });
server.listen(port, '127.0.0.1', () => {
  const profile = fs.mkdtempSync(path.join(out, '.chrome-'));
  chrome = spawn(chromePath, ['--headless=new', '--autoplay-policy=no-user-gesture-required', '--no-first-run',
    '--no-default-browser-check', ...(process.platform === 'win32' ? [] : ['--no-sandbox']),
    '--user-data-dir=' + profile, `http://127.0.0.1:${port}/`], { stdio: 'ignore' });
  const timer = setTimeout(() => { console.log('timeout after ' + timeout + ' s'); finish(); }, timeout * 1000);
  done.then(async () => {
    clearTimeout(timer);
    await new Promise((r) => { chrome.once('exit', r); try { chrome.kill(); } catch (e) { r(); } setTimeout(r, 5000); });
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    server.close(); process.exit(status);
  });
});
