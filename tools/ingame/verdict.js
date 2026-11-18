// Pass/fail rules for one tools/ingame/run.js result (README § In-game test tool).
// Shared by run.js (exit status) and compare.js (is a capture usable evidence).
//
// failures: the job did not measure what it was asked to (timeout, page or
//   engine error, effect load error, animation never started or never ended,
//   a --preload-id cold play whose effect was not loaded before the cast).
// warnings: the job measured it, but something around it needs a look (console
//   errors, the preload plugin timing out). They never change the exit status.
function verdict(r) {
  const failures = [], warnings = [];
  if (!r || typeof r !== 'object') return { status: 'fail', failures: ['no result'], warnings };
  if (r.runnerError) failures.push('runner error: ' + r.runnerError);
  if (r.timeout) failures.push(`timed out${r.timeoutSeconds ? ` after ${r.timeoutSeconds} s` : ''} before the page reported a result`);
  if (r.pageError) failures.push('page error: ' + String(r.pageError).split('\n')[0].slice(0, 200));
  if (r.engineErrors && r.engineErrors.length) failures.push(`${r.engineErrors.length} engine error(s)`);
  if (r.errors && r.errors.length) failures.push('load errors: ' + r.errors.join(', '));
  // a material, texture or model the effect names that the server did not have: the layer drew without it, and the
  // engine reports no error. --sub serves only the named file; its resources load from the project folder, so a build
  // whose material code changed (a new hashed name) drew its layers flat (spark isolates of a study effect read 0.006 of
  // the game, 2026-11-18). Preview a build folder with --map (preview.py capture --efk) instead.
  if (r.missingEffectFiles && r.missingEffectFiles.length)
    failures.push(`effect resources not found (${r.missingEffectFiles.length}): ${r.missingEffectFiles.slice(0, 4).join(', ')}${r.missingEffectFiles.length > 4 ? ', ...' : ''} (--sub serves only the named file and loads its materials, textures and models from the project folder: serve the build's folder with --map, e.g. preview.py capture --efk)`);
  const measured = !r.timeout && !r.pageError && !r.runnerError;
  if (measured && !r.cold && !r.capture) failures.push('result has neither time nor capture data');
  for (const k of ['cold', 'warm']) {
    const p = r[k];
    if (!p) continue;
    if (p.loadErr) failures.push(`${k} play: effect failed to load`);
    if (p.loadPending) failures.push(`${k} play: effect never finished loading (the runtime reported neither load nor error: the runtime refuses an effect with more than 1024 rendering nodes, images, models or curves this way, run lint --strict on it; else a corrupt file)`);
    if (p.stalled) failures.push(`${k} play: the game stopped updating (no new game frame for 600 browser frames)`);
    else if (p.requestToStartMs === null || p.requestToStartMs === undefined) failures.push(`${k} play: animation never started within 3000 game frames of the request`);
    else if (!p.ended) failures.push(`${k} play: animation did not end within ${p.frameLimit || 3000} game frames (--max-frames)`);
  }
  if (r.cold && !r.warm && measured) failures.push('time mode result has no warm play');
  // --preload-id asked for the covered cast; a cold play that still loaded the
  // effect measured the uncovered one (plugin disabled, preload failed or not done).
  if (r.preloadId && r.cold && measured && !r.cold.loadedBefore) failures.push('cold play: --preload-id, but the effect was not loaded before the cast (preload plugin disabled, failed or still loading)');
  // Since runtime 20261027.3 the game draws animations in PIXI's scene; the overlay is only its fallback. A run that
  // lands there without --overlay measured the wrong engine (results before 2026-11-16 carry no overlayRequested).
  if (r.overlayRequested === false && r.effekseerPath === 'overlay') {
    failures.push(r.sceneLayer === false
      ? "played on the overlay fallback, not in the scene: the project's engine has no Sprite_Animation.SceneLayer (js/ replaced by an older engine build, e.g. by opening the project in an older editor); restore js/ from the engine template, or pass --overlay if the overlay is intended"
      : 'played on the overlay fallback, not in the scene, although the engine has an in-scene layer (Sprite_Animation.SceneLayer inactive); pass --overlay if the overlay is intended');
  }
  // --seed: a run whose plays were not seeded sampled the default draw, not the seed it is labelled with
  if (r.seedRequested !== undefined && r.seedRequested !== null && measured && !(r.seed && r.seed.plays > 0 && r.seed.value === r.seedRequested))
    failures.push(`--seed ${r.seedRequested}: no Effekseer play was seeded (context.play not reached through Graphics.effekseer's prototype)`);
  if (r.capture) {
    if (!(r.capture.frames >= 0)) failures.push('capture: animation never started (no frame captured)');
    else if (!r.capture.ended) failures.push(`capture: animation did not end within ${r.capture.captureSeconds || 120} s (--capture-seconds; capture is incomplete)`);
  }
  // --hdr-float reads the light buffer at the overlay's MSAA resolve: a capture that saw none measured nothing
  if (r.hdrFloatRequested && r.capture && measured) {
    const h = r.capture.hdrFloat;
    if (!h || !(h.blits > 0)) failures.push('--hdr-float: no HDR light-buffer resolve seen (not an HDR effect, its sidecar not served, the overlay path, or a context without multisampled float targets)');
    else if (h.sizeChanged) failures.push('--hdr-float: the light buffer changed size during the capture');
  }
  if (r.preload && r.preload.timedOut) warnings.push('preload plugin timed out');
  for (const k of ['cold', 'warm', 'capture']) {
    const p = r[k];
    if (p && p.instanceCapacity > 0 && p.peakLiveInstances >= p.instanceCapacity)
      warnings.push(`${k}: instance pool capacity reached (${p.peakLiveInstances}/${p.instanceCapacity}); particles may have been refused`);
  }
  const consoleErrors = Object.keys(r.console || {}).filter((m) => m.startsWith('error:'));
  if (consoleErrors.length) warnings.push(`${consoleErrors.length} distinct console error(s)`);
  return { status: failures.length ? 'fail' : warnings.length ? 'warn' : 'ok', failures, warnings };
}

module.exports = { verdict };
