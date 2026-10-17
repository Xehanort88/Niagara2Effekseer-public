#!/usr/bin/env python3
"""Black-box edit/dump regressions. Standard library; generated fixtures only.

All build outputs, NuGet/CLI caches, temporary files and transcripts live in
--work. Each invocation gets a fresh run directory, so rerunning is safe.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
LOCATION = "LocationValues/LocationFCurve/FCurve"
CURVE = LOCATION + "/Y"
SCALE = "ScalingValues/SingleFCurve"
TIME_CURVE = SCALE + "/S"
KNOWN = {
    "top": "retime, source, name, allowMissingResources, endFrame, edits, procedural",
    "retime": "factor",
    "edit": "node, expect, note, set, tint, add, shift, keys",
    "tint": "hue, saturation, subtractiveOpposite",
    "add": "from, name, file",
    "shift": "curve, from, by, percentOfLife",
    "keys": "curve, points",
    "procedural": "index, add, from, expect, note, set",
}


def require(condition, message):
    # Explicit checks still run under python -O.
    if not condition:
        raise AssertionError(message)


def contains(text, fragment):
    require(fragment in text, f"missing {fragment!r}")


def failure(label, error):
    # Keep the promised one-line status even when a command has multiline output.
    print(f"FAIL {label}: {' '.join(str(error).split())}", flush=True)


class Commands:
    def __init__(self, work, run):
        self.work = work
        self.env = dict(os.environ)
        for name, folder in {
            "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
            "DOTNET_CLI_HOME": "cli-home", "NUGET_PACKAGES": "packages",
            "NUGET_HTTP_CACHE_PATH": "http-cache",
            "NUGET_PLUGINS_CACHE_PATH": "plugins-cache",
        }.items():
            path = work / folder
            path.mkdir(parents=True, exist_ok=True)
            self.env[name] = str(path)
        self.env.update(DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
                        DOTNET_CLI_TELEMETRY_OPTOUT="1", PYTHONDONTWRITEBYTECODE="1")
        self.log = (run / "commands.jsonl").open("w", encoding="utf-8")

    def run(self, args, code=0):
        args = [str(arg) for arg in args]
        result = subprocess.run(args, cwd=self.work, env=self.env,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=300)
        self.log.write(json.dumps({"args": args, "exit": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr}) + "\n")
        self.log.flush()
        require(result.returncode == code,
                f"expected exit {code}, got {result.returncode}: "
                f"{result.stdout[-2000:]} {result.stderr[-2000:]}")
        require("Unhandled exception" not in result.stdout + result.stderr,
                "unhandled exception output")
        return result

    def build(self, label, project):
        self.run(["dotnet", "build", ROOT / "src" / project / (project + ".csproj"),
                  "-c", "Release", "-p:Platform=x64", "--artifacts-path",
                  self.work / "build", "-p:NuGetAudit=false", "-v", "quiet"])
        dll = self.work / "build" / "bin" / project / "release" / "Niagara2Efk.dll"
        require(dll.is_file(), f"build DLL missing: {dll}")
        print(f"PASS {label} build", flush=True)
        return dll


def png(path, value=0xff):
    """A generated opaque 2x2 RGBA PNG, no image package required. `value` sets RGB, so two calls can differ."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    pixel = bytes([value, value, value, 0xff])
    path.write_bytes(b"\x89PNG\r\n\x1a\n" +
                    chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)) +
                    chunk(b"IDAT", zlib.compress((b"\0" + pixel * 2) * 2)) +
                    chunk(b"IEND", b""))


def saved_curve(path, group, axis):
    # Use the repo's independent EDIT decoder without producing __pycache__.
    decoder = runpy.run_path(str(ROOT / "efkefc_dump.py"))
    tree = decoder["decode_edit_chunk"](decoder["parse_chunks"](path.read_bytes())["blocks"]["EDIT"])
    def descend(items, parts):
        if not parts:
            return items
        found = [item for item in items if item[0] == parts[0]]
        require(len(found) == 1, f"EDIT path missing/ambiguous: {parts}")
        return descend(found[0][2] or [], parts[1:])
    node = descend(tree, ["EffekseerProject", "Root", "Children", "Node"])
    # EDIT's curve wrapper spells FCurve as Keys internally.
    curve_group = descend(node, group.split("/"))
    keys = descend(curve_group, ["Keys", axis])
    return [{name: float(value) for name, value, _ in children}
            for tag, _, children in keys if tag.startswith("Key")]


def parameter_material(path):
    """Generated material declarations, independent of the port writer (stdlib)."""
    def ints(*v):
        return struct.pack('<' + 'i' * len(v), *v)
    def string(v):
        b = v.encode() + b'\0'
        return ints(len(b)) + b
    def chunk(tag, b):
        return tag + ints(len(b)) + b
    prm = ints(1, 0, 0, 0, 0, 0, 1)
    prm += string('Display texture') + string('nodeTex') + string('') + ints(0, 0, 1, 1, 0)
    prm += ints(4)
    for i in range(4):
        prm += string('Display %d' % i) + string('nodeValue%d' % (i + 1)) + ints(i, i, i)
        prm += struct.pack('<4f', 0, 0, 0, 0)
    prm += ints(0, 0)
    code = '$F3$ emissive = nodeValue4.xyz;\n'
    path.write_bytes(b'EFKM' + ints(1710) + bytes(8) + chunk(b'PRM_', prm) + chunk(b'GENE', string(code)))


