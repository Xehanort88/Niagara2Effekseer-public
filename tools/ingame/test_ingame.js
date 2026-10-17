#!/usr/bin/env node
// Regression tests for tools/ingame (README § Testing). Node only, no packages.
//
//   node tools/ingame/test_ingame.js --work out/tests/ingame [--port 8931] [--no-chrome] [--project <game dir>]
//
// verdict: unit cases. compare: generated capture folders run through the real
// CLI. run: the real runner, server and headless Chrome against fake projects
// (one hangs, one throws, the rest post a canned result); no game is involved,
// so these are not live-game checks. --no-chrome skips the run cases.
// --project adds live cases in that game (read-only, stock animation 3): a clean
// run, then each failure path forced inside the real game (broken effect file,
// engine exception, page error, animation cut off by --max-frames and
// --capture-seconds), a capture rerun over stale frames compared live, and
// --preload-id (the project must run plugins/ReactorEffectPreload.js).
// Every file goes under --work, in a fresh run-* folder.
const fs = require('fs'), path = require('path'), assert = require('assert');
const { spawnSync } = require('child_process');
const { verdict } = require('./verdict');

const argv = process.argv.slice(2), opts = {};
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--no-chrome') opts.noChrome = true;
  else if (argv[i].startsWith('--')) opts[argv[i].slice(2)] = argv[++i];
}
if (!opts.work) { console.error('usage: test_ingame.js --work <dir> [--port N] [--no-chrome] [--project <game dir>]'); process.exit(2); }
const ROOT = path.resolve(__dirname, '..', '..');
const workRoot = path.resolve(opts.work);
for (const p of ['src', 'external', 'tests', 'tools']) {
  const s = path.join(ROOT, p);
  if (workRoot === ROOT || workRoot === s || workRoot.startsWith(s + path.sep)) { console.error('--work must be outside repository source folders'); process.exit(2); }
}
fs.mkdirSync(workRoot, { recursive: true });
const WORK = fs.mkdtempSync(path.join(workRoot, 'run-'));
const PORT = +(opts.port || 8931);
const node = (script, args) => spawnSync(process.execPath, [path.join(__dirname, script), ...args], { encoding: 'utf8', timeout: 300000 });

