#!/usr/bin/env python3
"""
Join the filmstrip frames render.js saved for each effect into one labelled
image per effect, plus one image stacking every strip.

A filmstrip is the only fair way to judge an assembled effect. One brightest
frame cannot say whether a column bursts at its end or its start, or whether
three green explosions fire one after another or all at once.

    python tools/parts/strip.py out/parts/trial
"""
import json
import os
import sys

from PIL import Image, ImageDraw

LABEL_H = 18
GAP = 4
BG = (14, 14, 16)
TEXT = (200, 200, 206)
DIM = (120, 120, 128)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    report = json.load(open(os.path.join(root, "render-report.json"), encoding="utf-8"))
    out_dir = os.path.join(root, "strips")
    os.makedirs(out_dir, exist_ok=True)

    made = []
    for r in report["results"]:
        shots = r.get("strip") or []
        frames = []
        for s in shots:
            path = os.path.join(root, s["file"])
            if os.path.exists(path):
                frames.append((s, Image.open(path).convert("RGB")))
        if not frames:
            print("  %s: no strip frames" % r["id"])
            continue

        w, h = frames[0][1].size
        title_h = LABEL_H + 2
        sheet = Image.new("RGB", (len(frames) * (w + GAP) - GAP, title_h + h + LABEL_H), BG)
        draw = ImageDraw.Draw(sheet)
        draw.text((2, 3), r["id"], fill=TEXT)
        for i, (s, im) in enumerate(frames):
            x = i * (w + GAP)
            sheet.paste(im, (x, title_h))
            draw.text((x + 3, title_h + h + 3), "f%d  %dpx" % (s["frame"], s.get("lit") or 0), fill=DIM)

        path = os.path.join(out_dir, r["id"] + ".png")
        sheet.save(path)
        made.append((r["id"], sheet))
        print("  %s: %d frames -> %s" % (r["id"], len(frames), path))

    if made:
        width = max(im.size[0] for _, im in made)
        height = sum(im.size[1] for _, im in made) + GAP * (len(made) - 1)
        stack = Image.new("RGB", (width, height), BG)
        y = 0
        for _, im in made:
            stack.paste(im, (0, y))
            y += im.size[1] + GAP
        path = os.path.join(out_dir, "all-strips.png")
        stack.save(path)
        print("stacked %d strip(s) -> %s" % (len(made), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