class Suite:
    def __init__(self, commands, label, dll, directory):
        self.commands, self.label, self.dll, self.directory = commands, label, dll, directory
        directory.mkdir()
        self.counter = 0
        self.base = directory / "base.efkefc"
        self.tool("roundtrip", self.base, 60, 2)
        self.sources = {self.base: self.base.read_bytes()}
        self.percent = self.edit({"source": str(self.base), "edits": [{
            "node": "0", "set": {"LocationValues/Type": "LocationFCurve",
                                   CURVE + "/Sampling": 7},
            "keys": [{"curve": CURVE, "points": [[0, 1], [20, 4], [90, 2]]}]}]})
        self.sources[self.percent] = self.percent.read_bytes()

    def tool(self, *args, code=0):
        return self.commands.run(["dotnet", self.dll, *args], code)

    def edit(self, spec, code=0, output=None):
        self.counter += 1
        path = self.directory / f"spec-{self.counter:03}.edit.json"
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        output = output or self.directory / f"edited-{self.counter:03}.efkefc"
        result = self.tool("edit", path, output, code=code)
        if code == 0:
            contains(result.stdout, "VERIFY OK")
            require(not result.stderr.strip(), f"unexpected stderr: {result.stderr}")
            require(output.is_file(), "successful edit wrote no effect")
            return output
        require(not output.exists(), "refused edit wrote an output effect")
        return result

    def spec(self, op=None, source=None):
        return {"source": str(source or self.base), "edits": [op] if op else []}

    def dump(self, path):
        result = self.tool("dump", path, "0")
        require(not result.stderr.strip(), f"dump stderr: {result.stderr}")
        return result.stdout

    def material_values(self, bad=None):
        folder = self.directory / 'material-fixture'
        folder.mkdir(exist_ok=True)
        material = folder / 'fixture.efkmat'
        parameter_material(material)
        texture = folder / 'node.png'
        png(texture, 0x71)
        prefix = 'RendererCommonValues/MaterialFile/'
        settings = {'RendererCommonValues/Material': 'File', prefix + 'Path': str(material),
                    prefix + 'Uniforms/nodeValue1': 0.125, prefix + 'Uniforms/nodeValue2': [2, -3],
                    prefix + 'Uniforms/nodeValue3': [4, 5, 6], prefix + 'Uniforms/nodeValue4': [7, 8, 9, 0],
                    prefix + 'Textures/nodeTex': str(texture)}
        if bad == 'name':
            settings[prefix + 'Uniforms/Display 1'] = 1
        elif bad == 'width':
            settings[prefix + 'Uniforms/nodeValue4'] = [1, 2, 3]
        elif bad == 'kind':
            settings[prefix + 'Textures/nodeValue1'] = str(texture)
        elif bad == 'missing_texture':
            settings[prefix + 'Textures/nodeTex'] = str(folder / 'absent.png')
        elif bad == 'nonfinite':
            settings[prefix + 'Uniforms/nodeValue4'] = [1e39, 0, 0, 0]
        elif bad == 'not_file':
            settings['RendererCommonValues/Material'] = 'Default'
        spec = self.spec({'node': '0', 'set': settings})
        if bad:
            result = self.edit(spec, code=1)
            contains(result.stderr, {'name': 'unknown or ambiguous', 'kind': 'unknown or ambiguous',
                                    'width': 'cannot set a Vector4D', 'missing_texture': 'no such file',
                                    'nonfinite': 'expected finite floats', 'not_file': 'require Material File'}[bad])
            return
        spec['edits'] += [{'node': 'root', 'add': {'from': '0', 'name': 'Other'}, 'set': {
            prefix + 'Uniforms/nodeValue4': [0.2, 0.3, 0.4, 0.5]}},
            {'node': '0', 'set': {prefix + 'Uniforms/nodeValue1': 0.375}}]
        out = self.edit(spec)
        dump = self.dump(out)
        for name, value in [('nodeValue1', '0.375'), ('nodeValue2', '(2, -3)'),
                            ('nodeValue3', '(4, 5, 6)'), ('nodeValue4', '(7, 8, 9, 0)')]:
            self.line(dump, prefix + 'Uniforms/' + name, value)
        self.line(dump, prefix + 'Textures/nodeTex', 'node.png')
        other = self.tool('dump', out, '1').stdout
        self.line(other, prefix + 'Uniforms/nodeValue4', '(0.2, 0.3, 0.4, 0.5)')
        require((out.parent / 'Texture/node.png').read_bytes() == texture.read_bytes(), 'material texture bytes differ')
        # Reloading and recopying must preserve node values, not restore defaults.
        again = self.edit(self.spec(source=out))
        self.line(self.dump(again), prefix + 'Uniforms/nodeValue4', '(7, 8, 9, 0)')

    def line(self, dump, key, value):
        require(f"{key} = {value}" in dump.splitlines(), f"missing dump line: {key} = {value}")

    def unknown(self, level):
        spec = self.spec({"node": "0"})
        op = spec["edits"][0]
        if level == "top":
            part, where = spec, "at the top level"
        elif level == "retime":
            spec["retime"] = {"factor": 2}
            part, where = spec["retime"], "in retime"
        elif level == "procedural":
            spec["procedural"] = [{"index": 0}]
            part, where = spec["procedural"][0], "in procedural[0]"
        elif level == "edit":
            part, where = op, 'in edits[0] (node "0")'
        else:
            defaults = {"tint": {"hue": 0}, "add": {"from": "0"},
                        "shift": [{"curve": CURVE, "by": 1}],
                        "keys": [{"curve": CURVE, "points": [[0, 1]]}]}
            op[level] = defaults[level]
            part = op[level][0] if isinstance(op[level], list) else op[level]
            where = 'in edits[0] (node "0") ' + level + ("[0]" if level in ("keys", "shift") else "")
        part["factr" if level == "retime" else "typo"] = 2
        result = self.edit(spec, code=2)
        contains(result.stderr, f'unknown key "{"factr" if level == "retime" else "typo"}" {where}')
        contains(result.stderr, f"(known: {KNOWN[level]})")

    def comments(self):
        out = self.edit({"//top": "comment", "source": str(self.percent),
                         "retime": {"factor": 1, "//retime": True}, "edits": [
            {"node": "0", "//op": 1, "tint": {"hue": 0, "//tint": 1},
             "set": {"LocationValues/Type": "LocationFCurve", LOCATION + "/Timeline": "Time"},
             "keys": [{"curve": LOCATION + "/X", "points": [[0, 1], [20, 2]], "//keys": 1}]},
            {"node": "0", "shift": [{"curve": CURVE, "from": 20, "by": 1, "//shift": 1}]},
            {"node": "root", "add": {"from": "0", "name": "Copy", "//add": 1}}]})
        contains(self.tool("dump", out).stdout, "### 1 Copy")

    def procedural_comment(self):
        # The generated fixture has no procedural models. Demonstrate comment
        # validation passes, then the expected missing-model guard runs.
        result = self.edit({"source": str(self.base), "edits": [],
                            "procedural": [{"index": 0, "//comment": 1}]}, code=1)
        contains(result.stderr, "procedural: no model at index 0")
        require("unknown key" not in result.stderr, "procedural comment rejected")

    TUBE = {"Shape/PrimitiveType": "Spline4", "Shape/Point2": [0.5, 0.3],
            "Mesh/Divisions/X": 24, "VertexColor/ColorLowerCenter": [255, 200, 80, 255]}
    DRAW_MODEL = {"DrawingValues/Type": "Model", "DrawingValues/Model/ModelReference": "ProceduralModel"}

    def procedural_add(self):
        # A model created from the defaults, then a copy of it with one change;
        # a node references the first by name and its spawn shape the second by index.
        out = self.edit({"source": str(self.base), "procedural": [
            {"add": "Tube", "set": self.TUBE},
            {"add": "Wide", "from": 0, "expect": "Tube", "set": {"Shape/Point2": [0.9, 0.3]}}],
            "edits": [{"node": "0", "set": dict(self.DRAW_MODEL, **{
                "DrawingValues/Model/Reference": "Tube",
                "GenerationLocationValues/Type": "Model",
                "GenerationLocationValues/Model/ModelReference": "ProceduralModel",
                "GenerationLocationValues/Model/Reference": 1})}]})
        dump = self.dump(out)
        self.line(dump, "DrawingValues/Model/Reference", '#0 "Tube"')
        self.line(dump, "GenerationLocationValues/Model/Reference", '#1 "Wide"')
        lines = dump.splitlines()
        tube, wide = lines.index("### procedural 0 Tube"), lines.index("### procedural 1 Wide")
        for key, value in [("Shape/PrimitiveType", "Spline4"), ("Shape/Point2", "(0.5, 0.3)"),
                           ("Mesh/Divisions/X", "24"), ("VertexColor/ColorLowerCenter", "rgba(255, 200, 80, 255)")]:
            require(f"{key} = {value}" in lines[tube:wide], f"model 0 missing {key} = {value}")
            if key != "Shape/Point2":
                require(f"{key} = {value}" in lines[wide:], f"copied model 1 missing {key} = {value}")
        require("Shape/Point2 = (0.9, 0.3)" in lines[wide:], "copied model 1 lost its own change")
        return out

    def procedural_refused(self, case):
        spec = {"source": str(self.base), "edits": []}
        if case == "add_and_index":
            spec["procedural"], message = [{"add": "A", "index": 0}], "not both"
        elif case == "from_without_add":
            spec["procedural"], message = [{"index": 0, "from": 0}], "`from` only applies with `add`"
        elif case == "expect_without_from":
            spec["procedural"], message = [{"add": "A", "expect": "B"}], "`expect` guards the `from` model"
        elif case == "duplicate_name":
            spec["procedural"], message = [{"add": "A"}, {"add": "A"}], "a model named 'A' already exists at index 0"
        elif case == "unknown_name":
            spec["procedural"] = [{"add": "A"}]
            spec["edits"] = [{"node": "0", "set": dict(self.DRAW_MODEL, **{"DrawingValues/Model/Reference": "B"})}]
            message = "no procedural model named 'B' (the project has: 0 'A')"
        else:  # ambiguous_name: a rename can still make two models share a name
            spec["procedural"] = [{"add": "A"}, {"add": "B"}, {"index": 1, "set": {"Name": "A"}}]
            spec["edits"] = [{"node": "0", "set": dict(self.DRAW_MODEL, **{"DrawingValues/Model/Reference": "A"})}]
            message = "2 procedural models are named 'A' (indices 0, 1); reference one by index"
        contains(self.edit(spec, code=1).stderr, message)

    def procedural_warnings(self):
        # A reference left switched off, and a model nothing references: both
        # save fine but draw nothing, so the edit says so on stdout.
        self.counter += 1
        spec = self.directory / f"spec-{self.counter:03}.edit.json"
        spec.write_text(json.dumps({"source": str(self.base), "procedural": [{"add": "A"}, {"add": "Unused"}],
                                    "edits": [{"node": "0", "set": {"DrawingValues/Model/Reference": "A"}}]}), encoding="utf-8")
        result = self.tool("edit", spec, self.directory / "warned.efkefc")
        contains(result.stdout, "VERIFY OK")
        contains(result.stdout, "WARNING: procedural 1 'Unused' was added but no node references it")
        contains(result.stdout, "DrawingValues/Model/ModelReference is File and DrawingValues/Type is Sprite")

    def procedural_carry(self):
        # Copying a node out of another effect carries the procedural model it
        # draws: appended here, reference repointed, a second copy shares it.
        donor = self.procedural_add()
        target = self.edit({"source": str(self.base), "procedural": [{"add": "Local"}],
                            "edits": [{"node": "0", "set": dict(self.DRAW_MODEL, **{"DrawingValues/Model/Reference": "Local"})}]})
        self.counter += 1
        spec = self.directory / f"spec-{self.counter:03}.edit.json"
        spec.write_text(json.dumps({"source": str(target), "edits": [
            {"node": "root", "add": {"file": str(donor), "from": "0", "name": "Carried"}},
            {"node": "root", "add": {"file": str(donor), "from": "0", "name": "Again"}}]}), encoding="utf-8")
        out = self.directory / "carried.efkefc"
        result = self.tool("edit", spec, out)
        contains(result.stdout, "carried 2 procedural model reference(s) from " + donor.name + " as new model(s) 1..2")
        contains(result.stdout, "(models already carried by an earlier add)")
        contains(result.stdout, "3 procedural model(s) (2 added) and 5 project reference(s) identical after saving")
        dump = self.tool("dump", out).stdout
        lines = dump.splitlines()
        # Carried models are appended in the order the walk meets them; read
        # the indices back by name rather than assuming that order.
        index = {l.split()[3]: l.split()[2] for l in lines if l.startswith("### procedural ")}
        require(sorted(index) == ["Local", "Tube", "Wide"] and index["Local"] == "0", f"model list {index}")
        for node, draw, spawn in [("0", "Local", None), ("1", "Tube", "Wide"), ("2", "Tube", "Wide")]:
            start = next(i for i, l in enumerate(lines) if l.startswith(f"### {node} "))
            block = lines[start:next(i for i, l in enumerate(lines) if i > start and l.startswith("### "))]
            require(f'DrawingValues/Model/Reference = #{index[draw]} "{draw}"' in block, f"node {node} draws {draw}")
            want = f'#{index[spawn]} "{spawn}"' if spawn else "(none)"
            require(f"GenerationLocationValues/Model/Reference = {want}" in block, f"node {node} spawns on {want}")
        # A node-path dump lists only the models that node uses.
        only = self.tool("dump", out, "0").stdout
        require("### procedural 0 Local" in only and "### procedural 1" not in only, "node dump listed unused models")

    def blank_base(self):
        # `blank` is the neutral base to author from: one still, upright, unscaled
        # particle with no gravity, so nothing an edit leaves unset moves on its own.
        out = self.directory / "blank.efkefc"
        contains(self.tool("blank", out, 45).stdout, "BLANK OK")
        dump = self.dump(out)
        for key, value in [("CommonValues/Life", "45"), ("CommonValues/MaxGeneration", "1"), ("LocationValues/Type", "Fixed"),
                           ("LocationValues/PVA/Velocity/Y", "0"), ("LocationValues/PVA/Acceleration/Y", "0"),
                           ("RotationValues/Type", "Fixed"), ("ScalingValues/Type", "Fixed"), ("ScalingValues/Fixed/Scale", "(1, 1, 1)")]:
            self.line(dump, key, value)
        return out

    def blank_fresh_copy(self):
        # A copy of an edited node inherits its edits (a floor tilt laid a sigil card
        # flat); copying node 0 out of the blank FILE gives a fresh neutral node.
        blank = self.blank_base()
        out = self.edit({"source": str(blank), "edits": [
            {"node": "0", "set": {"RotationValues/Fixed/Rotation": [65, 0, 0]}},
            {"node": "root", "add": {"from": "0", "name": "Inherits"}},
            {"node": "root", "add": {"file": str(blank), "from": "0", "name": "Fresh"}}]})
        lines = self.tool("dump", out).stdout.splitlines()
        def block(path):
            start = next(i for i, l in enumerate(lines) if l.startswith(f"### {path} "))
            end = next((i for i, l in enumerate(lines) if i > start and l.startswith("### ")), len(lines))
            return lines[start:end]
        require("RotationValues/Fixed/Rotation = (65, 0, 0)" in block("1"), "in-project copy did not inherit the tilt")
        require("RotationValues/Fixed/Rotation = (0, 0, 0)" in block("2"), "copy from the blank file is not neutral")

    def renamed_texture(self):
        # The resource copier never overwrites: rebuilding into the same folder after
        # regenerating a texture under the same name saves it hash-suffixed, and the
        # read-back now says why instead of only "asked X, file holds X_ABCDEF".
        folder = self.directory / "renamed"
        texture = self.directory / "regenerated" / "glow.png"
        png(texture, 0xff)
        spec = self.spec({"node": "0", "set": {"RendererCommonValues/ColorTexture": str(texture)}})
        self.edit(spec, output=folder / "first.efkefc")
        png(texture, 0x40)
        # Read-back runs after the save, so this VERIFY FAIL leaves the file written (unlike a refusal).
        self.counter += 1
        path = self.directory / f"spec-{self.counter:03}.edit.json"
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        result = self.tool("edit", path, folder / "second.efkefc", code=1)
        contains(result.stderr, "a different glow.png was already beside the output, so this one was saved as glow_")
        contains(result.stderr, "delete it and rebuild")

    def spline_points_warning(self):
        # A Spline4 built from the defaults with a point left unset is almost always a
        # mistake (a lost Point4 folded a crystal into a bowl): the edit says so.
        self.counter += 1
        spec = self.directory / f"spec-{self.counter:03}.edit.json"
        spec.write_text(json.dumps({"source": str(self.base), "procedural": [
            {"add": "Partial", "set": {"Shape/PrimitiveType": "Spline4", "Shape/Point1": [0, 0], "Shape/Point2": [1.4, 2]}},
            {"add": "Full", "set": {"Shape/PrimitiveType": "Spline4", "Shape/Point1": [0, 0], "Shape/Point2": [1.4, 2],
                                     "Shape/Point3": [1.4, 3], "Shape/Point4": [0, 5]}}], "edits": []}), encoding="utf-8")
        result = self.tool("edit", spec, self.directory / "spline-warning.efkefc")
        contains(result.stdout, "WARNING: procedural 0 'Partial' is a Spline4 built from the defaults but leaves Shape/Point3, Shape/Point4 at the default ((1, 1), (1, 2))")
        require("'Full' is a Spline4" not in result.stdout, "fully specified Spline4 warned")

    def log_beside_spec(self):
        # The edit log goes beside the spec, so building straight into a game
        # project's effects folder leaves nothing there but the effect and textures.
        folder = self.directory / "clean-output"
        self.edit(self.spec({"node": "0", "set": {"CommonValues/Life": 30}}), output=folder / "clean.efkefc")
        require(not (folder / "niagara2efk-edit.log").exists(), "edit log written beside the output")
        require((self.directory / "niagara2efk-edit.log").exists(), "edit log not beside the spec")

    def whole(self, field, value):
        if value == 60.0:
            out = self.edit(self.spec({"node": "0", "set": {field: value}}, self.percent))
            self.line(self.dump(out), field, "60")
        else:
            result = self.edit(self.spec({"node": "0", "set": {field: value}}), code=1)
            contains(result.stderr, "expected a whole number, got 2.5")

    def same_folder(self):
        texture = self.directory / "Texture" / "generated.png"
        png(texture)
        original = texture.read_bytes()
        source = self.directory / "sprite.efkefc"
        self.tool("sprite", source, texture)
        before = source.read_bytes()
        out = self.edit(self.spec({"node": "0", "set": {"CommonValues/Life": 60}}, source))
        require(texture.read_bytes() == original, "same-folder texture changed")
        require(source.read_bytes() == before, "source sprite changed")
        dump = self.dump(out)
        contains(dump, "generated.png")
        self.line(dump, "CommonValues/Life", "60")

    def keys(self, timeline="Percent", points=None, sampling=None, code=0):
        settings = {"LocationValues/Type": "LocationFCurve", LOCATION + "/Timeline": timeline}
        if sampling is not None:
            settings[CURVE + "/Sampling"] = sampling
        points = points or [[0, 1], [20, 4], [90, 2]]
        result = self.edit(self.spec({"node": "0", "set": settings,
                                     "keys": [{"curve": CURVE, "points": points}]}), code=code)
        if code:
            contains(result.stderr, "keys outside 0..100 never play")
        else:
            dump = self.dump(result)
            self.line(dump, CURVE + "/Sampling", str(sampling if sampling is not None else 1))
            self.line(dump, LOCATION + "/Timeline", timeline)
            self.line(dump, CURVE, "fcurve[" + " ".join(f"{x}:{y}" for x, y in points) + "]")

    def colour_keys(self, points, code=0):
        """`keys` on an int curve (a colour channel, 0..255): whole values written and read back, fractions refused."""
        base = "DrawingValues/ColorAll/FCurve/FCurve"
        result = self.edit(self.spec({"node": "0", "set": {"DrawingValues/ColorAll/Type": "FCurve", base + "/Timeline": "Percent"},
                                      "keys": [{"curve": base + "/A", "points": points}]}), code=code)
        if code:
            contains(result.stderr, "whole values only")
        else:
            dump = self.dump(result)
            contains(dump, base + "/A = fcurve[0:0 30:200 100:0]")
            self.line(dump, base + "/A/Sampling", "1")

    def infinite_count(self, value, code=0):
        """MaxGeneration "inf" (an endless emitter, as dump prints it) written and read back; a finite count after
        it turns the flag off again; other text refused."""
        result = self.edit(self.spec({"node": "0", "set": {"CommonValues/MaxGeneration": value}}), code=code)
        if not code:
            self.line(self.dump(result), "CommonValues/MaxGeneration", "inf" if value == "inf" else str(value))

    def dump_metadata(self):
        out = self.edit(self.spec({"node": "0", "set": {
            CURVE + "/OffsetMin": 2, CURVE + "/OffsetMax": 5}}, self.percent))
        dump = self.dump(out)
        self.line(dump, LOCATION + "/Timeline", "Percent")
        self.line(dump, CURVE + "/Sampling", "7")
        self.line(dump, CURVE + "/OffsetMin", "2")
        self.line(dump, CURVE + "/OffsetMax", "5")

    def shift(self, life=60, by=5, timeline=None, percent=False, code=0):
        settings = {"CommonValues/Life": life}
        if timeline:
            settings[LOCATION + "/Timeline"] = timeline
        out = self.edit(self.spec({"node": "0", "set": settings, "shift": [
            {"curve": CURVE, "from": 20 if by > 0 else 0, "by": by,
             "percentOfLife": percent}]}, self.percent), code=code)
        if code:
            contains(out.stderr, "Timeline Percent")
            contains(out.stderr, "but `by` moves them in frames")
        else:
            dump = self.dump(out)
            self.line(dump, CURVE + "/Sampling", "1")
            self.line(dump, LOCATION + "/Timeline", timeline or "Percent")
            points = [[0, 1], [20, 4], [90, 2]]
            for point in points:
                if point[0] >= (20 if by > 0 else 0):
                    point[0] += by
            self.line(dump, CURVE, "fcurve[" + " ".join(f"{x}:{y}" for x, y in points) + "]")

    def layered(self, order):
        # Several ops on one curve: only the final saved curve is verified
        # (keys then shift used to report VERIFY FAILED on a correct file).
        time = {"LocationValues/Type": "LocationFCurve", LOCATION + "/Timeline": "Time"}
        keys = {"curve": CURVE, "points": [[0, 1], [20, 4], [90, 2]]}
        if order == "keys_shift":
            ops = [{"node": "0", "set": time, "keys": [keys]},
                   {"node": "0", "shift": [{"curve": CURVE, "from": 20, "by": 5}]}]
            expected = "fcurve[0:1 25:4 95:2]"
        elif order == "shift_keys":
            ops = [{"node": "0", "set": time, "shift": [{"curve": CURVE, "from": 20, "by": 5}]},
                   {"node": "0", "keys": [{"curve": CURVE, "points": [[0, 2], [30, 3]]}]}]
            expected = "fcurve[0:2 30:3]"
        else:
            ops = [{"node": "0", "set": time, "keys": [keys]},
                   {"node": "0", "shift": [{"curve": CURVE, "from": 20, "by": 5}]},
                   {"node": "0", "shift": [{"curve": CURVE, "from": 90, "by": 3}]}]
            expected = "fcurve[0:1 25:4 98:2]"
        # shift then keys needs keys to shift first: the percent fixture has them.
        source = self.percent if order == "shift_keys" else self.base
        out = self.edit({"source": str(source), "edits": ops})
        dump = self.dump(out)
        self.line(dump, CURVE, expected)
        self.line(dump, CURVE + "/Sampling", "1")

    def retime(self):
        source = self.edit(self.spec({"node": "0", "set": {
            "CommonValues/GenerationTimeOffset": 5,
            "ScalingValues/Type": "SingleFCurve", SCALE + "/Timeline": "Time",
            TIME_CURVE + "/OffsetMin": 2, TIME_CURVE + "/OffsetMax": 4,
            TIME_CURVE + "/Sampling": 6},
            "keys": [{"curve": TIME_CURVE, "points": [[0, 1], [10, 3], [20, 2]]}]}, self.percent))
        before = self.dump(source)
        time_keys = saved_curve(source, SCALE, "S")
        percent_keys = saved_curve(source, LOCATION, "Y")
        require(len(time_keys) == 3, "no independently decoded Time keys")
        out = self.edit({"source": str(source), "retime": {"factor": 2}, "edits": []})
        dump = self.dump(out)
        self.line(dump, "CommonValues/Life", "120")
        offset = "CommonValues/" + ("Generation/" if self.label == "1.8" else "") + "GenerationTimeOffset"
        self.line(dump, offset, "10")
        self.line(dump, TIME_CURVE, "fcurve[0:1 20:3 40:2]")
        self.line(dump, SCALE + "/Timeline", "Time")
        self.line(dump, TIME_CURVE + "/Sampling", "1")
        # Value offsets, not time: retime leaves them as they were.
        self.line(dump, TIME_CURVE + "/OffsetMin", "2")
        self.line(dump, TIME_CURVE + "/OffsetMax", "4")
        for line in before.splitlines():
            if line.startswith(LOCATION + "/"):
                contains(dump, line)
        require(saved_curve(out, LOCATION, "Y") == percent_keys, "Percent keys/handles changed")
        after_keys = saved_curve(out, SCALE, "S")
        require(len(after_keys) == len(time_keys), "retime changed key count")
        for old, new in zip(time_keys, after_keys):
            require(old.keys() == new.keys(), "retime changed key schema")
            for key, value in old.items():
                require(new[key] == value * (2 if key in ("Frame", "LeftX", "RightX") else 1),
                        f"retime key {key}: {value} -> {new[key]}")

    def rounding(self):
        source = self.edit(self.spec({"node": "0", "set": {"CommonValues/Life": 61}}))
        self.counter += 1
        spec = self.directory / f"spec-{self.counter:03}.edit.json"
        spec.write_text(json.dumps({"source": str(source), "retime": {"factor": 1.5}, "edits": []}), encoding="utf-8")
        out = self.directory / "rounded.efkefc"
        result = self.tool("edit", spec, out)
        contains(result.stdout, "RETIME ROUND:")
        contains(result.stdout, "91.5 -> 92")
        contains(result.stdout, "VERIFY OK")
        self.line(self.dump(out), "CommonValues/Life", "92")

    def lint(self, points, sampling, code, frame=None, intended=None, played=None,
             runtime=None, off_grid=None, short_tail=None):
        out = self.edit(self.spec({"node": "0", "set": {
            "LocationValues/Type": "LocationFCurve", LOCATION + "/Timeline": "Time",
            CURVE + "/Sampling": sampling}, "keys": [{"curve": CURVE, "points": points}]}))
        report = out.with_suffix(".lint.json")
        result = self.tool("lint", out, "--json", report, *(["--runtime", runtime] if runtime else []), code=code)
        data = json.loads(report.read_text(encoding="utf-8"))
        expected_runtime = runtime or "1.80.7"
        require(data["Runtime"] == expected_runtime, f"report runtime {data['Runtime']}, expected {expected_runtime}")
        contains(result.stdout, f"(runtime {expected_runtime})")
        curves = [c for c in data["Effects"][0]["Curves"] if c["CurvePath"] == CURVE]
        if off_grid is not None:
            require(curves[0]["OffGridKeys"] == off_grid, f"off-grid keys {curves[0]['OffGridKeys']}, expected {off_grid}")
        if short_tail is not None:
            require(curves[0]["ShortTail"] == short_tail, f"short tail {curves[0]['ShortTail']}, expected {short_tail}")
        if frame is None:
            return
        worst = curves[0]["LargestDifference"]
        require(worst["Frame"] == frame, f"largest difference at {worst['Frame']}, expected {frame}")
        require(abs(worst["Intended"] - intended) < 1e-3 and abs(worst["Played"] - played) < 1e-3,
                f"intended/played {worst['Intended']}/{worst['Played']}, expected {intended}/{played}")

    def lint_unknown_runtime(self):
        result = self.tool("lint", self.base, "--runtime", "1.80.5", code=2)
        contains(result.stderr, "unknown runtime 1.80.5")

    def lint_incomplete(self, findings, strict, code):
        """A folder with one readable effect and one unreadable file: unreadable
        effects are listed and non-fatal by default, exit 3 under --strict."""
        self.counter += 1
        folder = self.directory / f"lint-incomplete-{self.counter:03}"
        folder.mkdir()
        points = [[0, 8], [9, 17], [42, 17], [50, 0]]
        good = self.edit(self.spec({"node": "0", "set": {
            "LocationValues/Type": "LocationFCurve", LOCATION + "/Timeline": "Time",
            CURVE + "/Sampling": 10 if findings else 1}, "keys": [{"curve": CURVE, "points": points}]}),
            output=folder / "readable.efkefc")
        (folder / "broken.efkefc").write_bytes(b"SKFE not an effect")
        report = self.directory / f"lint-incomplete-{self.counter:03}.json"
        result = self.tool("lint", folder, "--json", report, *(["--strict"] if strict else []), code=code)
        contains(result.stdout, "UNREADABLE effect: " + str(folder / "broken.efkefc"))
        contains(result.stdout, "LINT INCOMPLETE: 1 of 2 target effects unreadable")
        contains(result.stdout, "--strict, exit 3" if strict else "use --strict to fail on it")
        data = json.loads(report.read_text(encoding="utf-8"))
        require((data["TargetEffects"], data["UnreadableEffects"], data["Complete"], data["Strict"]) == (2, 1, False, strict),
                f"report completeness fields: {data['TargetEffects']}, {data['UnreadableEffects']}, {data['Complete']}, {data['Strict']}")
        require(data["OursFindings"] == (1 if findings else 0), f"ours findings {data['OursFindings']}")
        require(good.is_file(), "readable fixture missing")

    def lint_empty(self, strict, code):
        self.counter += 1
        folder = self.directory / f"lint-empty-{self.counter:03}"
        folder.mkdir()
        result = self.tool("lint", folder, *(["--strict"] if strict else []), code=code)
        contains(result.stdout, "LINT INCOMPLETE: no .efkefc effect found")

    def lint_strict_complete(self):
        out = self.edit(self.spec({"node": "0", "set": {"CommonValues/Life": 30}}))
        report = out.with_suffix(".strict.json")
        result = self.tool("lint", out, "--strict", "--json", report)
        require("INCOMPLETE" not in result.stdout, "complete lint reported incomplete")
        require(json.loads(report.read_text(encoding="utf-8"))["Complete"] is True, "Complete not true")

    def lint_incomplete_corpus(self):
        # A comparison corpus with unreadable files (a sandbox that cannot see the corpus
        # drive) is said at the end, not only on the Corpus line; the exit status is unchanged.
        self.counter += 1
        folder = self.directory / f"lint-corpus-{self.counter:03}"
        folder.mkdir()
        catalogue = folder / "catalogue.json"
        catalogue.write_text(json.dumps({"effects": [{"path": str(self.base)}, {"path": str(folder / "missing.efkefc")}]}), encoding="utf-8")
        result = self.tool("lint", self.base, "--strict", "--catalogue", catalogue)
        contains(result.stdout, "LINT NOTE: comparison corpus incomplete (1 of 2 files unreadable)")

    def unchanged(self):
        for source, data in self.sources.items():
            require(source.read_bytes() == data, f"fixture source modified: {source}")

    def cases(self):
        for level in KNOWN:
            yield "unknown_" + level, lambda level=level: self.unknown(level)
        yield "comments_nested", self.comments
        yield "comment_procedural_validation", self.procedural_comment
        yield "procedural_add_copy_and_reference", self.procedural_add
        for case in ("add_and_index", "from_without_add", "expect_without_from",
                     "duplicate_name", "unknown_name", "ambiguous_name"):
            yield "procedural_refused_" + case, lambda case=case: self.procedural_refused(case)
        yield "procedural_unused_and_switched_off_warn", self.procedural_warnings
        yield "procedural_carried_by_foreign_add", self.procedural_carry
        yield "blank_base_is_neutral", self.blank_base
        yield "add_from_blank_file_is_fresh", self.blank_fresh_copy
        yield "renamed_texture_explained", self.renamed_texture
        yield "spline4_unset_points_warned", self.spline_points_warning
        yield "edit_log_beside_spec", self.log_beside_spec
        for name, field in [("random_int", "CommonValues/Life"),
                            ("int_infinite", "CommonValues/MaxGeneration"),
                            ("int", CURVE + "/Sampling"), ("enum", "DrawingValues/Type")]:
            for suffix, value in [("whole_float", 60.0), ("fraction_refused", 2.5)]:
                # Use a valid enum number while still testing whole-float parsing.
                if name == "enum" and value == 60.0:
                    continue
                yield name + "_" + suffix, lambda field=field, value=value: self.whole(field, value)
        yield "same_folder_resources", self.same_folder
        yield "dump_curve_metadata", self.dump_metadata
        yield "keys_sampling_default", self.keys
        yield "keys_sampling_override", lambda: self.keys(sampling=4)
        yield "keys_percent_above_100_refused", lambda: self.keys(points=[[0, 1], [101, 2]], code=1)
        yield "keys_percent_below_0_refused", lambda: self.keys(points=[[-1, 1], [90, 2]], code=1)
        yield "keys_time_same_op", lambda: self.keys(timeline="Time", points=[[-1, 1], [120, 2]])
        yield "keys_colour_int_curve", lambda: self.colour_keys([[0, 0], [30, 200], [100, 0]])
        yield "keys_colour_fraction_refused", lambda: self.colour_keys([[0, 0.5], [100, 0]], code=1)
        yield "max_generation_infinite", lambda: self.infinite_count("inf")
        yield "max_generation_finite", lambda: self.infinite_count(12)
        yield "max_generation_text_refused", lambda: self.infinite_count("forever", code=1)
        yield "shift_percent_life_refused", lambda: self.shift(code=1)
        yield "shift_percent_above_100_refused", lambda: self.shift(life=100, by=20, code=1)
        yield "shift_percent_below_0_refused", lambda: self.shift(life=100, by=-5, code=1)
        yield "shift_percent_life_100_accepted", lambda: self.shift(life=100)
        yield "shift_time_same_op", lambda: self.shift(timeline="Time", by=30)
        yield "shift_percent_opt_in", lambda: self.shift(percent=True)
        yield "keys_then_shift_same_curve", lambda: self.layered("keys_shift")
        yield "shift_then_keys_same_curve", lambda: self.layered("shift_keys")
        yield "keys_then_two_shifts_same_curve", lambda: self.layered("shift_shift")
        yield "retime_double_saved_values_and_handles", self.retime
        yield "retime_integer_rounding", self.rounding
        yield "lint_off_grid_time_keys", lambda: self.lint(
            [[0, 8], [9, 17], [42, 17], [50, 0]], 10, 1, 42, 17, 13.6)
        # 1.80.3 spreads a shortened final interval over a full Sampling.
        yield "lint_short_final_interval_1803", lambda: self.lint(
            [[0, 0], [45, 10]], 10, 1, 44, 88 / 9, 80 / 9 + 10 / 9 * 0.4,
            runtime="1.80.3", off_grid=[45], short_tail=5)
        # 1.80.7 (the default) plays it at its real length: a short tail alone is clean.
        yield "lint_short_tail_clean_1807_default", lambda: self.lint(
            [[0, 0], [45, 10]], 10, 0, off_grid=[], short_tail=5)
        # ...but an inner off-grid key is still a finding, and the tail 40..45 plays over 5 units:
        # at 42 the line from 40 (400/42) to 45 (0) gives 400/42 * 3/5, where 1.80.3 gives * 8/10.
        yield "lint_short_tail_played_1807", lambda: self.lint(
            [[0, 0], [42, 10], [45, 0]], 10, 1, 42, 10, 400 / 42 * 0.6,
            runtime="1.80.7", off_grid=[42], short_tail=5)
        yield "lint_unknown_runtime_exit_2", self.lint_unknown_runtime
        yield "lint_sampling_1_clean", lambda: self.lint([[0, 8], [9, 17], [42, 17], [50, 0]], 1, 0)
        # Unreadable targets stay non-fatal by default (documented contract).
        yield "lint_unreadable_default_exit_0", lambda: self.lint_incomplete(False, False, 0)
        yield "lint_unreadable_with_findings_exit_1", lambda: self.lint_incomplete(True, False, 1)
        yield "lint_unreadable_strict_exit_3", lambda: self.lint_incomplete(False, True, 3)
        yield "lint_unreadable_strict_beats_findings", lambda: self.lint_incomplete(True, True, 3)
        yield "lint_empty_folder_default_exit_0", lambda: self.lint_empty(False, 0)
        yield "lint_empty_folder_strict_exit_3", lambda: self.lint_empty(True, 3)
        yield "lint_strict_complete_exit_0", self.lint_strict_complete
        yield "lint_incomplete_corpus_noted", self.lint_incomplete_corpus
        yield "source_fixtures_unchanged", self.unchanged
        yield "material_uniforms_float1_to_float4_textures_and_copy", self.material_values
        for bad in ('name', 'width', 'kind', 'missing_texture', 'nonfinite', 'not_file'):
            yield 'material_' + bad + '_refused', lambda bad=bad: self.material_values(bad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="all generated outputs and build caches")
    args = parser.parse_args()
    work = args.work.resolve()
    # Never allow generated files to be mixed with repository source folders.
    require(work != ROOT and not any(work == ROOT / p or ROOT / p in work.parents
                                    for p in ("src", "external", "tests", "tools")),
            "--work must be separate from repository source folders")
    work.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=work))
    commands = Commands(work, run)
    passed = failed = 0
    suites = {}
    try:
        for label, project in [("1.70e", "Niagara2Efk"), ("1.8", "Niagara2Efk1803")]:
            try:
                dll = commands.build(label, project)
                suite = Suite(commands, label, dll, run / label)
                suites[label] = suite
            except Exception as exc:
                failure(f"{label} setup", exc)
                failed += 1
                continue
            for name, test in suite.cases():
                try:
                    test()
                    print(f"PASS {label} {name}", flush=True)
                    passed += 1
                except Exception as exc:
                    failure(f"{label} {name}", exc)
                    failed += 1
        if len(suites) == 2:
            try:
                old, new = suites["1.70e"], suites["1.8"]
                result = old.tool("dump", new.base, code=3)
                contains(result.stderr, "newer than 1.70")
                require("unexpected error" not in result.stderr.lower(), "version error used generic exception reporting")
                print("PASS cross_build newer_fixture_exit_3", flush=True)
                passed += 1
            except Exception as exc:
                failure("cross_build newer_fixture_exit_3", exc)
                failed += 1
    finally:
        commands.log.close()
    summary = f"RESULT: {passed} passed, {failed} failed"
    print(summary, flush=True)
    (run / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        failure("runner", exc)
        sys.exit(1)
