"""Unity serialized objects as one graph, from either of the two forms a Unity game's assets reach us in.

- Text YAML (`.prefab`, `.mat`, `.asset`, as AssetRipper exports a project or a Unity editor saves it): one document
  per object, headed `--- !u!<classID> &<fileID>`. A reference is `{fileID: N}` inside the same file, or
  `{fileID: N, guid: G, type: T}` into another asset, found through the `.meta` files of `--assets` folders.
- A typetree JSON dump written by `unity_dump.py` (UnityPy, straight from the game's bundles): every object under
  an id `<serialized file>:<path id>`, every PPtr already rewritten to `{"$ref": id}` (or `{"$ref": null,
  "unresolved": ...}` when its file was not loaded).

Field names are Unity's own serialized names in both forms (`InitialModule`, `startLifetime`, `m_Materials`), so
everything above this module reads one set of names. Values are normalised only where the two forms differ in
spelling: YAML writes bools as 0/1 and floats as `Infinity`; UnityPy writes maps as [key, value] pairs.
"""
import json
import math
import re
from pathlib import Path

# YAML class ids that matter here; others keep the class name their document states.
CLASS_NAMES = {1: 'GameObject', 4: 'Transform', 21: 'Material', 28: 'Texture2D', 33: 'MeshFilter', 43: 'Mesh',
               48: 'Shader', 114: 'MonoBehaviour', 115: 'MonoScript', 198: 'ParticleSystem',
               199: 'ParticleSystemRenderer', 224: 'RectTransform', 1001: 'PrefabInstance'}
# Unity's built-in resource files: references into them name an engine asset, not a file of the project.
BUILTIN_GUIDS = {'0000000000000000e000000000000000': 'builtin default resources',
                 '0000000000000000f000000000000000': 'builtin extra resources'}
_HEADER = re.compile(r'^--- !u!(\d+) &(-?\d+)( stripped)?[^\n]*$', re.M)
_GUID = re.compile(r'^guid:\s*([0-9a-f]{32})\s*$', re.M)
_SPECIAL = {'Infinity': math.inf, '-Infinity': -math.inf, 'NaN': math.nan, 'inf': math.inf, '-inf': -math.inf}


class UnityError(Exception):
    pass


def num(v, default=0.0):
    """A serialized number: YAML may spell infinities and NaN as words; None and '' read as `default`."""
    if v is None or v == '':
        return default
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    return _SPECIAL[s] if s in _SPECIAL else float(s)


def flag(v):
    """A serialized bool: YAML writes 0/1, UnityPy True/False."""
    return bool(int(v)) if isinstance(v, (int, float, bool)) else str(v).strip() not in ('', '0', 'false', 'False')


def pairs(v):
    """A serialized map as [(key, value)]: YAML writes a list of one-key dicts, UnityPy a list of [key, value]."""
    out = []
    for item in v or []:
        if isinstance(item, dict) and len(item) == 1:
            out.extend(item.items())
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            out.append((item[0], item[1]))
        elif isinstance(item, dict) and 'first' in item and 'second' in item:
            out.append((item['first'], item['second']))
    return out


def vec(v, keys='xyz'):
    return [num((v or {}).get(k)) for k in keys]


def is_ref(v):
    return isinstance(v, dict) and ('fileID' in v or '$ref' in v or 'm_PathID' in v)


class UObj:
    __slots__ = ('id', 'cls', 'class_id', 'data', 'doc', 'stripped')

    def __init__(self, oid, cls, class_id, data, doc, stripped=False):
        self.id, self.cls, self.class_id, self.data, self.doc, self.stripped = oid, cls, class_id, data, doc, stripped

    @property
    def name(self):
        n = self.data.get('m_Name')     # str(): YAML reads an unquoted name such as `1e5` or `true` as a number/bool
        return '' if n is None else str(n)

    def __repr__(self):
        return '<%s %s %r>' % (self.cls, self.id, self.name)


class AssetIndex:
    """guid -> asset path from the `.meta` files under some folders (an AssetRipper export), and the YAML documents
    of those assets, loaded once on first use."""

    def __init__(self, roots=()):
        self.paths, self._docs, self.roots = {}, {}, [Path(r) for r in roots]
        for root in self.roots:
            for meta in root.rglob('*.meta'):
                m = _GUID.search(meta.read_text(encoding='utf-8', errors='replace')[:4096])
                if m:
                    self.paths.setdefault(m.group(1), meta.with_suffix(''))

    def path(self, guid):
        return self.paths.get(guid)

    def doc(self, guid):
        """The asset's objects (YAML assets only), or None for a binary/unknown asset."""
        if guid not in self._docs:
            p = self.paths.get(guid)
            self._docs[guid] = None
            if p is not None and p.is_file():
                head = p.open('rb').read(64)
                if head.startswith(b'%YAML'):
                    self._docs[guid] = UnityDoc.from_yaml(p, self)
        return self._docs[guid]


