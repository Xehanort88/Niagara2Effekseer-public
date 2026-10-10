#!/usr/bin/env node
// Build the browsable contact sheet from one or more rendered parts directories.
//
// This is the point of the whole pipeline: you cannot choose a part by reading
// that it is a Ring with a Default material and a three-link chain. You choose
// it by looking at it. And having chosen, you need a spec -- so the page also
// turns a set of picked cards into assembler JSON, because copying part ids into
// a file by hand is the friction the assembler exists to remove.
//
// The page's CSS and script live in sheet.css and sheet-page.js and are inlined
// here, so the output is one self-contained file that can sit anywhere above the
// thumbnails it points at.

const fs = require('fs');
const path = require('path');

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const ROOT = path.resolve(__dirname, '..', '..');

// One or more rendered parts directories, comma separated. The renderers were
// run in two passes because Sprite is two and a half times the rest of the
// library put together, but one sheet over both is what anyone actually wants.
const dirs = arg('parts', path.join(ROOT, 'out', 'parts', 'all'))
  .split(',').map((d) => path.resolve(d.trim()));
const outFile = path.resolve(arg('out', path.join(dirs[0], 'contact-sheet.html')));
const sheetDir = path.dirname(outFile);

// Where a generated spec expects to find things. The catalogue is emitted
// absolute: SourceResolver accepts a rooted path, and a generated spec that
// resolves wherever it is saved beats one that depends on its own depth.
const cataloguePath = path.resolve(arg('catalogue', path.join(ROOT, 'out', 'catalogue.json')));
const specDir = arg('specdir', 'out/parts/specs');
const assembledDir = arg('outdir', 'out/parts/assembled');
const dll = arg('dll', 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll');

const rows = [];
let seconds = 0;

for (const partsDir of dirs) {
  const manifest = JSON.parse(fs.readFileSync(path.join(partsDir, 'manifest.json'), 'utf8'));
  const report = JSON.parse(fs.readFileSync(path.join(partsDir, 'render-report.json'), 'utf8'));
  const byId = new Map(report.results.map((r) => [r.id, r]));
  seconds += report.seconds || 0;

  // Thumbnails stay where they were written; the sheet points at them from
  // wherever it is being written to.
  const prefix = path.relative(sheetDir, partsDir).split(path.sep).join('/');

  for (const p of manifest.parts) {
    const r = byId.get(p.id) || {};
    const peakAt = (r.bestFrame === undefined || r.bestFrame === null) ? -1 : r.bestFrame;
    const budget = Number(p.frames) || 0;
    rows.push({
      id: p.id,
      renderer: p.renderer,
      name: p.name || '',
      source: p.source,
      corpus: p.corpus,
      node: p.node,
      copies: Number(p.copies) || 1,
      chain: Number(p.chain) || 0,
      material: p.material || '',
      lit: r.lit || 0,
      peak: r.peak || 0,
      frame: peakAt,
      frames: budget,
      drew: !!r.rendered,
      // Black first, grey only for parts that showed nothing on it. A grey
      // thumbnail is a subtractive or dark-alpha part, not a rendering quirk.
      background: r.background || 'black',
      thumb: r.thumb ? (prefix ? prefix + '/' + r.thumb : r.thumb) : null,
      problem: r.error || r.loadError || null,
      // A part whose brightest moment sits in the last tenth of its run may
      // have been cut off rather than finished, so say so instead of
      // pretending the thumbnail caught the peak.
      truncated: peakAt > 0 && budget > 0 && peakAt > budget * 0.9,
      // Apparent size, for scaling parts against each other. r90 is the radius
      // around the effect origin that holds 90% of the drawn pixels at the
      // brightest frame, converted to effect units. "clipped" means the part
      // ran off the frame, so the size is a lower bound, not a measurement.
      r50: r.r50 === undefined ? null : r.r50,
      r90: r.r90 === undefined ? null : r.r90,
      bbox: r.bbox || null,
      extent: r.extent || null,
      clipped: !!r.clipped,
      sizeUnits: (r.r90 !== undefined && r.r90 !== null && r.unitsPerPixel)
        ? Math.round(r.r90 * r.unitsPerPixel * 100) / 100 : null,
    });
  }
}

rows.sort((a, b) => (b.drew - a.drew) || (b.copies - a.copies) || a.id.localeCompare(b.id));

const corpora = Array.from(new Set(rows.map((r) => r.corpus))).sort();
const renderers = Array.from(new Set(rows.map((r) => r.renderer))).sort();
const drew = rows.filter((r) => r.drew).length;
const failed = rows.filter((r) => r.problem).length;
const truncated = rows.filter((r) => r.truncated).length;

const opts = (list) => list.map((v) => '<option>' + v + '</option>').join('');

const CONFIG = {
  catalogue: cataloguePath.split(path.sep).join('/'),
  specDir: specDir,
  outDir: assembledDir,
  dll: dll,
};

const css = fs.readFileSync(path.join(__dirname, 'sheet.css'), 'utf8');
const pageScript = fs.readFileSync(path.join(__dirname, 'sheet-page.js'), 'utf8');

const html = [
  '<!doctype html>',
  '<meta charset="utf-8">',
  '<title>Effekseer parts contact sheet</title>',
  '<style>',
  css,
  '</style>',
  '',
  '<header>',
  '  <h1>Effekseer parts contact sheet</h1>',
  '  <div class="stat">' + rows.length + ' distinct parts &middot; ' + drew +
    ' drew something &middot; ' + failed + ' failed &middot; rendered in ' + seconds + 's</div>',
  '  <div class="controls">',
  '    <select id="fr"><option value="">every renderer</option>' + opts(renderers) + '</select>',
  '    <select id="fc"><option value="">every pack</option>' + opts(corpora) + '</select>',
  '    <select id="fd"><option value="drew">drew something</option>' +
    '<option value="">including blanks</option><option value="blank">blanks only</option>' +
    '<option value="picked">picked only</option></select>',
  '    <input id="q" placeholder="search name or source" size="24">',
  '    <select id="so"><option value="copies">most reused first</option>' +
    '<option value="lit">brightest first</option><option value="big">largest first</option>' +
    '<option value="small">smallest first</option><option value="id">by id</option></select>',
  '    <span class="stat" id="count"></span>',
  '    <span class="stat" id="picked">nothing picked</span>',
  '  </div>',
  '</header>',
  '',
  '<div id="grid"></div>',
  '',
  '<aside id="tray" hidden>',
  '  <h2>Picked parts</h2>',
  '  <div class="hint">Click a card to add or remove it. Order is graft order.</div>',
  '  <div id="picklist"></div>',
  '  <div class="controls" style="margin:2px 0 6px">',
  '    <button id="align">align to lead</button>',
  '    <label class="stat"><input type="checkbox" id="align-size" checked> size</label>',
  '    <label class="stat"><input type="checkbox" id="align-time" checked> timing</label>',
  '  </div>',
  '  <div class="hint">Align scales every other part so its measured size matches the lead, ' +
    'and delays it so its brightest frame lands on the brightest frame of the lead. ' +
    'Peaks were sampled every third frame, so timing is good to about three frames.</div>',
  '  <div class="controls" style="margin-bottom:8px">',
  '    <input id="specname" value="MyEffect" size="14">',
  '    <button id="copy">copy spec</button>',
  '    <button id="download" class="primary">save spec</button>',
  '    <button id="clear">clear</button>',
  '  </div>',
  '  <textarea id="spec" spellcheck="false"></textarea>',
  '  <div class="hint">Save the spec into <code>' + specDir + '</code>, then:</div>',
  '  <pre class="cmd" id="cmd"></pre>',
  '  <div class="hint">Leave a field empty to omit it. Offsets are in effect ' +
    'units. Delay is in frames and is added to when the part was authored to start, ' +
    'so a negative value pulls it earlier, never before frame 0.</div>',
  '</aside>',
  '',
  '<script>',
  'var ROWS = ' + JSON.stringify(rows) + ';',
  'var CONFIG = ' + JSON.stringify(CONFIG) + ';',
  pageScript,
  '</' + 'script>',
  '',
].join('\n');

fs.writeFileSync(outFile, html, 'utf8');
console.log('contact sheet: ' + outFile);
console.log('  ' + rows.length + ' parts, ' + drew + ' drew, ' + failed + ' failed, ' +
            truncated + ' peaked at the end of their run');
