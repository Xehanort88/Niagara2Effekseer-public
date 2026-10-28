// Injected into the game page by tools/ingame/run.js. Test-only runtime changes,
// none written to disk:
// - SceneManager.isGameActive is forced true: the engine pauses without window
//   focus, and a headless page never has focus.
// - The battle is held still after it starts (BattleManager.update and sound
//   effects disabled) so only the animation under test varies.
// - In capture mode the game runs exactly one update per browser frame, so runs
//   are repeatable frame for frame.
// Hooks go on functions the engine looks up by name each frame: the PIXI ticker
// keeps its own bound copy of Graphics._onTick, so wrapping that does nothing.
(async function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const post = (obj) => fetch('/__result', { method: 'POST', body: JSON.stringify(obj) });
  window.addEventListener('error', (e) => post({ pageError: String(e.message) }));
  const job = await (await fetch('/__job.json')).json();
  const ID = job.id;
  // How long an animation may play, from its first frame, before it counts as
  // not ending (run.js --max-frames / --capture-seconds). Loading has its own
  // allowance: an animation not started this many frames after the request
  // never started. Both count game frames (Graphics.frameCount, 60 per second),
  // not browser frames: on a 144-165 Hz display requestAnimationFrame ticks two
  // to three times per game frame, and a browser-frame limit cut effects off
  // at a third of their length. A game that stops updating (an engine
  // exception calls SceneManager.stop) would never reach either limit, so a
  // play also ends when the game frame stands still for STALL_FRAMES browser
  // frames (10 s at 60 Hz, 3.6 s at 165 Hz; a load freeze is one long frame).
  const MAX_FRAMES = job.maxFrames || 3000, CAPTURE_S = job.captureSeconds || 120, LOAD_FRAMES = 3000, STALL_FRAMES = 600;
  const P = { loads: {}, renderMs: 0, updMs: 0, finish: false, peakLive: 0, longTasks: [], engineErrors: [] };
  // Console warnings and errors (first 40 distinct, with repeat counts).
  const consoleLog = {};
  for (const level of ['warn', 'error']) {
    const orig = console[level].bind(console);
    console[level] = function (...a) {
      const msg = level + ': ' + a.map((x) => (x && x.message) || String(x)).join(' ').slice(0, 300);
      if (consoleLog[msg]) consoleLog[msg]++; else if (Object.keys(consoleLog).length < 40) consoleLog[msg] = 1;
      return orig(...a);
    };
  }
  const postResult = post;
  const postWithConsole = (obj) => postResult({ ...obj, console: consoleLog });
  try {
    new PerformanceObserver((l) => { for (const e of l.getEntries()) P.longTasks.push(Math.round(e.duration)); })
      .observe({ type: 'longtask', buffered: true });
  } catch (e) {}

  function hookEngine() {
    SceneManager.isGameActive = () => true;
    const ce = SceneManager.catchException;
    SceneManager.catchException = function (e) { P.engineErrors.push(String((e && e.stack) || e).slice(0, 800)); return ce.call(this, e); };
    const sl = EffectManager.startLoading;
    EffectManager.startLoading = function (url) { P.loads[url] = { t0: performance.now() }; return sl.call(this, url); };
    const ol = EffectManager.onLoad;
    EffectManager.onLoad = function (url) { if (P.loads[url]) P.loads[url].t1 = performance.now(); return ol.apply(this, arguments); };
    const oe = EffectManager.onError;
    EffectManager.onError = function (url) { if (P.loads[url]) P.loads[url].err = true; return oe.apply(this, arguments); };
    const ue = SceneManager.updateEffekseer;
    SceneManager.updateEffekseer = function () { const t = performance.now(); ue.call(this); P.updMs += performance.now() - t; };
    // Wrappers keep the context they are called on: since runtime 20261027.3 the engine steps the in-scene
    // context with the overlay's own update (SceneLayer.update(Graphics.effekseer.update), called on the scene's
    // context). A wrapper bound to the overlay stepped the overlay instead, so in-scene effects never advanced or
    // ended and every run reported "did not end".
    const ctx = Graphics.effekseer, gl = Graphics._effekseerGL;
    const dh = ctx.drawHandle;
    ctx.drawHandle = function (h) { if (P.finish) gl.finish(); const t = performance.now(); dh.call(this, h); if (P.finish) gl.finish(); P.renderMs += performance.now() - t; };
    const up = ctx.update;
    // Live instances per context: its free count when idle (the most seen) minus the fewest free during the play;
    // the overlay's pool and the in-scene context's differ in size.
    const pools = new Map();
    ctx.update = function (d) {
      up.call(this, d);
      if (!this.getRestInstancesCount) return;
      const rest = this.getRestInstancesCount();
      let st = pools.get(this);
      if (!st) { st = { most: rest }; pools.set(this, st); }
      st.most = Math.max(st.most, rest);
      P.peakLive = Math.max(P.peakLive, st.most - rest);
    };
    hookSceneLayer();
  }

  // Screen animations drawn inside PIXI's scene (Sprite_Animation.SceneLayer, runtime 20261027.3+): they load in the
  // layer's own Effekseer context (not through EffectManager) and draw from a RenderContainer during PIXI's render.
  const sceneLayer = () => (window.Sprite_Animation && Sprite_Animation.SceneLayer) || null;
  const inScene = () => { const L = sceneLayer(); return !!(L && L.active && L.active()); };
  function hookSceneLayer() {
    const L = sceneLayer();
    if (!L) return;
    const ec = L.ensureContext;
    L.ensureContext = function () {
      const c = ec.call(this);
      if (c && !c.__ingameHooked) {
        c.__ingameHooked = true;
        const le = c.loadEffect;
        c.loadEffect = function (url, scale, onload, onerror) {
          P.loads[url] = { t0: performance.now() };
          return le.call(this, url, scale,
            function () { if (P.loads[url]) P.loads[url].t1 = performance.now(); if (onload) return onload.apply(this, arguments); },
            function () { if (P.loads[url]) P.loads[url].err = true; if (onerror) return onerror.apply(this, arguments); });
        };
      }
      return c;
    };
    for (const name of ['draw', 'drawHdr']) {
      const f = L[name];
      if (typeof f !== 'function') continue;
      L[name] = function (renderer) {
        const g = renderer && renderer.gl;
        if (P.finish && g) g.finish();
        const t = performance.now();
        try { return f.apply(this, arguments); } finally { if (P.finish && g) g.finish(); P.renderMs += performance.now() - t; }
      };
    }
  }
  // An effect's cache entry on the path the game plays it on now.
  function cacheEntry(effectName) {
    if (inScene()) return sceneLayer().cache.get(effectName) || null;
    return EffectManager._cache[EffectManager.makeUrl(effectName)] || null;
  }

  // Mean colour of the rendered battle scene before the animation: a run whose
  // backdrop did not load (it happens in headless runs) shows up here.
  // Read through a 2D canvas in the same task as the render, so no GL state of
  // the game's renderer is touched.
  function sceneMean() {
    const c = document.createElement('canvas'); c.width = 320; c.height = 180;
    const c2 = c.getContext('2d', { willReadFrequently: true });
    c2.drawImage(Graphics._canvas, 0, 0, 320, 180);
    const px = c2.getImageData(0, 0, 320, 180).data;
    const s = [0, 0, 0];
    for (let i = 0; i < px.length; i += 16) { s[0] += px[i]; s[1] += px[i + 1]; s[2] += px[i + 2]; }
    const n = px.length / 16;
    return s.map((v) => +(v / n).toFixed(1));
  }
  function grabSceneMean() {
    return new Promise((res) => {
      const ra = Sprite_Animation.renderActive;
      Sprite_Animation.renderActive = function (renderer) { ra.call(this, renderer); Sprite_Animation.renderActive = ra; res(sceneMean()); };
    });
  }

  async function idle() {
    const d = []; let l = performance.now();
    await new Promise((r) => { let k = 0; function t(now) { d.push(now - l); l = now; if (++k < 180) requestAnimationFrame(t); else r(); } requestAnimationFrame(t); });
    d.sort((a, b) => a - b); return { medianMs: +d[90].toFixed(1), maxMs: +d[179].toFixed(1) };
  }

  async function probe(id, { finish = false, maxFrames = MAX_FRAMES } = {}) {
    P.finish = finish; P.peakLive = 0; P.longTasks.length = 0;
    const anim = $dataAnimations[id];
    const url = EffectManager.makeUrl(anim.effectName);
    const entry = cacheEntry(anim.effectName);
    const cached = !!entry;
    // Fully loaded before the request (preloaded): the cast a player sees when
    // plugins/ReactorEffectPreload.js covers it. cachedBefore alone is also true
    // for a load still in progress.
    const loaded = cached && !!entry.isLoaded;
    const ss = SceneManager._scene._spriteset;
    const frames = []; let started = null, ended = null, stalled = false;
    const tReq = performance.now();
    $gameTemp.requestAnimation($gameTroop.aliveMembers(), id);
    // n counts browser frames (frame-time statistics); the limits use gf, the
    // game frames since the request.
    let last = performance.now(), n = 0;
    const gf0 = Graphics.frameCount; let lastGf = gf0, lastGfN = 0;
    await new Promise((res) => {
      function tick(now) {
        const dt = now - last; last = now; n++;
        const sp = (ss._animationSprites || []).find((s) => s._animation === anim);
        frames.push({ dt, upd: P.updMs, rend: P.renderMs }); P.updMs = 0; P.renderMs = 0;
        if (started === null && sp && sp._started) started = { n, gf: Graphics.frameCount, ms: now - tReq };
        if (started !== null && !ss.isAnimationPlaying()) { ended = { n, gf: Graphics.frameCount }; return res(); }
        const gf = Graphics.frameCount;
        if (gf !== lastGf) { lastGf = gf; lastGfN = n; } else if (n - lastGfN > STALL_FRAMES) { stalled = true; return res(); }
        if (started === null ? gf - gf0 > LOAD_FRAMES : gf - started.gf > maxFrames) return res();
        requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
    const L = P.loads[url] || {};
    const play = started ? frames.slice(started.n) : [];
    const pre = started ? frames.slice(0, started.n) : frames;
    const srt = (a) => a.slice().sort((x, y) => x - y);
    const dts = srt(play.map((f) => f.dt)), rs = srt(play.map((f) => f.rend));
    const q = (a, p, d = 1) => (a.length ? +a[Math.min(a.length - 1, Math.floor(p * a.length))].toFixed(d) : null);
    return {
      id, name: anim.name, effectName: anim.effectName, cachedBefore: cached, loadedBefore: loaded,
      loadMs: L.t1 && !cached ? Math.round(L.t1 - L.t0) : null, loadErr: !!L.err,
      // Requested but neither loaded nor failed: the runtime gave no answer (a
      // corrupt .efkefc does this), so the game waits for it forever.
      loadPending: !cached && !!L.t0 && !L.t1 && !L.err,
      requestToStartMs: started ? Math.round(started.ms) : null,
      worstFrameDuringLoadMs: pre.length ? Math.round(Math.max(...pre.map((f) => f.dt))) : null,
      longTasksMs: P.longTasks.slice(),
      gameFrames: started && ended ? ended.gf - started.gf : null, ended: !!ended, frameLimit: maxFrames, frameLimitUnit: 'game', stalled,
      // Browser frames over the play: about gameFrames on a 60 Hz display, more
      // on a faster one. frameMs and framesOver20ms are per browser frame.
      browserFrames: started ? (ended ? ended.n : n) - started.n : null,
      frameMs: { median: q(dts, 0.5), p95: q(dts, 0.95), max: q(dts, 1) },
      framesOver20ms: play.filter((f) => f.dt > 20).length,
      drawMs: { median: q(rs, 0.5, 2), p95: q(rs, 0.95, 2), max: q(rs, 1, 2), gpuSynced: finish },
      updateMsMax: play.length ? +Math.max(...play.map((f) => f.upd)).toFixed(2) : null,
      peakLiveInstances: P.peakLive,
    };
  }

  async function capture(id, step, shots) {
    SceneManager.determineRepeatNumber = function () { return 1; };
    const anim = $dataAnimations[id];
    const ss = SceneManager._scene._spriteset;
    const gl = Graphics._effekseerGL;
    // In the scene the game's own canvas holds the finished frame (read through a 2D canvas in the render's task,
    // no GL state touched); on the overlay path the overlay's buffer holds the composited frame.
    const scene = inScene();
    const W = scene ? Graphics._canvas.width : gl.drawingBufferWidth, H = scene ? Graphics._canvas.height : gl.drawingBufferHeight;
    const full = new Uint8Array(W * H * 4);
    const comp = document.createElement('canvas'); comp.width = 640; comp.height = 360;
    const c2 = comp.getContext('2d');
    const half = document.createElement('canvas'); half.width = W >> 1; half.height = H >> 1;
    const h2 = half.getContext('2d', { willReadFrequently: true });
    let startGf = null, done = false, lastF = -1;
    const sends = [];
    const ra = Sprite_Animation.renderActive;
    Sprite_Animation.renderActive = function (renderer) {
      ra.call(this, renderer);
      if (done) return;
      const sp = (ss._animationSprites || []).find((s) => s._animation === anim);
      if (startGf === null && sp && sp._started) startGf = Graphics.frameCount;
      if (startGf === null) return;
      const f = Graphics.frameCount - startGf;
      if (f !== lastF && f % step === 0) {
        lastF = f;
        const w = W >> 1, h = H >> 1;
        let out;
        if (scene) {
          h2.imageSmoothingEnabled = true; h2.imageSmoothingQuality = 'high';
          h2.drawImage(Graphics._canvas, 0, 0, w, h);
          out = new Uint8Array(h2.getImageData(0, 0, w, h).data.buffer);
        } else {
          // While an effect plays, the overlay holds the whole composited frame.
          gl.bindFramebuffer(gl.FRAMEBUFFER, null);
          gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, full);
          out = new Uint8Array(w * h * 4);
          for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) for (let c = 0; c < 4; c++) {
            const i = ((2 * y) * W + 2 * x) * 4 + c;
            out[((h - 1 - y) * w + x) * 4 + c] = (full[i] + full[i + 4] + full[i + W * 4] + full[i + W * 4 + 4] + 2) >> 2;
          }
        }
        sends.push(fetch('/__frame', { method: 'POST', headers: { 'x-frame': String(f) }, body: out }));
        if (shots && f % shots === 0) {
          c2.drawImage(Graphics._canvas, 0, 0, 640, 360);
          if (!scene) c2.drawImage(Graphics._effekseerCanvas, 0, 0, 640, 360);
          sends.push(fetch('/__shot', { method: 'POST', headers: { 'x-frame': String(f) }, body: comp.toDataURL('image/jpeg', 0.9) }));
        }
      }
      if (!ss.isAnimationPlaying()) done = true;
    };
    $gameTemp.requestAnimation($gameTroop.aliveMembers(), id);
    for (let i = 0; i < CAPTURE_S * 20 && !done; i++) await sleep(50);
    await Promise.all(sends);
    return { frames: lastF, ended: done, size: [W >> 1, H >> 1], step, captureSeconds: CAPTURE_S };
  }

  try {
    for (let i = 0; i < 900; i++) {
      if (window.SceneManager) SceneManager.isGameActive = () => true;
      if (window.SceneManager && SceneManager._scene && SceneManager._scene.constructor.name === 'Scene_Title' &&
          DataManager.isDatabaseLoaded() && Graphics.effekseer) break;
      await sleep(100);
    }
    if (!window.Graphics || !Graphics.effekseer) throw new Error('game never reached the title screen with Effekseer running');
    hookEngine();
    const anim = $dataAnimations[ID];
    if (!anim || !anim.effectName) throw new Error(`animation ${ID} has no Effekseer effect`);
    const glr = (() => { const g = Graphics._effekseerGL; const d = g.getExtension('WEBGL_debug_renderer_info'); return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER); })();
    DataManager.setupNewGame();
    // run.js --preload-id: the tested id joins the preload plugin's list, as
    // --prepare "ReactorEffectPreload.params.extraIds.push(ID);" would.
    if (job.preloadId) {
      if (!window.ReactorEffectPreload || !ReactorEffectPreload.params) throw new Error('--preload-id: the project does not run plugins/ReactorEffectPreload.js (window.ReactorEffectPreload missing)');
      if (!ReactorEffectPreload.params.extraIds.includes(ID)) ReactorEffectPreload.params.extraIds.push(ID);
    }
    // run.js --overlay: the engine's overlay canvas instead of PIXI's scene (its fallback path).
    if (job.overlay) Graphics.effekseerInScene = false;
    if (job.prepare) (0, eval)(job.prepare);
    BattleManager.setup(job.troop || 1, true, false);
    const tGoto = performance.now();
    SceneManager.goto(Scene_Battle);
    for (let i = 0; i < 1200 && !(SceneManager._scene instanceof Scene_Battle && SceneManager._scene.isStarted()); i++) await sleep(10);
    const battleStartMs = Math.round(performance.now() - tGoto);
    const preloadRecord = window.ReactorEffectPreload && ReactorEffectPreload.last ? { ...ReactorEffectPreload.last } : null;
    if (preloadRecord) preloadRecord.readyMs = preloadRecord.readyMs === null ? null : Math.round(preloadRecord.readyMs);
    await sleep(2500); $gameMessage.clear();
    BattleManager.update = function () {}; AudioManager.playSe = function () {};
    await sleep(500);
    const warmupId = job.warmup === undefined ? 1 : job.warmup;
    let warmup = null;
    if (warmupId && $dataAnimations[warmupId] && $dataAnimations[warmupId].effectName && warmupId !== ID) {
      // The stock warm-up always gets the default limit, not the job's.
      warmup = await probe(warmupId, { maxFrames: 3000 }); await sleep(500);
    }
    const backdrop = await grabSceneMean();
    const common = { id: ID, preloadId: !!job.preloadId, effekseerPath: inScene() ? 'scene' : 'overlay', renderer: glr, battleStartMs, preload: preloadRecord, warmupLoadMs: warmup && warmup.loadMs, sceneMean: backdrop };
    if (job.mode === 'capture') {
      // Loaded before the capture starts, on the path the animation will play on.
      if (inScene()) sceneLayer().load(anim.effectName); else EffectManager.load(anim.effectName);
      for (let i = 0; i < 1200 && !(cacheEntry(anim.effectName) && cacheEntry(anim.effectName).isLoaded); i++) await sleep(50);
      const cap = await capture(ID, job.step || 1, job.shots || 0);
      postWithConsole({ ...common, capture: cap, errors: EffectManager._errorUrls.slice(), engineErrors: P.engineErrors });
      return;
    }
    const idleBase = await idle();
    const cold = await probe(ID);
    await sleep(500);
    const warm = await probe(ID, { finish: true });
    postWithConsole({ ...common, idle: idleBase, cold, warm, errors: EffectManager._errorUrls.slice(), engineErrors: P.engineErrors });
  } catch (e) {
    postWithConsole({ id: ID, pageError: String((e && e.stack) || e), engineErrors: P.engineErrors });
  }
})();