// ---- verdict -------------------------------------------------------------
const okTime = () => ({ tag: 't', cold: { requestToStartMs: 40, ended: true, loadErr: false }, warm: { requestToStartMs: 1, ended: true, loadErr: false }, errors: [], engineErrors: [], console: {} });
const okCap = (frames = 2, step = 1) => ({ tag: 'c', capture: { frames, ended: true, size: [2, 1], step }, errors: [], engineErrors: [], console: {}, sceneMean: [10, 10, 10] });
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('verdict_ok_time', () => assert.deepStrictEqual(verdict(okTime()), { status: 'ok', failures: [], warnings: [] }));
test('verdict_ok_capture', () => assert.strictEqual(verdict(okCap()).status, 'ok'));
test('verdict_timeout_fails', () => assert.strictEqual(verdict({ timeout: true }).status, 'fail'));
test('verdict_page_error_fails', () => assert.match(verdict({ pageError: 'Error: boom\n at x' }).failures[0], /page error: Error: boom$/));
test('verdict_engine_error_fails', () => assert.strictEqual(verdict({ ...okTime(), engineErrors: ['LoadError'] }).status, 'fail'));
test('verdict_load_error_fails', () => assert.match(verdict({ ...okTime(), errors: ['effects/x.efkefc'] }).failures.join(), /load errors: effects\/x.efkefc/));
test('verdict_never_ended_fails', () => { const r = okTime(); r.cold.ended = false; assert.match(verdict(r).failures.join(), /cold play: animation did not end/); });
test('verdict_never_started_fails', () => { const r = okTime(); r.warm.requestToStartMs = null; r.warm.ended = false; assert.match(verdict(r).failures.join(), /warm play: animation never started/); });
test('verdict_reports_job_limits', () => {
  const r = okTime(); r.cold.ended = false; r.cold.frameLimit = 20;
  assert.match(verdict(r).failures.join(), /did not end within 20 frames \(--max-frames\)/);
  const c = okCap(); c.capture.ended = false; c.capture.captureSeconds = 7;
  assert.match(verdict(c).failures.join(), /did not end within 7 s/);
  assert.match(verdict({ timeout: true, timeoutSeconds: 300 }).failures.join(), /timed out after 300 s/);
});
test('verdict_capture_not_ended_fails', () => { const r = okCap(); r.capture.ended = false; assert.strictEqual(verdict(r).status, 'fail'); });
test('verdict_capture_never_started_fails', () => assert.strictEqual(verdict(okCap(-1)).status, 'fail'));
test('verdict_empty_result_fails', () => assert.strictEqual(verdict({ tag: 'x' }).status, 'fail'));
test('verdict_load_pending_fails', () => {
  const r = okTime(); r.cold.loadPending = true; r.cold.requestToStartMs = null; r.cold.ended = false;
  assert.match(verdict(r).failures.join(), /cold play: effect never finished loading/);
});
test('verdict_preload_timeout_warns', () => { const v = verdict({ ...okTime(), preload: { timedOut: true } }); assert.strictEqual(v.status, 'warn'); assert.deepStrictEqual(v.failures, []); });
test('verdict_preload_id_needs_loaded_cold_play', () => {
  const r = { ...okTime(), preloadId: true };
  assert.match(verdict(r).failures.join(), /cold play: --preload-id, but the effect was not loaded before the cast/);
  r.cold.loadedBefore = true;
  assert.strictEqual(verdict(r).status, 'ok');
  // Without --preload-id an uncovered cast is the normal case, not a failure.
  assert.strictEqual(verdict(okTime()).status, 'ok');
});
test('verdict_console_error_warns', () => assert.strictEqual(verdict({ ...okTime(), console: { 'error: x': 2, 'warn: y': 1 } }).status, 'warn'));

// ---- compare ---------------------------------------------------------------
// Two-pixel frames (8 bytes); record size [2, 1].
const px = (...v) => Buffer.from(v.flatMap((x) => [x, x, x, 255]));
let fixtureN = 0;
function fixture(caps) {
  const out = path.join(WORK, 'compare', String(++fixtureN).padStart(2, '0'));
  const lines = [];
  for (const [tag, c] of Object.entries(caps)) {
    if (c.frames) { fs.mkdirSync(path.join(out, tag), { recursive: true }); for (const [f, b] of Object.entries(c.frames)) fs.writeFileSync(path.join(out, tag, f + '.rgba'), b); }
    if (c.record !== null) lines.push(JSON.stringify({ ...okCap(Object.keys(c.frames || {}).length - 1), tag, ...(c.record || {}) }));
  }
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(out, 'results.jsonl'), lines.join('\n') + '\n');
  return out;
}
function compare(out, args, code) {
  const r = node('compare.js', ['--out', out, ...args]);
  assert.strictEqual(r.status, code, `exit ${r.status}, expected ${code}: ${r.stdout} ${r.stderr}`);
  return { stdout: r.stdout, report: code === 2 ? null : JSON.parse(fs.readFileSync(path.join(out, 'compare.json'), 'utf8')) };
}
const good = () => ({ frames: { f000: px(255, 255), f001: px(255, 255) } });