class UnityDoc:
    """The objects of one source (a YAML asset or a JSON dump) with reference resolution."""

    def __init__(self, source, kind, index=None, unity_version=None):
        self.source, self.kind, self.index, self.unity_version = str(source), kind, index, unity_version
        self.objects, self.order, self.root = {}, [], None

    # --- loading -----------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path, index=None):
        p = Path(path)
        head = p.open('rb').read(64).lstrip(b'\xef\xbb\xbf \t\r\n')
        if head.startswith(b'%YAML') or head.startswith(b'---'):
            return cls.from_yaml(p, index)
        if head.startswith(b'{'):
            return cls.from_json(p, index)
        raise UnityError('%s: neither Unity YAML nor a unity_dump.py JSON file' % p)

    @classmethod
    def from_yaml(cls, path, index=None):
        try:
            import yaml
        except ImportError:
            raise UnityError('reading Unity YAML needs PyYAML (pip install pyyaml)')
        loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
        text = Path(path).read_text(encoding='utf-8', errors='replace')
        doc = cls(path, 'yaml', index)
        m = re.search(r'^m_EditorVersion:\s*(\S+)', text, re.M)   # ProjectVersion-style hint, rarely present
        doc.unity_version = m.group(1) if m else None
        heads = list(_HEADER.finditer(text))
        if not heads:
            raise UnityError('%s: no `--- !u!` documents' % path)
        for i, h in enumerate(heads):
            body = text[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            try:
                parsed = yaml.load(body, Loader=loader) or {}
            except yaml.YAMLError as ex:
                raise UnityError('%s: object &%s does not parse: %s' % (path, h.group(2), ex))
            if not isinstance(parsed, dict) or len(parsed) != 1:
                raise UnityError('%s: object &%s is not one `ClassName:` mapping' % (path, h.group(2)))
            (name, data), = parsed.items()
            class_id = int(h.group(1))
            doc._add(UObj(h.group(2), name or CLASS_NAMES.get(class_id, 'Class%d' % class_id), class_id,
                          data if isinstance(data, dict) else {}, doc, bool(h.group(3))))
        return doc

    @classmethod
    def from_json(cls, path, index=None):
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        if not str(d.get('format', '')).startswith('n2e-unity-dump/'):
            raise UnityError('%s: not a unity_dump.py file (no "format": "n2e-unity-dump/...")' % path)
        doc = cls(path, 'json', index, d.get('unity_version'))
        for o in d.get('objects', []):
            doc._add(UObj(str(o['id']), o['class'], o.get('class_id'), o.get('data') or {}, doc))
        doc.root = d.get('root')
        return doc

    def _add(self, o):
        self.objects[o.id] = o
        self.order.append(o.id)

    # --- queries -----------------------------------------------------------------------------------------------
    def of_class(self, cls):
        return [self.objects[i] for i in self.order if self.objects[i].cls == cls]

    def deref(self, v):
        """The object a reference names, or None. A reference into another asset resolves through the asset index
        (YAML) and returns that asset's object; a null reference, an unloaded file or an unknown guid give None."""
        if not is_ref(v):
            return None
        if '$ref' in v:
            return self.objects.get(v['$ref']) if v['$ref'] is not None else None
        if 'm_PathID' in v:          # a raw UnityPy PPtr that the dumper did not rewrite (same file only)
            return self.objects.get(str(v['m_PathID'])) if int(v.get('m_FileID') or 0) == 0 else None
        fid, guid = str(v.get('fileID', 0)), v.get('guid')
        if fid == '0':
            return None
        if not guid:
            return self.objects.get(fid)
        if self.index is None:
            return None
        other = self.index.doc(guid)
        return other.objects.get(fid) if other is not None else None

    def describe_ref(self, v):
        """A short readable name for a reference, resolved or not: `Name (Class)`, an asset path, or why not."""
        if not is_ref(v):
            return None
        o = self.deref(v)
        if o is not None:
            n = shader_name(o) if o.cls == 'Shader' else o.name
            if not n and 'm_GameObject' in o.data:     # a component is named by its GameObject
                go = self.deref(o.data['m_GameObject'])
                n = go.name if go is not None else None
            return '%s (%s)' % (n or o.id, o.cls)
        if '$ref' in v:
            return None if v['$ref'] is None and not v.get('unresolved') else 'unresolved %s' % v.get('unresolved')
        if 'm_PathID' in v:
            return None if int(v['m_PathID']) == 0 else 'external file %s path %s' % (v.get('m_FileID'), v['m_PathID'])
        if str(v.get('fileID', 0)) == '0':
            return None
        guid = v.get('guid')
        if not guid:
            return 'missing local object &%s' % v.get('fileID')
        if guid in BUILTIN_GUIDS:
            return '%s #%s' % (BUILTIN_GUIDS[guid], v.get('fileID'))
        p = self.index.path(guid) if self.index else None
        if p is not None:
            if p.suffix == '.shader':
                n = shader_file_name(p)
                return '%s (Shader file %s)' % (n, p.name) if n else str(p)
            return str(p)
        return 'guid %s #%s (not in --assets)' % (guid, v.get('fileID'))


def shader_name(o):
    """A Shader object's name: `m_ParsedForm.m_Name` since Unity 5.5, else `m_Name`."""
    pf = o.data.get('m_ParsedForm')
    return (pf.get('m_Name') if isinstance(pf, dict) else None) or o.data.get('m_Name') or ''


def shader_file_name(path):
    """The name in a `.shader` source's first `Shader "Name"` line, or None."""
    try:
        m = re.search(r'^\s*Shader\s+"([^"]+)"', Path(path).read_text(encoding='utf-8', errors='replace')[:8192], re.M)
    except OSError:
        return None
    return m.group(1) if m else None


# --- the GameObject hierarchy -----------------------------------------------------------------------------------

def components(go):
    """A GameObject's component objects in order (both serialized spellings of m_Component)."""
    out = []
    for c in go.data.get('m_Component') or []:
        ref = c.get('component') if isinstance(c, dict) and 'component' in c else (
            c[1] if isinstance(c, (list, tuple)) and len(c) == 2 else c)
        o = go.doc.deref(ref)
        if o is not None:
            out.append(o)
    return out


def transform_of(go):
    for c in components(go):
        if c.cls in ('Transform', 'RectTransform'):
            return c
    return None


def gameobject_of(comp):
    return comp.doc.deref(comp.data.get('m_GameObject'))


def children(go):
    t = transform_of(go)
    out = []
    for ref in (t.data.get('m_Children') or []) if t is not None else []:
        ct = go.doc.deref(ref)
        cgo = gameobject_of(ct) if ct is not None else None
        if cgo is not None:
            out.append(cgo)
    return out


def roots(doc):
    """GameObjects whose Transform has no parent, in document order (the dump's root first when it names one)."""
    out = []
    for go in doc.of_class('GameObject'):
        t = transform_of(go)
        if t is not None and doc.deref(t.data.get('m_Father')) is None:
            out.append(go)
    if doc.root is not None:
        out.sort(key=lambda g: g.id != str(doc.root))
    return out


def walk(go, path=''):
    """(path, GameObject, depth) for go and every descendant, depth first. A cycle is cut, never followed."""
    seen = set()

    def rec(g, p, d):
        if g.id in seen:
            return
        seen.add(g.id)
        p = p + '/' + g.name if p else g.name
        yield p, g, d
        for c in children(g):
            yield from rec(c, p, d + 1)
    yield from rec(go, path, 0)


def transform_summary(t):
    if t is None:
        return None
    q = [num(t.data.get('m_LocalRotation', {}).get(k), 1.0 if k == 'w' else 0.0) for k in 'xyzw']
    return {'position': vec(t.data.get('m_LocalPosition')), 'rotation_xyzw': q, 'euler_deg': quat_to_unity_euler(q),
            'scale': [num(t.data.get('m_LocalScale', {}).get(k), 1.0) for k in 'xyz']}


def quat_to_unity_euler(q):
    """Unity's inspector Euler angles (degrees, applied Z, then X, then Y) of a unit quaternion."""
    x, y, z, w = q
    sx = 2 * (w * x - y * z)
    sx = max(-1.0, min(1.0, sx))
    ex = math.asin(sx)
    if abs(sx) < 0.999999:
        ey = math.atan2(2 * (w * y + x * z), 1 - 2 * (x * x + y * y))
        ez = math.atan2(2 * (w * z + x * y), 1 - 2 * (x * x + z * z))
    else:                                    # gimbal lock: put all yaw into Y
        ey = math.atan2(-2 * (x * z - w * y), 1 - 2 * (y * y + z * z))
        ez = 0.0
    return [round(math.degrees(a), 4) for a in (ex, ey, ez)]


def script_name(mb):
    """The class a MonoBehaviour runs: from its MonoScript (dump), or the script file its guid names (YAML)."""
    ref = mb.data.get('m_Script')
    o = mb.doc.deref(ref)
    if o is not None:
        ns = o.data.get('m_Namespace') or ''
        cn = o.data.get('m_ClassName') or o.name
        return (ns + '.' if ns else '') + cn
    d = mb.doc.describe_ref(ref)
    if d and mb.doc.index is not None and isinstance(ref, dict) and ref.get('guid'):
        p = mb.doc.index.path(ref['guid'])
        if p is not None:
            return p.stem
    return d or '?'


# --- materials --------------------------------------------------------------------------------------------------

# UnityEngine.Rendering.BlendMode
BLEND_FACTORS = ['Zero', 'One', 'DstColor', 'SrcColor', 'OneMinusDstColor', 'SrcAlpha', 'OneMinusSrcColor',
                 'DstAlpha', 'OneMinusDstAlpha', 'SrcAlphaSaturate', 'OneMinusSrcAlpha']
BLEND_KINDS = {('One', 'One'): 'additive', ('SrcAlpha', 'One'): 'additive (alpha-weighted)',
               ('SrcAlpha', 'OneMinusSrcAlpha'): 'alpha blend', ('One', 'OneMinusSrcAlpha'): 'premultiplied alpha',
               ('DstColor', 'Zero'): 'multiply', ('Zero', 'SrcColor'): 'multiply',
               ('DstColor', 'SrcColor'): 'multiply x2', ('OneMinusDstColor', 'One'): 'soft additive (screen)',
               ('One', 'Zero'): 'opaque'}


def material_summary(m):
    """A Material's shader, textures (with tiling), floats, colours and keywords as stored, plus the blend its
    `_SrcBlend`/`_DstBlend` floats state when the shader exposes them (nothing is inferred from names)."""
    d, doc = m.data, m.doc
    sp = d.get('m_SavedProperties') or {}
    tex = {}
    for k, v in pairs(sp.get('m_TexEnvs')):
        v = v or {}
        entry = {'texture': doc.describe_ref(v.get('m_Texture'))}
        sc, of = vec(v.get('m_Scale'), 'xy'), vec(v.get('m_Offset'), 'xy')
        if sc != [1.0, 1.0] or of != [0.0, 0.0]:
            entry.update(scale=sc, offset=of)
        if entry['texture'] or 'scale' in entry:
            tex[k] = entry
    floats = {k: num(v) for k, v in pairs(sp.get('m_Floats'))}
    ints = {k: num(v) for k, v in pairs(sp.get('m_Ints'))}
    colors = {k: vec(v, 'rgba') for k, v in pairs(sp.get('m_Colors'))}
    kw = d.get('m_ValidKeywords')
    if kw is None:
        kw = (d.get('m_ShaderKeywords') or '').split()
    out = {'name': m.name, 'shader': doc.describe_ref(d.get('m_Shader')), 'textures': tex, 'floats': floats,
           'colors': colors, 'keywords': list(kw)}
    if ints:
        out['ints'] = ints
    if d.get('m_CustomRenderQueue') not in (None, -1):
        out['render_queue'] = int(num(d.get('m_CustomRenderQueue')))
    tags = dict(pairs(d.get('stringTagMap')))
    if tags:
        out['tags'] = tags
    blend = {}
    for key in ('_SrcBlend', '_DstBlend', '_SrcBlendAlpha', '_DstBlendAlpha'):
        v = floats.get(key, ints.get(key))
        if v is not None and 0 <= int(v) < len(BLEND_FACTORS) and int(v) == v:
            blend[key] = BLEND_FACTORS[int(v)]
    if '_SrcBlend' in blend and '_DstBlend' in blend:
        blend['kind'] = BLEND_KINDS.get((blend['_SrcBlend'], blend['_DstBlend']), 'other')
    if blend:
        out['blend'] = blend
    return out
