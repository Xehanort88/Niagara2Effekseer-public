// Loader/control-flow regression tests without a browser. Integration pixels are checked by test_hdr_overlay.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../plugins/ReactorHdrOverlay.js'), 'utf8');

function environment(metadata) {
  const loads = [], fetches = [], warnings = [], draws = [];
  function Animation() {}
  Animation.prototype.setup = function (_targets, animation) {
    this._animation = animation; this._effectContext = null; this._playing = true;
    this._effect = context.EffectManager.load(animation.effectName);
  };
  Animation.prototype.canStart = () => true;
  Animation.prototype._doEffekseerDraw = function () { draws.push(this._animation.effectName); };
  Animation.renderActive = function () {};
  const context = {
    Sprite_Animation: Animation, Graphics: {}, PluginManager: {parameters: () => ({})},
    EffectManager: {makeUrl: n => 'effects/' + n + '.efkefc', load: n => {loads.push(n); return n;}},
    fetch: async url => {fetches.push(url); return metadata === null ? {status:404} : {status:200, ok:true, json:async () => metadata};},
    console: {warn: (...args) => warnings.push(args.join(' ')), info: () => {}},
  };
  context.window = context;
  vm.runInNewContext(source, context);
  function sprite(name, label = 'Ordinary', id = 1) {
    const s = new Animation(); s.setup([], {name:label, effectName:name, id}); return s;
  }
  async function ready(name) {await context.ReactorHdrOverlay.cache.get(name).promise;}
  return {context, sprite, ready, loads, fetches, warnings, draws};
}

(async () => {
  let e = environment(null);
  const a = e.sprite('Plain'), b = e.sprite('Plain');
  assert.equal(a.canStart(), false, 'pending metadata gates playback');
  await e.ready('Plain');
  assert.equal(a.canStart(), true); assert.equal(b.canStart(), true);
  a._doEffekseerDraw(); b._doEffekseerDraw();
  assert.deepEqual(e.draws, ['Plain', 'Plain']); assert.equal(e.fetches.length, 1);

  const side = {version:1, gradeInput:'hdr', grade:'vec3 tmOut = hdr;', fallback:'Twin',
    bloom:{intensity:0.675,radiusScale:2.4,sizes:[0.3,1,2,10,30,64],tints:[0.3465,0.138,0.1176,0.066,0.066,0.061]}};
  e = environment(side);
  const fallback = e.sprite('Nested/Effect-hdr'); await e.ready('Nested/Effect-hdr');
  assert.equal(fallback.canStart(), true); assert.equal(fallback._effect, 'Nested/Twin');
  assert.equal(fallback._hdrActive, false, 'an unavailable context selects the safe twin');

  e = environment(null);
  const forced = e.sprite('Unsafe-hdr', '[HDR]'); await e.ready('Unsafe-hdr');
  assert.equal(forced.canStart(), false); assert.equal(forced._playing, false);
  assert.equal(e.warnings.length, 1, 'missing override metadata fails closed');

  e = environment({...side, bloom:{...side.bloom, radiusScale:-1}});
  const invalid = e.sprite('Invalid'); await e.ready('Invalid');
  assert.equal(invalid.canStart(), false); assert.equal(invalid._playing, false);
  console.log('HDR loader/control flow: 4 cases passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
