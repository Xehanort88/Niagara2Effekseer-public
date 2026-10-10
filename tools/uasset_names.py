"""Pull readable identifiers out of an uncooked .uasset.

Not a real parser. Uncooked Unreal packages carry a name table of plain strings,
and for a NiagaraSystem those names include the renderer classes, the module
scripts in the emitter stack, and the simulation target. That is enough to judge
how convertible an effect is without opening the editor.
"""
import re
import sys
from collections import Counter

data = open(sys.argv[1], "rb").read()

# ASCII runs of 4+ printable chars, and UTF-16LE runs.
ascii_s = set(m.group().decode("ascii") for m in re.finditer(rb"[ -~]{4,}", data))
utf16_s = set()
for m in re.finditer(rb"(?:[ -~]\x00){4,}", data):
    utf16_s.add(m.group().decode("utf-16-le"))
names = ascii_s | utf16_s

PATTERNS = {
    "renderers": r"Niagara\w*RendererProperties",
    "sim target": r"SimTarget|GPUComputeSim|CPUSim|bSimulationStagesEnabled",
    "modules": r"^/Niagara/(?:Modules|Emitters)/[\w/]+",
    "data interfaces": r"NiagaraDataInterface\w+",
    "materials": r"^/Game/[\w/]*(?:M_|MI_)[\w/]*|^/Engine/[\w/]*(?:M_|MI_)[\w/]*",
    "textures": r"^/Game/[\w/]*T_[\w/]*|^/Engine/[\w/]*T_[\w/]*",
    "meshes": r"^/Game/[\w/]*SM_[\w/]*|^/Engine/[\w/]*SM_[\w/]*",
}

print("=== %s ===" % sys.argv[1].split("\\")[-1])
print("unique strings: %d" % len(names))
for label, pat in PATTERNS.items():
    rx = re.compile(pat)
    hits = sorted({n for n in names if rx.search(n)})
    print("\n-- %s (%d) --" % (label, len(hits)))
    for h in hits[:25]:
        print("   ", h)
    if len(hits) > 25:
        print("    ... %d more" % (len(hits) - 25))