test('compare_identical_clean', () => {
  const { report } = compare(fixture({ base: good(), noise: good(), run: good() }), ['--base', 'base', '--noise', 'noise', 'run'], 0);
  assert.strictEqual(report.valid, true);
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.worstPx, report.runs.run.frames], ['ok', 0, 2]);
});
test('compare_real_difference_counted', () => {
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: px(0, 255), f001: px(0, 0) } } }), ['--base', 'base', 'run'], 0);
  assert.deepStrictEqual([report.runs.run.worstPx, report.runs.run.worstFrame, report.runs.run.totalPx], [2, 'f001', 3]);
});
test('compare_threshold_respected', () => {
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: px(250, 255), f001: px(255, 255) } } }), ['--base', 'base', '--threshold', '8', 'run'], 0);
  assert.strictEqual(report.runs.run.totalPx, 0);
});
// The reported defect: one white pixel against an empty buffer counted 0 differences.
test('compare_empty_frames_unusable', () => {
  const { stdout, report } = compare(fixture({ base: good(), run: { frames: { f000: Buffer.alloc(0), f001: Buffer.alloc(0) } } }), ['--base', 'base', 'run'], 1);
  assert.strictEqual(report.valid, false);
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.worstPx, report.runs.run.totalPx], ['UNUSABLE', null, null]);
  assert.match(stdout, /run\s+UNUSABLE: f000: empty frame file/);
});
test('compare_truncated_frame_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: px(0), f001: px(255, 255) } } }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /f000: 4 bytes, expected 8/);
});
test('compare_partial_pixel_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: Buffer.alloc(7), f001: px(255, 255) } } }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /7 bytes is not whole RGBA pixels/);
});
test('compare_size_disagrees_with_base_unusable', () => {
  const big = { frames: { f000: px(1, 1, 1), f001: px(1, 1, 1) }, record: { capture: { frames: 1, ended: true, size: [3, 1], step: 1 } } };
  const { report } = compare(fixture({ base: good(), run: big }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /frame size 12 bytes differs from the base's 8/);
});
test('compare_lost_frame_unusable', () => {
  // The record says frames 0..1 were captured; f001 never reached the disk.
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: px(255, 255) }, record: { capture: { frames: 1, ended: true, size: [2, 1], step: 1 } } } }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /1 captured frame\(s\) missing on disk \(f001\)/);
});
test('compare_stale_extra_frame_unusable', () => {
  const run = { frames: { f000: px(255, 255), f001: px(255, 255), f002: px(0, 0) }, record: { capture: { frames: 1, ended: true, size: [2, 1], step: 1 } } };
  const { report } = compare(fixture({ base: good(), run }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /1 frame file\(s\) the record does not account for.*f002/);
});
test('compare_no_frames_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { frames: {}, record: { capture: { frames: -1, ended: false, size: [2, 1], step: 1 } } } }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /no frame files/);
});
test('compare_missing_folder_unusable', () => {
  const { report } = compare(fixture({ base: good() }), ['--base', 'base', 'ghost'], 1);
  assert.match(report.runs.ghost.problems.join(), /no results.jsonl record.*no capture folder/);
});
test('compare_no_record_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { ...good(), record: null } }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /no results.jsonl record/);
});
test('compare_allow_unrecorded_accepted_with_warning', () => {
  const { stdout, report } = compare(fixture({ base: good(), run: { ...good(), record: null } }), ['--base', 'base', '--allow-unrecorded', 'run'], 0);
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.worstPx], ['ok', 0]);
  assert.match(report.runs.run.warnings.join(), /no results.jsonl record: whether the run reached its end/);
  assert.match(stdout, /WARNING: no results.jsonl record/);
});
test('compare_allow_unrecorded_lost_frame_inferred', () => {
  // Step 2 inferred from f000 f002 f006: f004 is missing.
  const four = (v) => ({ f000: px(v, v), f002: px(v, v), f004: px(v, v), f006: px(v, v) });
  const run = { frames: { f000: px(255, 255), f002: px(255, 255), f006: px(255, 255) }, record: null };
  const { report } = compare(fixture({ base: { frames: four(255) }, run }), ['--base', 'base', '--allow-unrecorded', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /1 captured frame\(s\) missing on disk \(f004\)/);
});
test('compare_allow_unrecorded_shorter_unusable', () => {
  // Without a record a shorter capture may have been cut off: not a LENGTH DIFFERS result.
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: px(255, 255) }, record: null } }), ['--base', 'base', '--allow-unrecorded', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /length differs from the base and run has no record/);
});
test('compare_allow_unrecorded_bad_name_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { frames: { f000: px(255, 255), f001: px(255, 255), frame2: px(1, 1) }, record: null } }), ['--base', 'base', '--allow-unrecorded', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /unexpected frame file name\(s\) frame2/);
});
test('compare_failed_job_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { ...good(), record: { capture: { frames: 1, ended: false, size: [2, 1], step: 1 } } } }), ['--base', 'base', 'run'], 1);
  assert.match(report.runs.run.problems.join(), /job failed: capture: animation did not end/);
});
test('compare_backdrop_unusable', () => {
  const { report } = compare(fixture({ base: good(), run: { ...good(), record: { sceneMean: [90, 10, 10] } } }), ['--base', 'base', 'run'], 1);
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.backdrop], ['UNUSABLE', 'DIFFERENT']);
});
test('compare_base_unusable_voids_all', () => {
  const { report } = compare(fixture({ base: { frames: { f000: Buffer.alloc(0), f001: px(255, 255) } }, run: good() }), ['--base', 'base', 'run'], 1);
  assert.strictEqual(report.baseProblems.length > 0, true);
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.worstPx], ['UNUSABLE', null]);
});
test('compare_empty_noise_unusable', () => {
  const { stdout, report } = compare(fixture({ base: good(), noise: { frames: { f000: Buffer.alloc(0), f001: Buffer.alloc(0) } }, run: good() }), ['--base', 'base', '--noise', 'noise', 'run'], 1);
  assert.deepStrictEqual([report.noiseFloor.status, report.noiseFloor.worstPx], ['UNUSABLE', null]);
  assert.match(stdout, /noise floor \(noise\) UNUSABLE/);
  // The run itself is fine; it just gets no above-noise verdict.
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.framesAboveNoise], ['ok', null]);
});
test('compare_noise_length_mismatch_unusable', () => {
  const noise = { frames: { f000: px(255, 255) } };
  const { report } = compare(fixture({ base: good(), noise, run: good() }), ['--base', 'base', '--noise', 'noise', 'run'], 1);
  assert.match(report.noiseFloor.problems.join(), /noise run has 1 frame\(s\), base has 2/);
});
test('compare_length_differs_reported_not_counted', () => {
  // Both captures are complete; the edit simply ends one frame sooner.
  const { stdout, report } = compare(fixture({ base: good(), run: { frames: { f000: px(255, 255) } } }), ['--base', 'base', 'run'], 0);
  assert.deepStrictEqual([report.runs.run.status, report.runs.run.framesOnlyInBase, report.runs.run.frames], ['LENGTH DIFFERS', ['f001'], 1]);
  assert.match(stdout, /LENGTH DIFFERS: 1 frame\(s\) only in base/);
});
test('compare_above_noise', () => {
  const big = (v) => Buffer.from(Array.from({ length: 300 }, () => [v, v, v, 255]).flat());
  const cap = { capture: { frames: 0, ended: true, size: [300, 1], step: 1 } };
  const { report } = compare(fixture({ base: { frames: { f000: big(255) }, record: cap }, noise: { frames: { f000: big(255) }, record: cap }, run: { frames: { f000: big(0) }, record: cap } }), ['--base', 'base', '--noise', 'noise', 'run'], 0);
  assert.deepStrictEqual(report.runs.run.framesAboveNoise, ['f000']);
});
test('compare_noise_floor_alone', () => {
  const { stdout, report } = compare(fixture({ base: good(), noise: good() }), ['--base', 'base', '--noise', 'noise'], 0);
  assert.deepStrictEqual([report.noiseFloor.status, report.noiseFloor.worstPx], ['ok', 0]);
  assert.match(stdout, /noise floor \(noise\): worst 0 px/);
});
test('compare_usage_errors', () => {
  const out = fixture({ base: good() });
  for (const args of [['run'], ['--base', 'base'], ['--base', 'base', '--threshold', '-1', 'run']]) {
    const r = node('compare.js', ['--out', out, ...args]);
    assert.strictEqual(r.status, 2, `exit ${r.status} for ${args.join(' ')}`);
  }
});

