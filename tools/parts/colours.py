#!/usr/bin/env python3
"""
Give every rendered part a dominant colour, read from its thumbnail.

The catalogue knows a part's renderer, blend and textures, but not what colour
it comes out, and "green energy" or "ice" is a colour before it is anything
else. Searching by name alone misses most of it: plenty of green bursts are
called "Node".

Each lit pixel -- one differing from the thumbnail's ground by more than the
render page's own threshold -- is averaged, weighted by how far it stands out,
so a bright core outvotes a faint halo. The mean is named by hue, or white/grey
when it is too unsaturated to have one.

    python tools/parts/colours.py out/parts/all,out/parts/sprite out/parts/colours.json
"""
import colorsys
import json
import os
import sys
from collections import Counter

from PIL import Image

LIT_THRESHOLD = 16                    # matches batch.html
GROUNDS = {"black": (0, 0, 0), "grey": (66, 66, 77)}   # batch.html BACKGROUNDS, as bytes


def name_for(hue, sat, val):
    if sat < 0.22:
        return "white" if val > 0.55 else "grey"
    for limit, label in ((15, "red"), (45, "orange"), (70, "yellow"), (160, "green"),
                         (200, "cyan"), (255, "blue"), (290, "purple"), (345, "magenta")):
        if hue < limit:
            return label
    return "red"


def pixels(im):
    get = getattr(im, "get_flattened_data", None) or im.getdata
    return get()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dirs = [d.strip() for d in sys.argv[1].split(",")]
    out_path = sys.argv[2]

    out, counts = {}, Counter()
    for d in dirs:
        report = json.load(open(os.path.join(d, "render-report.json"), encoding="utf-8"))
        for r in report["results"]:
            if not r.get("rendered") or not r.get("thumb"):
                continue
            try:
                im = Image.open(os.path.join(d, r["thumb"])).convert("RGB")
            except OSError:
                continue
            bg = GROUNDS.get(r.get("background", "black"), GROUNDS["black"])
            sr = sg = sb = wsum = 0.0
            for (R, G, B) in pixels(im):
                w = max(abs(R - bg[0]), abs(G - bg[1]), abs(B - bg[2]))
                if w <= LIT_THRESHOLD:
                    continue
                sr += R * w; sg += G * w; sb += B * w; wsum += w
            if not wsum:
                continue
            h, s, v = colorsys.rgb_to_hsv(sr / wsum / 255, sg / wsum / 255, sb / wsum / 255)
            label = name_for(h * 360, s, v)
            out[r["id"]] = {"hue": round(h * 360, 1), "sat": round(s, 3), "val": round(v, 3),
                            "colour": label, "background": r.get("background", "black")}
            counts[label] += 1

    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=0)
    print("coloured %d parts -> %s" % (len(out), out_path))
    print("  " + ", ".join("%s %d" % kv for kv in counts.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
