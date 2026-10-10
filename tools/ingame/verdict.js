// Pass/fail rules for one tools/ingame/run.js result (README § In-game test tool).
// Shared by run.js (exit status) and compare.js (is a capture usable evidence).
//
// failures: the job did not measure what it was asked to (timeout, page or
//   engine error, effect load error, animation never started or never ended).
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
  const measured = !r.timeout && !r.pageError && !r.runnerError;
  if (measured && !r.cold && !r.capture) failures.push('result has neither time nor capture data');
  for (const k of ['cold', 'warm']) {
    const p = r[k];
    if (!p) continue;
    if (p.loadErr) failures.push(`${k} play: effect failed to load`);
    if (p.loadPending) failures.push(`${k} play: effect never finished loading (the runtime reported neither load nor error; corrupt file?)`);
    if (p.requestToStartMs === null || p.requestToStartMs === undefined) failures.push(`${k} play: animation never started within 3000 frames of the request`);
    else if (!p.ended) failures.push(`${k} play: animation did not end within ${p.frameLimit || 3000} frames (--max-frames)`);
  }
  if (r.cold && !r.warm && measured) failures.push('time mode result has no warm play');
  if (r.capture) {
    if (!(r.capture.frames >= 0)) failures.push('capture: animation never started (no frame captured)');
    else if (!r.capture.ended) failures.push(`capture: animation did not end within ${r.capture.captureSeconds || 120} s (--capture-seconds; capture is incomplete)`);
  }
  if (r.preload && r.preload.timedOut) warnings.push('preload plugin timed out');
  const consoleErrors = Object.keys(r.console || {}).filter((m) => m.startsWith('error:'));
  if (consoleErrors.length) warnings.push(`${consoleErrors.length} distinct console error(s)`);
  return { status: failures.length ? 'fail' : warnings.length ? 'warn' : 'ok', failures, warnings };
}

module.exports = { verdict };