// ---- run.js under headless Chrome --------------------------------------------
function project(name, body) {
  const dir = path.join(WORK, 'projects', name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'index.html'), `<html><body>${body}</body></html>`);
  return dir;
}
const postResult = (r) => `<script>fetch('/__result', { method: 'POST', body: JSON.stringify(${JSON.stringify(r)}) });</script>`;
function runjs(name, proj, args, code) {
  const out = path.join(WORK, 'run', name);
  const r = node('run.js', ['--project', proj, '--out', out, '--port', String(PORT), ...args]);
  assert.strictEqual(r.status, code, `exit ${r.status}, expected ${code}: ${r.stdout} ${r.stderr}`);
  const lines = fs.readFileSync(path.join(out, 'results.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  return { stdout: r.stdout, lines };
}
if (!opts.noChrome) {
  test('run_timeout_exits_1', () => {
    const { stdout, lines } = runjs('timeout', project('hang', 'no game here'), ['--ids', '1', '--timeout', '6'], 1);
    assert.deepStrictEqual([lines[0].status, lines[0].timeout], ['fail', true]);
    assert.match(stdout, /INGAME: 1 job\(s\), 0 ok, 0 warn, 1 failed/);
  });
  test('run_page_error_exits_1', () => {
    const { lines } = runjs('page-error', project('throw', '<script>setTimeout(function () { throw new Error("fake boom"); }, 1500);</script>'), ['--ids', '1', '--timeout', '60'], 1);
    assert.strictEqual(lines[0].status, 'fail');
    assert.match(lines[0].failures[0], /page error: .*fake boom/);
  });
  test('run_independent_jobs_all_finish', () => {
    const jobs = path.join(WORK, 'jobs-two.json');
    fs.writeFileSync(jobs, JSON.stringify([{ tag: 'first', id: 1 }, { tag: 'second', id: 2 }]));
    const { lines } = runjs('two-jobs', project('hang2', 'no game here'), ['--jobs', jobs, '--timeout', '5'], 1);
    assert.deepStrictEqual(lines.map((l) => [l.tag, l.status]), [['first', 'fail'], ['second', 'fail']]);
  });
  test('run_canned_ok_exits_0', () => {
    const { lines } = runjs('ok', project('ok', postResult(okTime())), ['--ids', '1', '--timeout', '60'], 0);
    assert.deepStrictEqual([lines[0].status, lines[0].failures, lines[0].warnings], ['ok', [], []]);
  });
  test('run_canned_warning_exits_0', () => {
    const { stdout, lines } = runjs('warn', project('warn', postResult({ ...okTime(), preload: { requested: [], readyMs: 5, timedOut: true } })), ['--ids', '1', '--timeout', '60'], 0);
    assert.strictEqual(lines[0].status, 'warn');
    assert.match(stdout, /=> WARN: preload plugin timed out/);
  });
  test('run_canned_never_ended_exits_1', () => {
    const r = okTime(); r.cold.ended = false;
    const { lines } = runjs('never-ended', project('never', postResult(r)), ['--ids', '1', '--timeout', '60'], 1);
    assert.match(lines[0].failures.join(), /cold play: animation did not end within 3000 frames/);
  });
  // The page echoes the job it was given, so the flag's path from the CLI and a
  // jobs file to page.js is checked without a game.
  const echoJob = (cold) => `<script>fetch('/__job.json').then((r) => r.json()).then((j) => {
    const r = ${JSON.stringify(okTime())}; delete r.tag; r.preloadId = j.preloadId; Object.assign(r.cold, ${JSON.stringify(cold)});
    fetch('/__result', { method: 'POST', body: JSON.stringify(r) }); });</script>`;
  test('run_preload_id_flag_reaches_page', () => {
    const { stdout, lines } = runjs('preload-id', project('preload', echoJob({ loadedBefore: true, worstFrameDuringLoadMs: 6 })), ['--ids', '1', '--preload-id', '--timeout', '60'], 0);
    assert.deepStrictEqual([lines[0].tag, lines[0].preloadId, lines[0].status], ['time-1-pre', true, 'ok']);
    assert.match(stdout, /freeze 6 ms \(preloaded\)/);
  });
  test('run_without_preload_id_says_not_preloaded', () => {
    const { stdout, lines } = runjs('no-preload-id', project('nopreload', echoJob({ loadedBefore: false, worstFrameDuringLoadMs: 250 })), ['--ids', '1', '--timeout', '60'], 0);
    assert.deepStrictEqual([lines[0].tag, lines[0].preloadId, lines[0].status], ['time-1', false, 'ok']);
    assert.match(stdout, /freeze 250 ms \(not preloaded\)/);
  });
  test('run_jobs_file_preload_id_not_loaded_fails', () => {
    const jobs = path.join(WORK, 'jobs-preload.json');
    fs.writeFileSync(jobs, JSON.stringify([{ tag: 'pre', id: 1, preloadId: true }]));
    const { lines } = runjs('preload-jobs', project('preload-miss', echoJob({ loadedBefore: false })), ['--jobs', jobs, '--timeout', '60'], 1);
    assert.deepStrictEqual([lines[0].preloadId, lines[0].status], [true, 'fail']);
    assert.match(lines[0].failures.join(), /--preload-id, but the effect was not loaded/);
  });
  test('run_bad_limits_usage_exit_2', () => {
    const proj = project('limits', 'no game here');
    for (const args of [['--max-frames', '0'], ['--capture-seconds', 'x'], ['--timeout', '-5']]) {
      const r = node('run.js', ['--project', proj, '--out', path.join(WORK, 'run', 'limits'), '--port', String(PORT), '--ids', '1', ...args]);
      assert.strictEqual(r.status, 2, `exit ${r.status} for ${args.join(' ')}`);
    }
  });
  test('run_capture_clears_stale_frames', () => {
    // An earlier, longer run of the same tag left f099; run.js removes only its own frame files.
    const out = path.join(WORK, 'run', 'stale');
    fs.mkdirSync(path.join(out, 'capture-1'), { recursive: true });
    for (const f of ['f099.rgba', 'shot-f099.jpg', 'notes.txt']) fs.writeFileSync(path.join(out, 'capture-1', f), 'old');
    const { stdout, lines } = runjs('stale', project('cap', postResult(okCap(0))), ['--ids', '1', '--mode', 'capture', '--timeout', '60'], 0);
    assert.strictEqual(lines[0].clearedStaleFrames, 2);
    assert.deepStrictEqual(fs.readdirSync(path.join(out, 'capture-1')), ['notes.txt']);
    assert.match(stdout, /deleted 2 frame file\(s\) left by an earlier run of this tag/);
  });
}

// ---- live: run.js and compare.js in a real game ------------------------------
if (opts.project && !opts.noChrome) {
  const P = path.resolve(opts.project);
  const ID = '3';
  test('live_clean_run_ok', () => {
    const { lines } = runjs('live-ok', P, ['--ids', ID], 0);
    assert.deepStrictEqual([lines[0].status, lines[0].cold.ended, lines[0].warm.ended], ['ok', true, true]);
  });
  test('live_broken_effect_file_fails', () => {
    const anims = JSON.parse(fs.readFileSync(path.join(P, 'data', 'Animations.json'), 'utf8'));
    const broken = path.join(WORK, 'broken.efkefc');
    fs.writeFileSync(broken, 'SKFE this is not an effect');
    const { lines } = runjs('live-broken', P, ['--ids', ID, '--sub', `${anims[+ID].effectName}.efkefc=${broken}`, '--max-frames', '300'], 1);
    assert.strictEqual(lines[0].status, 'fail');
    assert.match(lines[0].failures.join(), /cold play: effect never finished loading/);
  });
  test('live_engine_exception_fails', () => {
    const { lines } = runjs('live-engine', P, ['--ids', ID, '--warmup', '0', '--max-frames', '300',
      '--prepare', 'Sprite_Animation.prototype.update = function () { throw new Error("injected engine error"); };'], 1);
    assert.match(lines[0].failures.join(), /engine error/);
    assert.match(lines[0].engineErrors.join(), /injected engine error/);
  });
  test('live_page_error_fails', () => {
    const { lines } = runjs('live-page', P, ['--ids', ID, '--prepare', 'throw new Error("injected prepare error");'], 1);
    assert.match(lines[0].failures.join(), /page error: .*injected prepare error/);
  });
  test('live_max_frames_cut_off_fails', () => {
    const { lines } = runjs('live-maxframes', P, ['--ids', ID, '--max-frames', '20'], 1);
    assert.match(lines[0].failures.join(), /cold play: animation did not end within 20 frames/);
  });
  test('live_capture_cut_off_unusable', () => {
    const { lines } = runjs('live-capcut', P, ['--ids', ID, '--mode', 'capture', '--capture-seconds', '0.3'], 1);
    assert.match(lines[0].failures.join(), /capture: animation did not end within 0.3 s/);
    const r = node('compare.js', ['--out', path.join(WORK, 'run', 'live-capcut'), '--base', `capture-${ID}`, `capture-${ID}`]);
    assert.strictEqual(r.status, 1, r.stdout);
    assert.match(r.stdout, /UNUSABLE: job failed: capture: animation did not end/);
  });
  // Both cases of the first-cast freeze: --preload-id must leave the effect
  // loaded before the cold play (the plugin's extraIds), and fail loudly when
  // the project does not run the plugin instead of timing an uncovered cast.
  test('live_preload_id_preloads', () => {
    const { lines } = runjs('live-preload', P, ['--ids', ID, '--preload-id'], 0);
    assert.deepStrictEqual([lines[0].status, lines[0].preloadId, lines[0].cold.loadedBefore], ['ok', true, true]);
    assert.ok(lines[0].preload && lines[0].preload.requested.includes(lines[0].cold.effectName), JSON.stringify(lines[0].preload));
  });
  test('live_preload_id_without_plugin_fails', () => {
    const empty = path.join(WORK, 'no-preload-plugin.js');
    fs.writeFileSync(empty, '// stands in for ReactorEffectPreload.js: no plugin');
    const { lines } = runjs('live-preload-missing', P, ['--ids', ID, '--preload-id', '--override', `/js/plugins/ReactorEffectPreload.js=${empty}`], 1);
    assert.match(lines[0].failures.join(), /page error: .*--preload-id: the project does not run plugins\/ReactorEffectPreload.js/);
  });
  test('live_capture_rerun_over_stale_frames', () => {
    const out = path.join(WORK, 'run', 'live-cap');
    fs.mkdirSync(path.join(out, `capture-${ID}-1`), { recursive: true });
    fs.writeFileSync(path.join(out, `capture-${ID}-1`, 'f998.rgba'), Buffer.alloc(16));
    const { lines } = runjs('live-cap', P, ['--ids', ID, '--mode', 'capture', '--repeat', '2', '--step', '2'], 0);
    assert.deepStrictEqual(lines.map((l) => [l.status, l.clearedStaleFrames || 0]), [['ok', 0], ['ok', 1]]);
    const r = node('compare.js', ['--out', out, '--base', `capture-${ID}-0`, '--noise', `capture-${ID}-1`, `capture-${ID}-1`]);
    assert.strictEqual(r.status, 0, r.stdout);
    const rep = JSON.parse(fs.readFileSync(path.join(out, 'compare.json'), 'utf8'));
    assert.deepStrictEqual([rep.valid, rep.runs[`capture-${ID}-1`].status], [true, 'ok']);
  });
}

let passed = 0, failed = 0;
for (const [name, fn] of cases) {
  try { fn(); console.log('PASS ' + name); passed++; } catch (e) { console.log('FAIL ' + name + ': ' + String(e.message).split('\n').join(' ').slice(0, 600)); failed++; }
}
const summary = `RESULT: ${passed} passed, ${failed} failed` + (opts.noChrome ? ' (run.js cases skipped: --no-chrome)' : '') +
  (opts.project && !opts.noChrome ? ' (includes live game cases)' : ' (no live game cases: pass --project)');
console.log(summary);
fs.writeFileSync(path.join(WORK, 'summary.txt'), summary + '\n');
process.exit(failed ? 1 : 0);
