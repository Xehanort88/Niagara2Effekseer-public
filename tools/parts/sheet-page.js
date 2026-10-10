// Client script for the parts contact sheet. sheet.js inlines this verbatim
// after defining ROWS (every part) and CONFIG (paths the spec needs).
//
// Three jobs: filter 11,000-odd thumbnails down to something a person can scan,
// turn a set of picked cards into an assembler spec, and make the picked parts
// fit each other. The last is what stops a spec from producing one part that
// fills the frame beside two that cannot be seen: across the library a large
// part is about four times the size of a small one, and peaks land anywhere
// in a part's run.

(function () {
  'use strict';

  var STORE = 'efk-parts-picks';
  var PAGE = 1200;             // cards rendered at once; filters narrow further
  var SCALE_MIN = 0.1, SCALE_MAX = 10;

  var byId = {};
  ROWS.forEach(function (r) { byId[r.id] = r; });

  var grid = document.getElementById('grid');
  var tray = document.getElementById('tray');
  var els = ['fr', 'fc', 'fd', 'q', 'so'].map(function (i) { return document.getElementById(i); });

  // Picks are [{id, name, offset:[x,y,z], scale, delay, lifeScale, lead}] in the
  // order they were chosen, because parts are grafted in spec order.
  var picks = load();

  // Warnings from the last align, per part id. Not persisted: they describe
  // what align did, and go stale the moment a value is edited by hand.
  var notes = {};

  function load() {
    try {
      var raw = localStorage.getItem(STORE);
      if (!raw) return [];
      return JSON.parse(raw).filter(function (p) { return byId[p.id]; });
    } catch (e) { return []; }
  }

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(picks)); } catch (e) {}
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function has(v) { return v !== null && v !== undefined; }
  function round2(x) { return Math.round(x * 100) / 100; }

  function indexOfPick(id) {
    for (var i = 0; i < picks.length; i++) if (picks[i].id === id) return i;
    return -1;
  }

  function leadPick() {
    for (var i = 0; i < picks.length; i++) if (picks[i].lead) return picks[i];
    return picks[0] || null;
  }

  // ------------------------------------------------------------------ grid

  function card(r) {
    var n = indexOfPick(r.id);
    var img = r.thumb
      ? '<img loading="lazy" src="' + r.thumb + '">'
      : '<div class="none">' + (r.problem ? 'failed' : 'nothing drawn') + '</div>';
    var tags = '<span class="tag">' + r.renderer + '</span>'
      + '<span class="tag">x' + r.copies + '</span>'
      + (r.chain ? '<span class="tag">chain ' + r.chain + '</span>' : '')
      + (has(r.sizeUnits)
        ? '<span class="tag" title="radius around the origin holding 90% of the drawn pixels at peak, in effect units'
          + (r.clipped ? '; ran off the frame, so this is a lower bound' : '') + '">r '
          + r.sizeUnits + (r.clipped ? '+' : '') + '</span>'
        : '')
      + (r.background === 'grey' ? '<span class="tag">grey bg</span>' : '')
      + (r.truncated ? '<span class="warn">peak at end</span>' : '');

    return '<div class="card' + (r.drew ? '' : ' blank') + (n >= 0 ? ' picked' : '')
      + '" data-id="' + r.id + '">'
      + (n >= 0 ? '<span class="order">' + (n + 1) + '</span>' : '')
      + img
      + '<div class="meta">'
      + '<div class="nm" title="' + esc(r.name) + '">' + (esc(r.name) || '(unnamed)') + '</div>'
      + '<div class="sub" title="' + esc(r.source) + ' node ' + esc(r.node) + '">' + esc(r.source) + '</div>'
      + '<div class="sub">' + tags + '</div>'
      + '<div class="sub"><code>' + r.id + '</code></div>'
      + '</div></div>';
  }

  function filtered() {
    var fr = els[0].value, fc = els[1].value, fd = els[2].value;
    var needle = els[3].value.trim().toLowerCase(), so = els[4].value;

    var list = ROWS.filter(function (r) {
      if (fr && r.renderer !== fr) return false;
      if (fc && r.corpus !== fc) return false;
      if (fd === 'drew' && !r.drew) return false;
      if (fd === 'blank' && r.drew) return false;
      if (fd === 'picked' && indexOfPick(r.id) < 0) return false;
      if (needle && (r.name + ' ' + r.source + ' ' + r.id).toLowerCase().indexOf(needle) < 0) return false;
      return true;
    });

    if (so === 'lit') list = list.slice().sort(function (a, b) { return b.lit - a.lit; });
    else if (so === 'id') list = list.slice().sort(function (a, b) { return a.id.localeCompare(b.id); });
    else if (so === 'big' || so === 'small') {
      // Parts with no size -- blank or failed -- sink to the bottom either way.
      var dir = so === 'big' ? -1 : 1;
      list = list.slice().sort(function (a, b) {
        var na = !has(a.sizeUnits), nb = !has(b.sizeUnits);
        if (na || nb) return na === nb ? 0 : (na ? 1 : -1);
        return dir * (a.sizeUnits - b.sizeUnits);
      });
    }
    return list;
  }

  function draw() {
    var list = filtered();
    document.getElementById('count').textContent = list.length + ' shown';
    grid.innerHTML = list.slice(0, PAGE).map(card).join('');
    if (list.length > PAGE) {
      grid.insertAdjacentHTML('beforeend',
        '<div class="none" style="grid-column:1/-1;aspect-ratio:auto;padding:14px">'
        + (list.length - PAGE) + ' more hidden - narrow the filters</div>');
    }
  }

  grid.addEventListener('click', function (ev) {
    var el = ev.target.closest('.card');
    if (!el) return;
    toggle(el.getAttribute('data-id'));
  });

  function toggle(id) {
    var i = indexOfPick(id);
    if (i >= 0) picks.splice(i, 1);
    else picks.push({ id: id, name: '', offset: [0, 0, 0], scale: '', delay: '', lifeScale: '', lead: false });
    delete notes[id];
    save();
    draw();
    drawTray();
  }

  // ------------------------------------------------------------------ align

  function num(v) {
    if (v === '' || v === null || v === undefined) return null;
    var f = parseFloat(v);
    return isFinite(f) ? f : null;
  }

  // Scale each non-lead part so its measured size matches the lead's, and delay
  // it so its brightest frame lands on the lead's brightest frame.
  //
  // Both are exact consequences of how the numbers were measured, which is why
  // they can be computed rather than tuned. Size is the radius around the
  // origin, and a spec's scale acts about the origin, so size scales linearly
  // with it (measured: 0.52, 1.0 and 2.0 times at scale 0.5, 1 and 2). Peak
  // frame is measured with the part at its authored start, and a spec's delay
  // is added to that start, so the peak moves by exactly the delay.
  //
  // The lead's own scale and delay are respected, so a lead set to twice its
  // size pulls every other part up to match.
  function align(doSize, doTiming) {
    var lead = leadPick();
    if (!lead) return;
    var L = byId[lead.id];
    var leadScale = num(lead.scale); if (leadScale === null) leadScale = 1;
    var leadDelay = num(lead.delay); if (leadDelay === null) leadDelay = 0;

    notes = {};
    var timed = [];            // picks whose delay this align set
    picks.forEach(function (p) {
      if (p === lead) return;
      var r = byId[p.id];
      var msgs = [];

      if (doSize) {
        if (!has(L.sizeUnits)) {
          msgs.push('the lead has no measured size, so scale was left alone');
        } else if (!has(r.sizeUnits) || r.sizeUnits <= 0) {
          msgs.push('no measured size, so scale was left alone');
        } else {
          var s = L.sizeUnits * leadScale / r.sizeUnits;
          var c = Math.min(SCALE_MAX, Math.max(SCALE_MIN, s));
          if (c !== s) msgs.push('wanted scale ' + round2(s) + ', clamped to ' + round2(c));
          p.scale = String(round2(c));
          // A clipped size is a lower bound. Say which way the error goes.
          if (r.clipped) msgs.push('ran off the frame when measured, so it is really larger and this scale is too big');
          if (L.clipped) msgs.push('the lead ran off the frame when measured, so this scale is too small');
        }
      }

      if (doTiming) {
        if (!has(L.frame) || L.frame < 0 || !has(r.frame) || r.frame < 0) {
          msgs.push('no measured peak, so delay was left alone');
        } else {
          p.delay = String(Math.round(L.frame + leadDelay - r.frame));
          timed.push(p);
          if (r.truncated) msgs.push('its peak was at the end of its render, so the real peak may be later');
          if (L.truncated) msgs.push('the lead peaked at the end of its render, so its real peak may be later');
        }
      }

      notes[p.id] = msgs;
    });

    // A part that peaks later than the lead would need a negative delay, and
    // the assembler can only pull a part earlier as far as its outermost
    // carrier's authored start -- often frame 0, which would clamp the delay
    // away and leave the peaks apart. Push everything later instead, the lead
    // included, so every delay is zero or more and the peaks still coincide.
    if (doTiming && timed.length) {
      var lowest = leadDelay;
      timed.forEach(function (p) { lowest = Math.min(lowest, num(p.delay)); });
      if (lowest < 0) {
        var shift = -lowest;
        timed.forEach(function (p) { p.delay = String(num(p.delay) + shift); });
        lead.delay = String(leadDelay + shift);
        notes[lead.id] = ['delayed by ' + shift + ' frames so that no part needs a negative delay'];
      }
    }

    save();
    drawTray();
  }

  // ------------------------------------------------------------------ tray

  function spec() {
    var parts = picks.map(function (p) {
      var r = byId[p.id];
      var out = { source: r.source, node: r.node, ancestors: 'controllers' };
      out.name = p.name || r.name || r.id;

      var o = [num(p.offset[0]) || 0, num(p.offset[1]) || 0, num(p.offset[2]) || 0];
      if (o[0] || o[1] || o[2]) out.offset = o;

      var s = num(p.scale); if (s !== null && s !== 1) out.scale = s;
      var d = num(p.delay); if (d !== null && d !== 0) out.delay = Math.round(d);
      var l = num(p.lifeScale); if (l !== null && l !== 1) out.lifeScale = l;
      return out;
    });
    return { catalogue: CONFIG.catalogue, parts: parts };
  }

  function measured(r) {
    var size = has(r.sizeUnits) ? 'size r ' + r.sizeUnits + (r.clipped ? '+' : '') : 'no size';
    var peak = has(r.frame) && r.frame >= 0 ? 'peak frame ' + r.frame + (r.truncated ? ' (at end)' : '') : 'no peak';
    return size + ' &middot; ' + peak;
  }

  function drawTray() {
    if (!picks.length) {
      tray.hidden = true;
      document.body.classList.remove('tray-open');
      document.getElementById('picked').textContent = 'nothing picked';
      return;
    }
    tray.hidden = false;
    document.body.classList.add('tray-open');
    document.getElementById('picked').textContent = picks.length + ' picked';

    var lead = leadPick();
    var html = picks.map(function (p, i) {
      var r = byId[p.id];
      var isLead = p === lead;
      var msgs = (notes[p.id] || []).map(function (m) { return '<div class="note">' + esc(m) + '</div>'; }).join('');
      return '<div class="pick' + (isLead ? ' is-lead' : '') + '" data-id="' + p.id + '">'
        + '<div class="head"><span class="idx">' + (i + 1) + '</span>'
        + '<span class="nm" title="' + esc(r.source) + ' node ' + esc(r.node) + '">'
        + (esc(r.name) || r.id) + '</span>'
        + '<label class="lead"><input type="radio" name="lead" data-lead="' + p.id + '"'
        + (isLead ? ' checked' : '') + '> lead</label>'
        + '<button class="rm" data-rm="' + p.id + '">remove</button></div>'
        + '<div class="sub">' + esc(r.source) + ' &middot; node ' + esc(r.node) + '</div>'
        + '<div class="measure">' + measured(r) + '</div>'
        + msgs
        + '<div class="fields">'
        + '<label>name</label><input class="wide" data-f="name" value="' + esc(p.name) + '">'
        + '<label>x</label><input data-f="ox" value="' + esc(p.offset[0]) + '">'
        + '<label>y</label><input data-f="oy" value="' + esc(p.offset[1]) + '">'
        + '<label>z</label><input data-f="oz" value="' + esc(p.offset[2]) + '">'
        + '<label>scale</label><input data-f="scale" placeholder="1" value="' + esc(p.scale) + '">'
        + '<label>delay</label><input data-f="delay" placeholder="0" value="' + esc(p.delay) + '">'
        + '<label>life x</label><input data-f="lifeScale" placeholder="1" value="' + esc(p.lifeScale) + '">'
        + '</div></div>';
    }).join('');

    document.getElementById('picklist').innerHTML = html;
    document.getElementById('spec').value = JSON.stringify(spec(), null, 1);

    var name = document.getElementById('specname').value.trim() || 'MyEffect';
    document.getElementById('cmd').textContent =
      'dotnet ' + CONFIG.dll + ' assemble ' + CONFIG.specDir + '/' + name + '.json \\\n'
      + '  ' + CONFIG.outDir + '/' + name + '/' + name + '.efkefc';
  }

  tray.addEventListener('click', function (ev) {
    var rm = ev.target.getAttribute && ev.target.getAttribute('data-rm');
    if (rm) toggle(rm);
  });

  tray.addEventListener('change', function (ev) {
    var id = ev.target.getAttribute && ev.target.getAttribute('data-lead');
    if (!id) return;
    picks.forEach(function (p) { p.lead = p.id === id; });
    notes = {};
    save();
    drawTray();
  });

  tray.addEventListener('input', function (ev) {
    var f = ev.target.getAttribute && ev.target.getAttribute('data-f');
    if (!f) return;
    var holder = ev.target.closest('.pick');
    if (!holder) return;
    var p = picks[indexOfPick(holder.getAttribute('data-id'))];
    if (!p) return;

    if (f === 'ox') p.offset[0] = ev.target.value;
    else if (f === 'oy') p.offset[1] = ev.target.value;
    else if (f === 'oz') p.offset[2] = ev.target.value;
    else p[f] = ev.target.value;

    save();
    document.getElementById('spec').value = JSON.stringify(spec(), null, 1);
  });

  document.getElementById('specname').addEventListener('input', drawTray);

  document.getElementById('align').addEventListener('click', function () {
    align(document.getElementById('align-size').checked,
          document.getElementById('align-time').checked);
  });

  document.getElementById('copy').addEventListener('click', function () {
    var ta = document.getElementById('spec');
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    if (!ok && navigator.clipboard) navigator.clipboard.writeText(ta.value);
    this.textContent = 'copied';
    var self = this;
    setTimeout(function () { self.textContent = 'copy spec'; }, 1200);
  });

  document.getElementById('download').addEventListener('click', function () {
    var name = (document.getElementById('specname').value.trim() || 'MyEffect') + '.json';
    var blob = new Blob([document.getElementById('spec').value], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
  });

  document.getElementById('clear').addEventListener('click', function () {
    picks = [];
    notes = {};
    save();
    draw();
    drawTray();
  });

  els.forEach(function (e) { e.addEventListener('input', draw); });
  draw();
  drawTray();
})();
