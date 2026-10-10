# Trail-effect checklist

Lessons from a 2026-10-04 study of four sword-trail effects drawn with
Effekseer Track nodes, each retextured with a generated variant. The study
notes behind it (`FINDINGS.md`, two `INSIGHTS.md`) are local working files
under `out/edits/` and are not in the repository. Two items come from
the project's agent guidelines rather than the study: "Render past the end" (§ 5) and
the reuse-first advice in diagnosis step 7.

**[P]** = general principle, applies to any trail renderer.
**[T]** = tested observation from one renderer and one effect. It is an
example only. Re-check it on your target before relying on it.

## 1. Before editing the texture

- [ ] **[P] UV direction.** Find out which texture axis runs along the path
      and which runs across the width. Confirm it with a dump or a test
      texture. Don't assume it.
  - [T] Effekseer Track: U spans the width and V follows the path.
    Horizontal streak textures needed a (1−V, U) mapping. Other renderers
    and ribbon types may differ.
- [ ] **[P] Active layers.** List every emitter or node that draws the trail.
      Look at each one alone before you fade or redraw a bitmap.
  - [T] A narrow arc and a wide arc drawing together read as one doubled
    trail. Disabling the wide arc removed the doubling and kept the motion
    and timing.

## 2. Channels and edges

- [ ] **[P] Read the real channels.** Check whether the file really has
      alpha. Don't trust a description of it.
  - [T] One supplied PNG had no alpha, although it was described as having
    it.
- [ ] **[P] Blend mode decides what black means.** Under additive blending,
      black RGB disappears but is not transparent. Under alpha blending it
      shows. Colour stored under alpha 0 is invisible, so it is not a border.
- [ ] **[P] Check all four edges.** Partial alpha or a transparent
      background does not prove the edges are feathered. Inspect the texture
      inside the effect, on both grey and near-black backgrounds, not only in
      an image viewer.
  - [T] Some generated variants kept faint border pixels and irregular
    cutouts.

## 3. Generated or refined textures

- [ ] **[P] A new texture is a new variant.** The streak's shape, width and
      intensity distribution can all change.
- [ ] **[P] Softer is not better by default.** A softer or higher-resolution
      bitmap can make the trail look thinner. It never extends the trail's
      lifetime.
- [ ] **[P] Keep the source texture** untouched for side-by-side comparison.

## 4. Material bindings

- [ ] **[P] Read back after saving:** the node's texture binding, the
      material defaults, the packaged resource hash and the runtime
      appearance.
- [ ] **[P] Give every texture slot a distinct display name and uniform
      name.**
  - [T] An old texture survived a material-path change because the uniform
    names matched. Distinct names fixed it.
- [ ] **[P] Record advanced-render flags before switching to a file
      material.** A file material can drop those settings.

## 5. Timing

- [ ] **[P] Measure the trail on its own.** Measure how long the isolated
      trail is visible, separately from the whole effect's lifetime. Sparks
      and other layers lengthen the total.
  - [T] Example from one effect only: the isolated arc was visible at
    indices 1–17, and the full effect reached permanent zero at index 34.
    Controller life was 7 frames, point life 10 and fade-out 3. Later
    variants had matching full-effect traces, but their isolated arcs were
    not measured.
- [ ] **[P] Render past the end.** If the length is unknown, use at least 240
      updates. If the effect is still alive on the last frame, report "still
      alive at fN", not an end.
- [ ] **[P] Report timing in simulated updates,** and state the frame-index
      convention you used.

## 6. Previews and reported numbers

- [ ] **[P] Label every preview's speed,** for example "6× slow GIF". Slow
      previews are for looking, never for timing.
- [ ] **[P] Brightness belongs to a layer.** The whole effect's brightness
      peak may come from sparks. Don't report it as the trail's brightness.

## 7. "Unchanged" claims: say which one

- [ ] **Source integrity:** the source file's hash is unchanged.
- [ ] **Saved-value equivalence:** the dumped node values are identical.
- [ ] **Byte identity:** the output files are byte-for-byte identical.
- [ ] **[P]** Compare the property that your claim actually needs.
  - [T] A no-op edit produced identical saved values but different container
    bytes.

## 8. Verification gates

Each gate answers a different question. Passing one never implies the next.

| Gate | Question it answers |
|---|---|
| Saved readback | Does the file contain what the spec says? |
| Load / draw | Does the runtime load the effect and draw it? |
| Trace to zero | Does the effect end? |
| Live game | Does it behave correctly in the target game (freeze, timing, camera)? |
| User visual approval | Does it look right? Only the user can decide this. |

- [T] When the findings were written: 16 renders (grey and dark backgrounds,
  240 updates) matched the original and refined instance traces. That covers
  load/draw and trace to zero. No live-game test, installation or approval
  had been done at that point.
- [T] Later the same day, the refined set and the originals were installed
  in a test project and played in a real battle: all eight loaded and ended
  after 48 game frames. That covers the live-game gate for load, end and
  first-cast freeze only. It says nothing about the look.

## 9. Cleanup that preserves evidence

- [ ] **[P] Keep the evidence:** final assets, specs, dumps, traces, prompts,
      inventories and any helper scripts still worth reusing.
- [ ] **[P] Archive before removing.** Archive superseded evidence and
      hash-verify the archive against the loose copies. Only then remove the
      loose copies.
- [ ] **[P] State the scope of any temp-file scan.**
  - [T] The last scan covered four trail folders and found no browser
    profiles and no .tmp, .temp or .bak files. It does not cover other
    temp locations.

---

## Diagnosis order: a doubled, sharp or apparently brief trail

Work on a copy, change one thing at a time and go in this order. Steps 1–5
are cheap to check, and a texture or timing edit can hide their causes.

1. **Layers.** Find which emitters draw the trail, then look at each one
   alone. *Doubled:* first suspect overlapping arcs of different widths.
2. **Geometry and UV.** Check that the streak runs along the path. A rotated
   mapping can look banded, chopped or hard-edged across the width.
3. **Channels and blend.** Check whether alpha really exists and how black
   behaves under this blend mode.
4. **Edges.** Check whether all four edges fade out, on grey and near-black,
   inside the effect. *Sharp:* first suspect an unfeathered border, then
   the UV mapping from step 2.
5. **Material binding.** Confirm that the texture on screen is the one you
   bound. A stale slot can survive a material change.
6. **Timing.** *Brief:* compare the isolated trail's visible span with the
   controller life, point life and fade-out, in simulated updates. Then
   rule out a mislabelled preview speed, and spark or brightness numbers
   attributed to the trail. A thinner-looking texture can read as shorter
   even when the lifetime is unchanged.
7. **Only then edit.** Before adding new parts, prefer reusing or retiming
   what is already there. Keep the source texture and the earlier variant
   for comparison.

## Open uncertainties from the study

- The (1−V, U) mapping and the 1–17 / 34 / 7-10-3 frame values were checked
  on one renderer and one effect only.
- The refined variants' isolated arcs were not measured. Only their
  full-effect traces were compared.
- Some generated variants still have faint border pixels. These were not
  resolved in the findings.
- The binding survival through uniform-name matching was seen in one
  replacement workflow. Whether it happens in other workflows is untested.
- No user visual approval is recorded for any variant. The in-game run
  showed that the effects play and end, not that they look right.
- The temp-file scan covered four folders only.
