# Reference

The full documentation of every command and tool, with what was measured and found along the way. Start with the
[README](../README.md) for setup and an overview. This file was kept while the tools were built, so sections carry
dates, measurements and the reasons behind each choice; `out/...` paths name local working folders that are not part
of the repository.

> **Scope.** No game or third-party content is included. Use the tools on effects, packs and exports you have the
> right to use, and do not distribute effects converted from someone else's content without permission
> ([README § Scope and responsibility](../README.md#scope-and-responsibility)).

## Layout

| Path | What |
|---|---|
| `external/Effekseer/` | Pristine shallow clone, **pinned to tag `170e`**. Do not edit. |
| `src/EffekseerCore170e/` | Our SDK-style wrapper that builds the upstream sources at net9.0. |
| `src/Niagara2Efk/` | Console generator. Currently sample effects + validation, not the converter. |
| `tools/render-harness/` | Self-contained WebGL page that plays effects under the project's own 1.70e runtime. |
| `tools/parts/` | Contact-sheet pipeline: enumerate distinct parts, render each, build the sheet. |
| `tools/fontatlas/` | Letter sheets for the POLISH Quests/Results text effects from any TTF/OTF font. |
| `tools/meshimport/` | GLB mesh to `.efkmodel` + lit 1710 `.efkmat` (with tests). |
| `tools/gradientmap/` | Gradient-map recolour: greyscale texture through a colour ramp, unlit 1710 `.efkmat` + `edit` spec (with tests). |
| `tools/hlslcheck/` | Checks that `.efkmat` file materials compile as DirectX HLSL, as the Effekseer editor builds them (Windows, with tests). |
| `tools/assetinspect/` | Read-only inspection of source assets: binary FBX inventory, texture contact sheets, OBJ wireframes (with tests). |
| `tools/ingame/` | Plays animations in a real game project's battle scene on the real GPU: first-cast freeze, frame times, end, per-frame captures. |
| `plugins/` | RPGReactor plugins (mods) for game projects, e.g. `ReactorEffectPreload.js`. |
| `docs/TRAIL-CHECKLIST.md` | Checklist and diagnosis order for trail effects that look doubled, sharp or too short. |
| `efkefc_dump.py` | `.efkefc` → readable XML decoder (independent of the C# path). |
| `out/` | Generated effects, `audit-report.csv`, `niagara2efk.log` (not in the repository). |

## Build

`external/Effekseer` is a git submodule pinned to tag `170e`. On a fresh clone,
fetch it first:

```bash
git submodule update --init --depth 1
```

`out/` and the pack-derived harness cases are not in the repository; see
`.gitignore`.

```bash
dotnet build src/Niagara2Efk/Niagara2Efk.csproj -c Release -p:Platform=x64
```

### Two builds: Effekseer 1.70e and 1.80.3

The same sources also build against Effekseer **1.80.3** (tag `1803`, checked out
at `external/Effekseer1803`), through `src/EffekseerCore1803` and
`src/Niagara2Efk1803`:

```bash
dotnet build src/Niagara2Efk1803/Niagara2Efk1803.csproj -c Release
```

Pick the build by the runtime of the project the effect is for:

| Build | Output | Plays in |
|---|---|---|
| `src/Niagara2Efk/bin/Release/net9.0/Niagara2Efk.dll` | binary version **1710** | 1.70 and 1.8 runtimes |
| `src/Niagara2Efk1803/bin/Release/net9.0/Niagara2Efk.dll` | binary version **1810** | 1.8 runtimes only |

Only the 1.8 build can open files saved by the 1.8 editor, and only it can set
1.8 features. `ASSEMBLER TEST PROJECT` runs the 1.80.3 web runtime since
2026-09-27; `REACTOR DRAFT PROJECT` still runs 1.70.

`src/Niagara2Efk/EfkCompat.cs` holds every difference between the two cores.
1.8 moved six `CommonValues` settings into new groups and kept the old names as
load-only shims, so writing the old name succeeds in memory and is **silently
dropped on save**. Code reaches them through `EfkCompat`, and `edit` translates
spec paths, so a spec written with either spelling works on both builds:

| 1.70 path | 1.8 path |
|---|---|
| `CommonValues/GenerationTime` | `CommonValues/Generation/GenerationTime` |
| `CommonValues/GenerationTimeOffset` | `CommonValues/Generation/GenerationTimeOffset` |
| `CommonValues/RemoveWhenLifeIsExtinct` | `CommonValues/Removal/WhenLifeIsExtinct` |
| `CommonValues/RemoveWhenParentIsRemoved` | `CommonValues/Removal/WhenParentIsRemoved` |
| `CommonValues/RemoveWhenAllChildrenAreRemoved` | `CommonValues/Removal/WhenAllChildrenAreRemoved` |
| `CommonValues/TriggerParam/ToStartGeneration`, `ToStopGeneration`, `ToRemove` | `CommonValues/Generation/ToStartGeneration`, `ToStopGeneration`, `CommonValues/Removal/TriggerToRemove` |

That list is every property 1.8 loads but no longer saves (diffed from both
cores' `[IO(Export = false)]` attributes). Two smaller API changes are also
gated: `Effekseer.Utl` became `Effekseer.Utils`, and `EfkEfc.Load` now returns
success and keeps the document in `EditorData`.

Verified 2026-09-27: `selftest` gives 182/182 byte-identical on **both** builds;
the 1.70 build still reproduces earlier outputs byte for byte; the same
assemble + edit specs give the same end frame and particle peak from either
build when played under 1.80.3; a spec using the old names saves the values
into the new fields on the 1.8 build.

**1.8 features by spec path** (1.8 build only; `dump` lists every path):

| Feature | Path |
|---|---|
| Ground collision | `CollisionsValues/IsGroundCollisionEnabled`, `Height`, `WorldCoordinateSyatem` (`Local` = relative to the effect's centre), `Bounce`, `Friction`, `LifetimeReductionPerCollision` |
| Face direction of travel | `RotationValues/Type` = `RotateToVelocity` (`RotationValues/Velocity/Axis`), or `RotateToViewpoint` |
| Trail and ribbon tiling by length | `DrawingValues/TextureUVType/Type` = `Tile` or `TilePerParticle`, `DrawingValues/TextureUVType/TileLength` |
| Directional billboard | `DrawingValues/Sprite/Billboard` = `DirectionalBillboard` |
| UV flip and mirror wrap | `RendererCommonValues/UVFlipHorizontalProbability`, `RendererCommonValues/Wrap` = `Mirror` |
| Spawn timing and triggers | `CommonValues/Generation/Timing`, `Trigger`, `TriggerCount`, `CommonValues/Removal/TriggerToRemove` |

**Collisions need force-field gravity.** The runtime corrects only world-space
motion (`Effekseer.Instance.cpp`), and a particle's own PVA acceleration keeps
pulling it through the floor. Put gravity in a force field instead:
`LocationAbsValues/LocalForceField1/Type` = `Gravity` with
`LocationAbsValues/LocalForceField1/Gravity/Gravity` = `[0, -g, 0]`. Measured with
one dropped sprite: it settles on the plane with collision on, and falls off
screen with it off. `out/edits/fireforge/rest.py` has a worked example
(`FireOne2-pillar-ash18`): the pillar's ash lands on the floor.

`src/Niagara2Efk.Mapper` (the Niagara converter's back half) still builds
against 1.70e only.

## Commands

Minimal node with no renderer resources, diffable between runs:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll roundtrip out/a.efkefc 60 2.0
```

A visible additive sprite burst. The `.efkefc` must be written next to a
`Texture/` folder, because resource paths serialize relative to the effect file:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll sprite out/effects_test/SpriteBurst.efkefc out/effects_test/Texture/Particle100.png
```

A neutral base to author a new effect from with `edit` (see
[Building an effect from scratch](#building-an-effect-from-scratch)): one node,
one particle, fixed life (default 60), still at the origin, upright, scale 1, no
gravity, a white additive sprite. Use it instead of `sprite`, whose burst
settings (random velocity, gravity, shrinking, random life) leak into every
node copied from it:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll blank out/edits/<task>/blank.efkefc 60
```

Load and re-write an effect. Doubles as the "upgrade an old effect to binary
version 1710" utility:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll resave in.efkefc out.efkefc
```

Load every effect under a directory, re-save it, read it back, and report:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll audit <effectsDir> out/audit-report.csv
```

The corpus is left exactly as it was found. Each effect is re-saved to a
temporary file beside itself, because resource paths serialize relative to the
effect file and writing anywhere else would shift every texture path. That file
is then always removed: deleted, or moved out to `out/audit-intermediates/`
when the effect was already at binary version 1710 and still came back
different, which is the case worth looking at by hand. Pass a third argument to
choose another directory, or `none` to keep nothing:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll audit <effectsDir> out/audit-report.csv none
```

A kept intermediate mirrors its path under the corpus root, so two effects with
the same name in different folders cannot collide. It is there to be **decoded
and diffed**, not played: its resource paths point back to where the original
effect lives and no longer resolve from the intermediates folder. The report's
`intermediate` column gives the path for each one.

Build the effect-assembler parts catalogue. Walks every node of every effect
under each corpus and writes one JSON index (see `ASSEMBLER-BRIEF.md`).
Corpora are given as `id=dir`; nothing under them is written:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll catalogue out/catalogue.json "stock=../REACTOR DRAFT PROJECT/effects" "death-dancer=<Effekseer tool folder>/death dancer"
```

`out/catalogue.json` covers **48 corpora** (rebuilt 2026-09-29): the stock
effects, every effect pack under `<Effekseer tool folder>`, and
`premium-44`, which is staged under `out/corpora/` (see below). The pack corpus id is
the folder name lowercased with each run of non-alphanumeric characters
collapsed to a hyphen, so part ids stay stable across runs. Build the argument
list rather than typing it:

```bash
PACKS="<Effekseer tool folder>"
echo "stock=../REACTOR DRAFT PROJECT/effects" > corpora.txt
for d in "$PACKS"/*/; do
  n=$(basename "$d")
  case "$n" in Tool|"EVFX Death Dancer MZ Demo"|"Premium #44") continue;; esac
  id=$(echo "$n" | tr 'A-Z' 'a-z' | sed -e 's/[^a-z0-9][^a-z0-9]*/-/g' -e 's/^-//' -e 's/-$//')
  echo "$id=$PACKS/$n" >> corpora.txt
done
echo "premium-44=out/corpora/premium-44" >> corpora.txt
mapfile -t ARGS < corpora.txt
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll catalogue out/catalogue.json "${ARGS[@]}"
```

`Tool` holds no effects. `EVFX Death Dancer MZ Demo` is skipped because it is the
same 16 files byte for byte as `death dancer`, which already has the established
corpus id. `Premium #44` is an RPG Maker sample project, not a plain pack
folder. 199 of its 205 effects are byte-identical to files in `MCG`, 7 of its
effect sets are still zipped, and two of its copies were re-saved in 1.8
format. Only the 33 effects not already in `MCG` are catalogued: the
`0043_MaleficGaze` and `0044_Claws` folders, plus zips `0031`–`0036` extracted
with their `MZ & Effekseer/effects/` or `Effekseer/` prefix stripped. They are
staged in `out/corpora/premium-44/` by
`out/corpora/_scratch/stage_premium44.py`. Since `out/` is not committed,
rerun that script on a fresh checkout before rebuilding.

The command exits 1 while any effect fails to load. As of 2026-09-29 exactly
one fails: `evfx-spellblade-mz-demo/effects/heavencloud ultima blade light hilt
needs work`. It was created in the 1.8 editor, it is not part of the Spellblade
pack, and no 1.7 version exists. Five other pack files had been re-saved in 1.8
format by the editor. On 2026-09-29 they were restored from verified originals
(their sibling files match byte for byte). The 1.8 copies are kept in
`out/corpora/_backup-1810/`. The five are:

- `fireforge/EVFXForge11_11_BlazeforgeStrike`, from `<effect pack folder>`
- `Astrology_Effekseer.1.0.4/Starflare`, from `<effect pack folder>`
- `Sample/00_Version16/ProceduralPudding`, from the Effekseer install's
  `ResourceData/samples`
- Premium #44's `Pain_V2` and `MegatonPressure`, from `MCG`

`out/corpora/_scratch/scan18.py` lists any pack effect stamped newer than 1710.
`Sample/00_Basic/Simple_Sprite_Directional` (1800) ships that way from
Effekseer, and 170e reads it.

Per node it records the tree path, name, renderer, parent renderer and whether
that parent is a controller (renderer `None`), the parent-inheritance flags,
life / generation settings, motion and spawn types, force fields, material type,
blend, advanced-render toggles, and the resource files the runtime will load.
Resource gating follows `Binary/Exporter.cs` exactly, so a texture set on a node
but unreachable by the exporter's rules is not listed. Model references are
reported as the `.efkmodel` the runtime opens, with the authored `.fbx` path
alongside.

Assemble a new effect from parts of existing ones, and verify it:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll assemble out/specs/hardcases.json out/assembled/HardCases.efkefc
```

A spec names parts by catalogue id (or file path) plus a node path, with optional
`offset`, `scale`, `delay`, `lifeScale` and `ancestors`:

```json
{
  "catalogue": "../catalogue.json",
  "parts": [
    { "source": "stock/MGC_W3_WaterShield", "node": "0/6", "name": "WaterShell" },
    { "source": "death-dancer/EVFXParagon15_09_FlameHoop", "node": "2/0/0/0",
      "offset": [7, 0, 0], "scale": 0.5 }
  ]
}
```

`delay` is in frames and is **added to the part's authored start**, never
substituted for it, and any randomness the author put in that start is kept. It
is applied to the outermost node of the part, the first ancestor carrier when a
chain is rebuilt, because a child only spawns while its parent is alive: a delay
placed on the part itself could outlast a short-lived carrier and stop the part
spawning at all. A negative delay pulls the part earlier but not before frame 0,
and the assembler says when it clamps one.

This was measured rather than assumed. By first visible frame under the shipped
runtime, a part authored to start around frame 40 appeared at 45 with no delay,
at 36 with a delay of 30 under the old substitute-the-value behaviour, and at 75
now. A 90-frame delay on a part under a four-link ancestor chain never appeared
under the old placement and appears at frame 108 now.

A part given an `offset` or `scale` hangs under a non-drawing anchor node. The
anchor is removed once everything under it is gone. Before that fix it lived
for ever, so the effect never ended, and RPGReactor waits on `handle.exists`
before moving on: every assembled effect with an offset or scale stalled the
battle. The runtime keeps a child group active until it has spawned its last
instance, so a delayed part is not cut short.

Check that grafting is faithful, against the whole corpus:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll selftest out/catalogue.json
```

### Testing

Run the generated-fixture `edit`/`dump` regression suite with system Python
(standard library only) and the .NET 9 SDK:

```bash
python tests/edit/run_tests.py --work out/tests/edit
```

The runner builds both tool versions, generates its own effects and PNG, and
prints one line per named test; exit 0 means every test passed. No pack content
is used. Build outputs, caches, temporary files, fixtures and command transcripts
stay under `--work`; each invocation keeps a fresh `run-*` evidence folder.
Run it twice with the same work path to check repeatability. NuGet access is
needed on the first build. The corpus `selftest` above remains a separate check.

The tool folders have their own unit tests (Pillow required), each keeping its
files under `--work`:

```bash
python tools/gradientmap/test_gradientmap.py --work out/tests/gradientmap
```

Its plan test builds a fixture with the tool's `sprite` and `edit` commands and
needs the built 1.70e DLL (`--tool` points at another build; it is skipped when
the DLL is missing).

```bash
python tools/assetinspect/test_assetinspect.py --work out/tests/assetinspect
python tools/fontatlas/test_make_atlases.py --work out/tests/fontatlas
node tools/ingame/test_ingame.js --work out/tests/ingame --port 8931
```

The asset-inspection tests write their own binary FBX files (both record
layouts, raw and zlib arrays, plus truncated, corrupt, ASCII and big-endian
variants), OBJ text and PNGs; no third-party asset is used.
The letter-sheet tests need fontTools to build their fixture fonts (subsets of
`--font`, default Arial: missing `Z` and `!`, an empty `Z`, `Z` mapped to
glyph 0, `Z` and `Q` drawn with the `X` and `O` glyphs) and git, to
check that a complete font still gives sheets byte-identical to the committed
`make_atlases.py`. The in-game tests run the result rules, `compare.js` on
generated capture folders, and `run.js` under real headless Chrome against fake
projects that hang, throw or post a canned result (`--no-chrome` skips those).
No game runs in those. `--project "../ASSEMBLER TEST PROJECT"` adds live cases
in that game, read-only, on stock animation 3: a clean run, then each failure
forced for real (a corrupt effect file through `--sub`, an engine exception and
a page error through `--prepare`, an animation cut off by `--max-frames` and
`--capture-seconds`), and a capture rerun over stale frames compared with a
noise floor. They take about five minutes and need the real GPU.

Read a cooked Unreal **Cascade** dump and write the intermediate document. This
is a front end only: it never touches Effekseer, and the mapper turns the
document into an effect. The four numbers are world scale, sprite scale,
turbulence gain and lift gain, none of which is derivable from the source:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll cascade intermediate/dumps/FX_SpawnShapes.cascade.json intermediate/FX_SpawnShapes.json 0.5 2.0 0.2 0.1
```

What it reads: lifetime, the whole spawn envelope, emitter delay, spawn volume,
initial velocity, constant acceleration, drag, sprite size and how it changes
across the life, rotation and spin, camera offset, constant colour, colour ramps,
flipbook sheets, and the local vector field with the orientation it was given.
The blend mode too, where the emitter states it: stock Cascade leaves it in the
material graph, but a forked engine may name it outright, and assuming additive
turns every soft smoke into a glowing one.

A fork is the harder case in general. It states the same quantity in different
units, degrees where stock Cascade counts turns, and puts the same job under a
different module name. So a module this reader has no rule for is now declared
rather than passed over: a name nobody taught it becomes a line in the report.

Motion is stated in frames on the Effekseer side and in seconds on the Cascade
side, so velocities are divided by the frame rate and accelerations by its
square. Rotation is worse: Cascade counts turns and Effekseer authors degrees, so
a turn is 360 and a turn per second is 6 degrees per frame. A value in a dump may also be written inline or as a pointer to a
separate distribution export, and the two forms sit side by side inside one
effect, so every read follows the pointer before giving up. Emitters switched off in Cascade are skipped rather than converted.
Everything else is written into the document's `unsupported` array, which the
schema treats as a deliverable rather than a log.

Spawning is three things at once and they only make sense together: a continuous
rate, a list of bursts, and a duration and loop count that decide when the
emitter stops. A node has one generation interval, so a rate and a burst cannot
both arrive as written. Where there is a rate it wins and the burst joins the
total, which is declared as approximated; where there is none, a near-zero
interval fires the burst at once. The number of particles a finite run would
produce becomes the generation cap, which is what stops a one-shot cast from
converting into an emitter that never ends.

Flipbooks are worth a note, because Effekseer states UV rectangles in **texels**
and its exporter divides them by the real texture size. Writing a rectangle into
the document would tie it to one resolution, so the document carries the grid and
the timing and the mapper measures the cell against the texture it copied. Author
a sheet at whatever size suits it and nothing needs changing. The grid itself is
declared on Cascade's required module, not on its SubUV module: SubUV only says
how the cell index moves.

Spawn volumes come from the primitive modules, where the size lives in
`StartRadius` and `StartHeight`. A sphere becomes a sphere, keeping its shell or
its volume; a cylinder becomes a flat circle and declares the lost height; a
plain location module becomes a point randomised per axis, which is the same box
the source described.

`intermediate/dumps/FX_SpawnShapes.cascade.json` is a hand-written dump that
exercises those paths and carries no third-party content.

## How the assembler stays accurate

`Core.Copy`/`Core.Paste` serialize with `isClip: true`, and in that mode
`Data/IO.cs` writes every `Value.Path` **absolute**. A pasted subtree therefore
still points at the source effect's own files, which is what makes an exact
rebuild possible: the destination tree is walked, each referenced file is copied
next to the output, and the path is repointed. Nothing is guessed.

Four things do not survive a paste and are repaired explicitly.

| Hazard | What breaks | Repair |
|---|---|---|
| Procedural models and dynamic equations | stored as a bare index into a project-level collection, so they dangle in a fresh project | the index is read on the source side, the definition carried over, and the reference repointed |
| File materials | changing the material path reloads the `.efkmat` and resets its texture slots | slots are captured first and restored afterwards |
| Project settings | `Global.RandomSeed`, `Culling` and `LodValues` go straight into the runtime chunk | taken from the first part's source |
| The ancestor chain | a part's position, rotation, scale and spawn timing come from its ancestors, whatever their renderer | rebuilt as non-drawing carriers by default (`"ancestors": "controllers"`) |

Paths on switched-off slots are a normal thing to find in the corpus. They are
not copied and a missing file there is not an error, matching exactly what
`Binary/Exporter.cs` would write.

**Measured.** For all 182 corpus effects whose root has a single child, grafting
that child into a fresh project reproduces the source **byte for byte in the
`BIN_` and `INFO` chunks** — the data the player actually consumes. The control
is a plain `resave`, not the file on disk, because most of the corpus predates
binary version 1710 and is legitimately migrated on save.

## Parts contact sheet

A catalogue you cannot look at is not a catalogue. `out/catalogue.json` describes
28,647 drawing nodes, and no one picks a part out of that by reading metadata:
"Ring, Default material, three-link chain" does not tell you it is a shockwave.
The contact sheet renders one thumbnail per part so parts can be chosen by eye.

Three stages, each usable on its own.

**1. Enumerate.** Collapse the corpus's drawing nodes down to distinct parts and
pick one representative for each. A part's identity is what it renders, how it
moves, what it inherits, which files it loads and the shape of its ancestor
chain — everything except its name and which effect it sits in. The corpus
repeats itself heavily, so this roughly halves the work:

```bash
python tools/parts/enumerate.py out/catalogue.json out/parts/selection.json --renderers Ring,Model,Track,Ribbon
```

Pass `--renderers all` to include Sprite, which is most of the library, or
`--limit N` to take a slice. The representative chosen for a fingerprint is the
one with the shallowest ancestor chain, so reruns pick the same node.

**2. Assemble.** Build one standalone effect per part, all in one process:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll parts out/parts/selection.json out/parts/all
```

Every part is lifted with `ancestors: controllers`, which is the only setting
that works for Track and Ribbon — not one Track node in the corpus stands on its
own. Thousands of separate `assemble` processes would spend their time starting
the runtime, and the self-test already proves many grafts in one process stay
byte-exact. Output is one directory per part holding the `.efkefc` and its own
copy of the textures, plus `manifest.json`.

**3. Render.** Play every part under the shipped 1.70e runtime in headless
Chrome and keep the brightest frame:

```bash
node tools/parts/render.js --parts out/parts/all
node tools/parts/sheet.js --parts out/parts/all
```

`render.js` is a static file server that also accepts results; the page does the
work and the run ends when the page says so. No browser-automation dependency.
`sheet.js` then writes the contact sheet, filterable by renderer, pack, whether
the part drew anything, and free text. Give it several rendered directories to
get one sheet over all of them:

```bash
node tools/parts/sheet.js --parts out/parts/all,out/parts/sprite --out out/parts/contact-sheet.html
```

Serve the sheet over HTTP rather than opening it from `file://`.

**4. Pick and build.** Clicking a card adds it to a tray on the right, in graft
order, and the tray writes the assembler spec for you: source, node path, and
`ancestors: controllers`, plus optional offset, scale, delay and life multiplier
per part. Save the spec into `out/parts/specs/`, run the command the tray
prints, and the effect is built. The picked set survives a reload, and the
`picked only` filter shows just what is in the tray.

**Align to a lead.** Mark one picked part as the lead and press align. Every
other part is scaled so its measured 90% radius matches the lead's, and delayed
so its brightest frame lands on the lead's brightest frame. Neither number is
tuned. Size scales linearly with a spec's `scale` because both are taken about
the origin, and a part's peak moves by exactly its `delay` because the peak was
measured at the part's authored start and `delay` adds to that start. The lead's
own scale and delay are respected. Scale is clamped to 0.1 to 10.

A part that peaks later than the lead would need a negative delay, and the
assembler can only pull a part earlier as far as its outermost carrier's
authored start, often frame 0. So when any delay comes out negative, align
pushes every part later instead, lead included, and the peaks still coincide.
The tray says when it does this, and warns when a part or the lead was clipped
when measured, since a clipped size is a lower bound and the scale is then off
in a known direction.

Verified by rendering each aligned part on its own. The lead was a particle
ring, aligned against an aura and an impact glow:

| Part | Measured before | Rendered after align |
|---|---|---|
| Particle ring, lead | size 5.38, peak frame 24 | size 5.49, peak frame 54 |
| Aura | size 2.49, peak frame 54 | size 5.07, peak frame 54 |
| Impact glow | size 1.45, peak frame 0 | size 5.49, peak frame 54 |

Every peak landed on the same frame, and sizes agree within about 8%; the lead
itself varies about 2% between renders. Combined, the unaligned effect showed
only the ring, while the aligned one reads as a single impact, drawing 9,717
pixels at its peak against 1,198. Align matches extent, not brightness: the glow,
scaled up 3.71 times, now washes out the centre of the ring, so an additive part
may still need its scale brought down by hand.

**Filmstrips.** A manifest entry may carry `strip`, a list of frame numbers, and
the render page snapshots each one alongside the usual brightest frame.
`strip.py` joins them into one labelled row per effect plus a stacked
`all-strips.png`. This is the only fair way to judge an assembled effect: a
single brightest frame cannot tell a column that bursts at its end from one that
bursts at its start. Strip frames are drawn even off the three-frame sampling
grid, but only grid frames feed the trace, so peak and visibility frames keep
their meaning. `render.js --manifest` renders effects straight out of a corpus
without writing a manifest into it; `--out` takes the thumbnails and report.

**Seed A/B renders.** One page plays every entry in one Effekseer context, and
the context's random state runs on from play to play: the same file listed twice
can come out different (46 of 159 wind nodes did, up to 69% of their pixels).
Give each entry the same `"seed": <int>` when comparing renders, e.g. an effect
against a copy with one value changed; the page then calls
`handle.setRandomSeed(seed)` right after `play`, and identical files render
pixel-identical. Both `tools/parts/render.js` and `tools/parts/gamecam/render.js`
take it. To keep the random draws in the same order in the copy, narrow a range
instead of fixing it (zero a `[2.3..9.9]` as `[0, 0.0001]`; see § Procedural
lightning bolts on new ranges). A `.efkmat` that animates on its own time can
still differ between seeded plays (`MGC_W3_WindShield`'s sphere nodes do), so
render the same file twice first and check the pair matches. Worked example:
`out/edits/wind-motion-fix-20261008/` (`scratch/pass2.py`, `analyze_motion.py`).

```bash
node tools/parts/render.js --parts "<Effekseer tool folder>" --manifest out/parts/sources/packs/manifest.json --out out/parts/sources/packs
python tools/parts/strip.py out/parts/sources/packs
```

**Colour.** `colours.py` names each rendered part's dominant colour from its
thumbnail, weighting each lit pixel by how far it stands out from the ground.
Names alone miss most of it; plenty of green bursts are called `Node`.

```bash
python tools/parts/colours.py out/parts/all,out/parts/sprite out/parts/colours.json
```

**Two things to know when writing specs by hand.** Omitting `node` does not graft
the whole effect: it grafts the first root child only. For an effect with several
root children, list each as its own part with the same delay, scale and offset.
And align is wrong for anything meant to unfold. It lands every peak on one frame,
which suits an impact; a three-part test collapsed into a single flash at frame
54 with nothing after 66. A sequence needs its delays set from the source effects'
own filmstrips.

**Trial, 2026-09-13.** Seven effects were built from parts of 17 source effects
across six packs and the stock set, specs in `out/parts/specs/trial/`:

| Effect | Built from | Parts |
|---|---|---|
| Column of light, explosion at the end | stock light pillar, stock explosion, MCG light burst | 3 |
| Planetary grand cross convergence | four Astrology zodiac planets, MCG charge, 3D pack cross, MCG burst | 7 |
| Repeated green energy explosion | Stormforge vacuum burst three times, stock poison cloud three times | 15 |
| Starred recovery | stock heal, Sanctuary revitalize | 4 |
| Horizontal energy beam | MCG charge, Blast charge beam, MCG burst at the tip | 3 |
| Vertical energy beam | MCG charge, Blast focus beam, stock explosion at impact | 5 |
| Ice explosion | Frostforge flash freeze, stock ice AoE, 3D pack ice streaks | 3 |

All seven assembled and verified first time and drew. On the first filmstrip
review three read as intended straight away. Four needed one round of tuning,
all of it spec values rather than code: identical planet signs, repeated bursts
too close in place and time to read as repeated, a star burst landing late, and
an ice crystal outgrowing the frame.

**Playing them in RPGReactor.** The seven are installed in a separate project,
`<your RPGReactor test project>`, as animations 151 to 157
named `Assembled: ...`, each effect in its own folder under `effects/Assembled/`.
`SCRATCH PROJECT` was not used, because its `effects` folder is a junction into
the source repository's `template/Demo/effects`, which is tracked on `main`. The
test project is a copy of the scratch project with a real `effects` folder. Its
`3d`, `audio`, `movies`, `img/fogs` and `img/parallaxes` are still junctions into
the Demo template, so treat those as read-only and never delete the project with
a tool that follows junctions. The scratch tools do not know about it, so a
scratch rebuild neither updates nor removes it. Rendered from their installed
location, all seven loaded with no missing files and matched the trial render
pixel for pixel.

**Second round: one art series per effect.** Played in RPGReactor, only the ice
explosion satisfied. The problem was not colour or timing but drawing style
mixing across sources, plus some poor part choices, such as zodiac-sign orbs
standing in for planets and stock poison clouds beside Stormforge wind. The
other six were rebuilt so that every part comes from one artist's series, chosen
after rendering 46 candidate source effects as filmstrips. Specs are in
`out/parts/specs/trial2/`; all six are installed alongside the first versions as
animations 158 to 163, named `Assembled (series): ...`, under
`effects/Assembled/Series/`.

| Effect | Series | Parts used |
|---|---|---|
| Column burst | MCG Holy | Holy Ascent column, Holy Impact burst |
| Grand cross | AstrorogeVFX | Selene Grace moons, Astroforge Blast planets, Event Horizon black hole, Genesis Nova finale |
| Green burst chain | Stormforge | vacuum burst three times, Storm Conjuration glyph rings |
| Star recovery | Sanctuary | Resurrect star trail, Regenerate stars, Revitalize star cloud |
| Horizontal beam | Blast | energy beam shot, charge beam, single blast at the tip |
| Vertical beam | Blast | focus beam aiming rays, satellite beam, focus beam impact dome |

Many series effects paint a full-frame backdrop: a `RENDER BG` or `Color BG`
layer, a galaxy backdrop, a haze or a whiteout. Those root children were left
out deliberately; lifting a whole effect would have dragged them in. The only
tuning needed was shrinking Genesis Nova's star field, which otherwise filled
the frame and outlasted a 200-frame render.

The spec's `catalogue` is written as an absolute path, because a generated spec
that resolves wherever it is saved beats one that depends on its own depth.
`sheet.js` takes `--catalogue`, `--specdir`, `--outdir` and `--dll` if those
defaults are wrong.

The page's CSS and script are `sheet.css` and `sheet-page.js`, inlined at build
time so the output stays one self-contained file. Edit those, not the emitted
HTML.

**Verified end to end.** Three parts picked from the sheet by clicking
— an MCG lightning-ball emitter, a Death Dancer aura and an MCG impact glow —
produced a spec that assembled cleanly (12 nodes, verify OK, each part carrying
its own three ancestor links) and rendered.

**Measured, 2026-09-12.** Sprite was run as a second pass because it is two and
a half times the rest of the library put together.

| | Parts | Drew | Blank | Failed |
|---|---|---|---|---|
| Ring, Model, Track, Ribbon | 3271 | 2870 | 357 | 44 |
| Sprite | 8412 | 7332 | 1076 | 4 |
| Total | 11683 | 10202 | 1433 | 48 |

Assembly took about 1h40m for all of them and used 1.7 GB, because each part
keeps its own copy of the textures it needs. Rendering took 16 minutes.

Three things worth knowing before reading the sheet.

**Playing before the effect finishes loading fails silently.** `loadEffect`
fetches the effect and every texture it names. The first run of the page played
on the next tick and reported all twelve sample parts blank. The page now waits
for the load callback, with a timeout that is recorded rather than swallowed.

**Simulation runs every frame; drawing does not.** Drawing is the expensive half
under SwiftShader, so frames are sampled every third one. A part's brightest
moment is not sharp enough in time for that to cost anything real.

**A black ground hides a lot of the library.** Nearly a quarter of the parts that
show nothing on black are subtractive, which on black can only darken what is
already at the floor; alpha-blended parts with dark textures fare little better.
Anything blank on black is retried on grey, and "lit" means differing from the
background rather than being bright, or the retry would score zero for the same
reason. This recovers 1,048 parts, and the sheet tags them `grey bg`.

**Every part that draws is measured for size.** From the brightest frame the page
records a bounding box, the union box over the whole run, and the radius around
the effect origin that holds half and nine tenths of the drawn pixels, with a
flag when the part touches the edge of the frame, in which case its size is only
a lower bound. The radii are percentiles rather than box sides, so one stray spark
cannot inflate them, and they are centred on the origin because a spec's `scale`
is applied about the origin. Pixels are converted to effect units on the plane
through the origin, so the figure is an apparent size from this camera, which is
what matters when parts are seen together. Each card shows its 90% radius, and
the sheet can sort by it.

The measurement was checked for linearity by assembling the same part at scale
0.5, 1 and 2. For one part the 90% radius came out at 0.52, 1.0 and 2.0 times the
unscaled value; for another, 1.92 at double size. A small part scaled down can
fall below the visibility threshold and stop counting as drawn.

Across the library, 11.6% of the parts that draw touch the frame edge at their
peak, so their size is a lower bound. Track and Ribbon are clipped twice as
often as the rest, at about 21%, because trails run long. For the parts that fit,
the 90% radius in effect units spreads like this:

| Renderer | Parts | 10th pct | Median | 90th pct |
|---|---|---|---|---|
| Sprite | 6527 | 2.49 | 5.07 | 10.46 |
| Ring | 1311 | 2.69 | 5.38 | 11.08 |
| Model | 710 | 2.80 | 5.49 | 12.01 |
| Track | 386 | 3.42 | 6.58 | 11.60 |
| Ribbon | 89 | 3.73 | 6.73 | 12.32 |

A part at the 90th percentile is about four times the size of one at the 10th,
which is the mismatch two parts picked at random would typically show.

**A blank thumbnail is not proof of a dead part.** BackDistortion draws nothing
in this harness at all, which is a known limitation and not a broken part. A
part lifted with its chain but none of its siblings can also look nothing like
it does in its source effect, so a thumbnail is a hint rather than a promise.
Where the brightest frame lands in the last tenth of a part's run, the sheet
says "peak at end": the run may have been cut off rather than finished, and 830
parts are in that position.

**Backing the camera off was tried and does not help.** It is the obvious first
guess for a blank part, so it is worth recording that it was measured and
failed: every part that draws, draws at distance 40, and not one of 667 blanks
recovered at 140, 450 or 1400.

**48 parts fail to load, and the fault is in the source packs.** They carry
`.efkmat` file materials that name `Textures/Distortion01.png`, a path that
exists only in Effekseer's own `Sample/00_MaterialBasic` and in none of the packs
that ship those materials. The original effects fail the same way.

Chrome needs `--enable-unsafe-swiftshader`, which `render.js` passes. Headless
has no GPU, and without it WebGL is refused outright rather than falling back —
which looks exactly like every effect drawing nothing.

## Editing a copy of an effect

`edit` changes named values on a copy of an existing effect. It loads the whole
source (so project settings, procedural models and dynamic equations need no
repair), sets each value, copies resources beside the output, saves, then reads
every value back from the saved file and compares it with the request. The
source is only read. A path through any property marked `[IO(Export = false)]`
is refused, since those are silently dropped on save.

To stretch the whole effect's authored time before per-node `edits`, use a
top-level `retime` (unknown keys, including a misspelled `factor`, are errors):

```json
{ "source": "Comet.efkefc", "retime": { "factor": 2 }, "edits": [] }
```

`factor` must be finite and positive: 2 is half speed, 0.5 is double speed.
Life, generation intervals/offsets, fades, UV animation frame lengths,
alpha-cutoff transition frames and 1.8 collision lifetime reduction scale by
the factor. PVA/SinglePVA/AxisPVA velocities (including ring PVA) and every UV
scroll rate divide by it; PVA accelerations, Gravity and Force/Wind strengths
divide by its square. Random ranges are retained. Integer durations and key
frames round to nearest (ties to even); every non-exact rounding is printed.
Set `endFrame` explicitly to override the scaled editor preview end.

Time FCurves scale key frames, both handle X coordinates and the curve's random
start offset (`OffsetMin`/`OffsetMax`, which the runtime subtracts from the age,
so on a Time curve it is frames; `dump` prints it when set) while preserving
values, handle Y and interpolation, and use `Sampling` 1 through the same
helper as `keys`/`shift`. Percent keys and their Sampling remain unchanged:
they follow the stretched lifetime. Existing Percent keys outside 0..100 print
a warning because they never play. Saved curves are checked for frames,
values, handles, interpolation, Timeline and Sampling. Per-node edits run
after retime and can override it; a foreign `add` subtree arrives afterwards
and is not retimed, while an in-project copy inherits its already retimed source.

Unsupported controls print node/path warnings and remain unchanged: dynamic
equations, velocity-dependent Vortex/Turbulence/Drag/AttractiveForce strengths,
sound delays, parent-follow steering, NURBS motion and Track/Ribbon sampling.
1.8 GPU particles scale lifetime/offset, initial/angular velocity and gravity;
their discrete emission, damping, vortex and turbulence controls warn. Spatial
procedural noise frequencies/powers remain unchanged (neither core has a
procedural noise speed field). Infinite UV frame lengths remain infinite.
Setter clamping and rounded key collisions are reported. This is authored
retiming; a field or curve whose stretched numbers exceed the core's numeric
range prints a warning and remains unchanged while other fields continue.
With discrete spawn/update approximations, verify the saved dump and
live-instance trace. Preview-speed settings and game animation database sound
cues are outside this operation.

```bash
Niagara2Efk edit out/edits/ThunderFall-lightning.edit.json "<project>/effects/Edited/ThunderFall-lightning/ThunderFall.efkefc"
```

A spec names nodes by child index from the root (`"1/0/0"`, as in the
catalogue), optionally guarded by `expect` (the node's current name), and sets
values by EffekseerCore property path, which is also how the EDIT chunk spells
them: `"CommonValues/Life": 5`, `"CommonValues/GenerationTime": [2, 3]` (a
random range), `"LocationValues/Fixed/Location": [0, 12, 0]`,
`"DrawingValues/TrailColorCenter/Fixed": [255, 255, 255]`, and enums by integer
or name. A random colour range (the dump shows `rgba([73..128], 0, [84..128], 255)`)
is written with `"DrawingValues/ColorAll/Easing/Start": {"min": [110, 0, 0, 255],
"max": [255, 0, 0, 255]}`: four bytes each, ordered, 0..255. Keep the random
range on one channel: Effekseer draws each channel independently, so a range
across several can produce off-hue streaks. Decode the source with
`efkefc_dump.py` to find paths. Old files keep
colour in legacy spots (`DrawingValues/Sprite/ColorAll`,
`DrawingValues/Track/ColorCenter_*`); load migrates them, so read a no-op edit's
output for the live layout.

`Niagara2Efk dump <effect> [node ...]` prints every saved value of the chosen
nodes in that same path spelling, then the procedural models they use (see
[Procedural models](#procedural-models)). Beside each keyed FCurve it also prints
`.../Sampling` (1 follows the curve exactly; the default 10 steps through it)
and the curve's `.../Timeline`: `Percent`, the default, reads key frames as % of
the node's life, `Time` reads them as frames. Keys meant as frames on a Percent
curve play at the wrong time; set `Timeline` to `Time` in the same op.
Whole-number fields also take a JSON float with no fraction (`60.0`, as Python
writes it); `2.5` is refused.

An unknown key anywhere in a spec (`"ops"` for `"edits"`, `"sets"` for `"set"`,
`"frm"` in a `shift`) is an error that names the key, where it is, and the keys
that are allowed there; nothing is written. Before this, a misspelt key was
skipped and the edit reported `VERIFY OK` with 0 values set. Keys starting with
`//` are comments and are allowed at any level. Path keys inside `set` are
checked separately, when they are resolved against the node.

An op may also carry `"add": {"from": "<path>", "name": "..."}`: it appends a
copy of the `from` node (with its subtree) as the LAST child of `node`, through
the editor's own clipboard, so no existing index shifts. `expect` then guards
the parent, `set`/`tint` apply to the new copy, and later ops address it as
`<node>/<old child count>`. A path value of `""` clears that resource slot.
An op's `"shift": [{"curve": "ScalingValues/SingleFCurve/S", "from": 21, "by": 40}]`
moves every key of that FCurve at or after frame `from` by `by` frames, handles
included, and verifies the saved key frames (`out/edits/shineforge_sequence.py`).
An op's `"keys": [{"curve": "LocationValues/LocationFCurve/FCurve/Y", "points": [[0, 0], [12, 5]]}]`
replaces every key of a float FCurve with straight lines through `points`
(`[frame, value]`) and verifies frames and values. It also takes a colour channel
(`DrawingValues/ColorAll/FCurve/FCurve/R`, `G`, `B`, `A`: int curves, 0..255), whole
values only; a fraction is refused rather than rounded.

Both write curves the runtime plays exactly, and refuse the two ways a curve
silently plays something else:

- **Sampling.** A saved curve reaches the runtime as samples every `Sampling`
  units from its first key, joined by straight lines (EffekseerCore
  `FCurve.GetBytes`, runtime `FCurve::GetValue`; the editor previews the same
  data). At the default 10 a key between samples is smoothed away: a pop at
  frame 1 became a 10-frame ramp, a hold that ended at 42 started shrinking at 40.
  `keys` and `shift` set the curve's `Sampling` to 1 (read back like any `set`
  value) unless the same op sets `<curve>/Sampling` itself.
- **Percent timeline.** On a `Timeline` `Percent` curve the key frames are % of
  the node's life, and keys outside 0..100 never play. `keys` refuses points
  outside 0..100 there; `shift` refuses unless the node lives exactly 100 frames
  (1% = 1 frame) and every moved key stays within 0..100. The fix is to set
  `"<group>/Timeline": "Time"` in the same op's `set` (applied before `shift` and
  `keys`), so every key counts in frames; `"percentOfLife": true` on a shift says
  `by` is meant as percent. A fresh location FCurve defaults to Percent. The
  Shineforge sequence lost its orb fade this way (life 280, keys moved to 255 and
  280 per cent); the installed copies have `-timing` variants
  (`out/edits/timing-fix-20261001`).
A spec's top-level `"endFrame": N` sets the Effekseer editor's preview range end
(`Core.EndFrame`, saved in the EDIT chunk only; the runtime ignores it). Pack
effects save it at their own length (Lightforge: 120, looping), so the editor
preview of a LENGTHENED effect stops or loops early unless this is raised. A copy placed under the same controller follows the
same particle, which is how to layer a glow, a core and filaments on one
projectile. Worked example: `out/edits/radiant_trails.py`, which writes the
`RadiantShower-trails-*` specs.

### Procedural models

A procedural model is a mesh the runtime builds from numbers (a sphere, cone,
cylinder or a `Spline4` profile spun round an axis, or `Ribbon` strands, with
shape noise and a 3×3 vertex-colour grid), so there is no `.efkmodel` file to
ship. Models live in a project-level list (`Core.ProceduralModel`, the editor's
"Procedural Model" window), and a node's `DrawingValues/Model/Reference` (or its
spawn shape, `GenerationLocationValues/Model/Reference`) holds only an index into
it. The packs lean on them: 1,226 Model-renderer nodes in the catalogue use one,
664 use a model file.

A spec's top-level `"procedural"` list changes or creates models. It runs before
`edits`, so a node can reference a model the same spec creates:

```json
{ "source": "Base.efkefc",
  "procedural": [
    { "add": "BeamTube", "set": { "Shape/PrimitiveType": "Spline4",
        "Shape/Point1": [0.2, 0], "Shape/Point2": [0.5, 0.3], "Shape/Point3": [0.5, 0.7],
        "Shape/Point4": [0.2, 1], "Mesh/Divisions/X": 24,
        "VertexColor/ColorLowerCenter": [255, 200, 80, 255] } },
    { "add": "BeamTubeWide", "from": 0, "expect": "BeamTube", "set": { "Shape/Point2": [0.8, 0.3] } },
    { "index": 0, "set": { "ShapeNoise/WaveNoisePower": [0.05, 0, 0.05] } } ],
  "edits": [ { "node": "0", "set": { "DrawingValues/Type": "Model",
      "DrawingValues/Model/ModelReference": "ProceduralModel",
      "DrawingValues/Model/Reference": "BeamTube" } } ] }
```

- `"index": N` changes model N; `expect` guards its current name.
- `"add": "<name>"` appends a new model (so no existing index shifts), from the
  defaults (a Mesh sphere, 10×10 divisions) or, with `"from": N`, as a copy of
  model N (`expect` then guards model N). A copy is how to change one node's
  shape without changing every other node that shares the original. Names must
  be unique; an `add` whose name already exists is refused.
- Values use the model's own property paths, the ones `dump` prints under
  `### procedural <i> <name>`: `"Shape/Point2": [0.9, 0.08]`,
  `"VertexColor/ColorLowerCenter": [255, 255, 255, 255]`, `"Mesh/Divisions/X": 24`.
- A node's `.../Model/Reference` takes a model index or a model name. A name must
  match exactly one model (the error lists the project's models otherwise). The
  reference only draws with `.../ModelReference` `ProceduralModel` and the group's
  `Type` `Model`; a reference set without both prints a `WARNING`, as does an
  added model no node references.

For a Spline4 mesh the points are (radius, depth), the profile is a natural cubic
through all four (vertex rows spaced by distance along the control polygon), spun round
the axis; with the model's `UV/UVSize` = (Divisions X - 1, Divisions Y - 1) each
facet quad gets one copy of the texture, so a cell texture drawing its borders and its
bottom-left to top-right diagonal draws the mesh's own wireframe
(`out/edits/procedural-models/wirecrystal`); vertex colour "Lower" is the depth-0 end (UV v = 1 - depth). Worked
example of changing one in place: `out/edits/ReferenceBeam/reference_beam_study.py`,
`ObliteratingArray-shaped`.

**How a texture lands on a procedural model** (runtime
`ProceduralModelGenerator.cpp`). On a Mesh, U runs around the swept arc
(`Mesh/AngleBeginEnd`) and V along the profile, 1 at the first point and 0 at
the last; the texture wraps once around whatever arc is swept, and
`Mesh/Rotate` turns it into a spiral. A Ribbon strand is a strip like a trail:
U across its width, V along its length; `RibbonScales` tapers it,
`RibbonAngles` twists it, and the `Point` cross-section builds no triangles at
all. Vertex colours follow the same U,V (Left/Right are a strip's edges), and
the model's `UV/UVSize` and `UV/UVPosition` remap U,V (`uv * size + position`).
Shape noise is baked once, so a tendril cannot wriggle on its own. For a
texture with a shape on it: crop its empty margins with `UV/UVSize` and
`UV/UVPosition`, set repeats to strip length / (strip width x tile
width:height) or the shape stretches out of recognition, and show a single
sprite once with a partial sweep (a curved card) rather than a full 0..360
wrap. Detail finer than about a tenth of a strip's width does not survive the
battle camera. Experiments and the packs' usage: `out/edits/procedural-models/shapes/SHAPES.md`.

**Further procedural findings** (Codex's lab, `out/edits/codex-procedural-lab/REPORT.md`,
checked against its files): a spawn shape on a procedural model
(`GenerationLocationValues/Type` Model, `Model/ModelReference` ProceduralModel)
puts particles at random points on the triangles (`Random`), on vertices
(`Vertex`/`RandomVertex`) or at triangle centres (`Face`/`RandomFace`), with no
edge mode. `VertexColorNoise` shifts the vertex colour channels only (never
position or alpha), so equal power on all three channels gives rainbow patches;
put power on one channel for single-hue mottling. A per-facet wireframe without
the diagonal (a quad grid) reads better on spheres and cylinders; the diagonal
suits faceted shapes. A flipbook on a model keeps the model's own UV crop, so
animate atlas cells with the full model UV and a flipbook. A shaped slash sprite
scrolled along a partial-sweep arc does not read as a swing; use a band texture
for scrolling, or the sprite once, fitted.

An `add` op with `"file"` (a node copied out of another effect) carries the
procedural models that subtree references: each definition is appended to this
effect's list and the pasted references are repointed (the assembler's repair).
Identical definitions carried by several adds in one spec share one model.
Subtrees using dynamic equations are still refused; use the assembler for those.

`dump` prints a node's reference as `#<index> "<name>"` and each model once at
the end (a whole-effect dump lists every model; a node dump lists the models
those nodes use). Every edit now also checks, after saving, that the model list
and the definition each node's reference reaches are identical to what was
saved; it reports this on the `VERIFY OK` line.

### Building an effect from scratch

`tools/author/efkauthor.py` packages these lessons as helpers for writing such
specs (standard library only; test: `python tools/author/test_efkauthor.py --work
out/tests/author`). A `Spec` starts from a `blank` base; `Spec.fresh` adds a part
copied from the blank file (inherits nothing), `Spec.copy` copies a part of this
effect; helpers cover lifetimes (`once`, `emit`, `delayed`), placement
(`at`, `on_circle`, `ring_of`, `rising`, `floor_tilt`), motion (`spin`, `grow`,
`scale_keys`, `spin_keys`), renderers (`model_node`, `sprite`, `hidden`,
`scroll`, `flipbook`), shapes (`spline4`, `sphere`, `cylinder`, `flat_disc`,
`ribbon`, `noise`, `rows`, `fade_rows`, `soft_edges`, `uv`, `facet_uv`, `card`,
`repeats`) and `spline4_rows`, a port of the runtime's profile curve to preview
where a Spline4's vertex rows land. `Spec.build` runs `edit` and raises on
anything but `VERIFY OK`.

Lessons from building ten effects out of procedural models (crystal bursts
356-359, tornado 355, chain binds 360-364; scripts in
`out/edits/procedural-models/builds/` and `chainbind/`, notes in
`shapes/SHAPES.md`):

- **Start from `blank`, never `sprite`.** The sprite burst carries gravity
  (`LocationValues/PVA/Acceleration/Y` -0.08), random velocity, a shrinking
  random scale and a random life. Every copied node inherits them: lab shapes
  shrank to nothing, and rising flames and sparkles in two installed effects fell
  through the floor until the acceleration was zeroed.
- **`add` copies everything the source node has by then**, including settings
  meant only for it (a floor tilt laid a sigil card flat). Reset what a copy
  should not inherit, or copy a fresh node out of the blank file:
  `{"node": "root", "add": {"file": "blank.efkefc", "from": "0", "name": "..."}}`.
- **The battle camera looks straight ahead.** Floor discs and rings are tilted
  about 65 degrees on X towards it, as the packs do; flat they are edge-on. Parts
  standing on the true floor never meet a tilted ring on screen, so parts that
  should start on the ring are children of a tilted, non-drawing controller
  (sprites stay upright, being billboards), and 3D parts come from above or
  around instead. The eye sits level with the target's feet, so at 65 degrees a
  ring shows 91% of its height and can read as a hoop standing behind the target;
  a ground ring under a column read as a floor ring at 25 degrees
  ([Procedural beams](#procedural-beams), side-by-side captures). Judge the tilt
  in battle, per effect.
- **Plane ribbons whose strip faces sideways are edge-on**: chains or tendrils
  radial to the camera vanish into lines. Use the `Cross` cross-section.
- **Fit shaped textures** (section above): crop margins with the model's UV,
  repeats = length / (width x tile aspect). Only then did chain links read.
- **A coloured texture tints badly**: a purple chain tinted gold turns brown.
  Use a desaturated, brightened copy for other palettes.
- **Flipbook sprites** (flames, sparkles): the drawn shape fills only part of
  each cell, so they need roughly 2-3x the scale you would guess;
  `DrawingValues/Sprite/Position` Fixed with LL/LR at y 0 and UL/UR at y 1 stands
  each one on its spawn point. Stop emitting so the last ones die with the rest
  of the effect, or stragglers outlive it.
- **Dark versions**: dark parts are Blend (additive dark is invisible); give
  them violet tips or rims and check on grey and on black. Wide dark ribbons at
  the same radius as the subject bury it: keep them thin, transparent and
  outside.
- **Brightness stacks**: several additive layers in one place (holy, lightning)
  turn the centre white and swallow a sigil; it reads in the moment it pops in.
- **Lightning**: shape noise is baked once, so flicker comes from a flipbook or
  from re-spawning a bolt at a random turn; a strike from the sky needs to be
  long enough to leave the top of the screen; a glow for a re-spawned bolt must
  be its child. Zigzag bolt strands: see § Procedural lightning bolts.
- **Render with the game camera** (`tools/parts/gamecam/render.js --harness
  <project>/js/libs`) and check the live-instance trace reaches 0; then
  `tools/ingame/run.js` for the real first-cast freeze. Isolate a missing part
  with a debug copy where every other node has `IsRendered` false.
- **A Spline4 built from the defaults needs all four points**: one left unset
  keeps its default ((1,0) (1,0.5) (1,1) (1,2)) and bends the profile; `edit`
  now warns. (A `#` comment in a generator script swallowed `Point4` and folded a
  crystal's top into a bowl.) `edit` writes its log beside the spec, so building
  straight into a game project leaves only the effect and its textures there;
  `tools/install/install.py` does the whole checked install.
- **Regenerating a texture under the same name** next to an earlier build makes
  the resource copier keep both (hash suffix) and the read-back fail; the
  message now says so. Delete the stale copy or rename the texture.

**Track facts behind that example** (1.70 runtime,
`EffekseerRenderer.TrackRendererBase.h`): index 0 is the OLDEST point, so
`TrackSizeFor` is the tail end and `TrackSizeBack` the head; the non-`Middle`
colours apply at both ends and `*Middle` at the midpoint; colour easing runs
per point over that point's own age, which is what fades a tail from head to
tail. Left/Right colours are multiplied by the colour texture, so over a soft
bar such as `DCSD_GradientV_002` an edge-only tint is invisible.

A spec with no edits is byte-identical, in all three chunks, to Effekseer's own
resave of the source with its resources present. Specs and a before/after strip
for the first job are in `out/edits/`; `render.js --thumb 320` saves full-size
frames for judging detail.

### Making a Thunderforge bolt read as lightning

The Thunderforge pack draws all its electricity as thin wispy tendrils; no effect in
it has a conventional bolt. At the game camera, `EVFXForge03_06_ThunderFall` node 1's
bolt reads as a thin wisp, and straightening or widening it naively gives flat white
slabs or a smooth beam. Worked out on that bolt (specs in `out/edits/`:
`ThunderFall-lightning`, `-fluid`, `-goldarcs`, `-goldarcs-jagged`):

- **A Track is the position history of its points, sampled once per frame** from a
  moving parent. Speeding the fall gives a few corners, not a bolt. An instant bolt
  needs all its points spawned in one frame by a **Line** spawn (Type 4, Order).
- **Line `PositionNoize` moves points ALONG the line only.** Sideways kinks come from
  randomising `PositionStart`/`PositionEnd` X/Z, drawn per point.
- **Track width is multiplied by the node's scale**, which in this pack is a random
  easing that includes negatives. Fix the scale at 1 first; at camera distance 40 a
  width of 0.6 is invisible and 3 reads well.
- **Flat slabs come from segment length against ribbon width**, not orientation: a wide
  ribbon over many one-unit segments folds at every kink. Use fewer points (about 6)
  with SplineDivision 8 and TrailSmoothing on for long clean curves.
- **Jaggedness belongs in the shared falling path**: Movement Controller turbulence
  (`LocalForceField1/Type` 1, FieldScale 0.5, Power 3, Octave 2; stronger = Power 5,
  FieldScale 0.3, Octave 3 plus a Drag field, LocalForceField2 Type 7, Power 0.5, so
  arcs don't drift off the impact). A force field of Type None is off whatever its
  sub-settings say, and this pack fills in turbulence values on such nodes.
  Per-point jitter separates the core from the glow.
- **A sharp core inside a smooth glow reads as lightning; one smooth ribbon reads as
  rope.** The first bolts that looked like lightning at the game camera (thunder pilot,
  2026-09-28, `out/edits/thunderforge/rest.py`) used a thin zigzag core (width 0.25,
  16 points, SplineDivision 1) inside a wide soft glow (width 3, 8 points,
  SplineDivision 8), with Movement Controller turbulence Power 5, FieldScale 0.3,
  Octave 3 and a Drag field of Power 0.5. Six smoothed points on a single ribbon gave
  soft S-curved ropes.
- Track `*_Easing` colours in this pack are dead data (colour type Fixed); the
  visible colour is inherited from the parent controller.
- A texture on the procedural trail varies with every strike; a flat sprite repeats
  and ghosts when strikes overlap. Prefer the trail for anything that re-strikes.
- The harness is deterministic per batch slot: render each variant alone when
  comparing settings, or you compare different random paths.

### Procedural lightning bolts

A bolt built as a procedural Ribbon strand, carrying a bolt texture, replaced the
Track bolts above (2026-10-06; scripts in `out/edits/procedural-models/bolt/` and
`out/edits/lightning/sparkforge-procedural/thunderfall/`). The user's approved version
carries their own tiling bolt texture; the same approach (fit a procedural model
to a texture the user supplies) is the default for their future textures.

**Tools.** `tools/author/efkbolts.py` packages everything below: `make_kit` builds
a bolt kit effect from `blank` (three strike strands with their forks, foot
flashes and sparks, plus a free arc with its base spark; glows are generated, no
pack texture needed), and `strikes()` / `arcs()` / `arc_spark()` return `edit` ops
that copy those parts into any effect (a pack effect whose own bolt you hide, or a
new one). Helpers: `centred` (a copy of a tiling texture padded so its line,
measured where it crosses the tile edges, sits in the middle; the kit widens each
strip by the padding factor), `side_fade` (strips fade to transparent at their
sides), `remap` (recolour by
brightness ramp), `rng` (one-channel colour range, refuses more), `bolt_model`,
`fork_model`, and texture cleaning (below): `haze_cut`, `seamless_y`, `soft_mask`. `make_kit(...,
burst=<soft_mask card>)` adds a Strike Burst to every bolt (child 4): one branching-discharge card at the foot per
strike, turned at random (`RotatedBillboard`), one frame late because the bolt fades in over its first frame. `widen`
(default 1) makes every strand that many times wider for drawings wider than 128 px (the kit fits 128 px across a
2.2-unit strand; the user's 256-wide "-12_3" bolts came out squeezed, the thin one lost). A widened strand's edge
vertices lie far from the drawn line, so its foot flash is then ONE glow at the foot (`WIDE_FLASH` x the plain
factor: three edge glows spread into a haze, then stood apart as spots) and its sparks start there too.
Test: `python tools/author/test_efkbolts.py --work out/tests/bolts`.

`tools/ingame/lightning_audit.py` checks the rules in a real battle: it captures
every frame on a one-enemy troop (default 4) and flags `bare-ground` (a line
reaching the ground line with no impact glow, 33x33 mean of the added light under
60) and `lone-line` (a line with no spark or impact near it). Calibrated on
374-style bare strikes and on Static Flare without its base sparks (both caught;
the fixed versions pass). Pass `--ground Y` for strike effects (210 for troop 4 in
the ASSEMBLER TEST PROJECT): in a good strike the foot sits in its flash, so the
ground line is often not found automatically, and the report then says the ground
was NOT checked. Leave it out for floating effects (static clouds can sit below
the target's feet). It writes `audit.json` and a sheet of every flagged frame,
circled; exit 1 on any violation. Test: `python tools/ingame/test_lightning_audit.py`.
It captures, judges and then deletes the raw frames one effect at a time. Each frame is an uncompressed 0.9 MB
`.rgba`, so a run over the 23 lightning effects used to leave 3.4 GB behind, and one day's runs about 20 GB.
Pass `--keep-frames` to keep them, for GIFs, measurements or a later `--frames` run. Frames read with
`--frames` are never deleted.

**`--isolate` judges only our lightning.** A rebuilt effect keeps pack parts that
the line finder also sees as lines: starburst rays, impact debris, crack rings, a
falling arrow, static crackle. Without `--isolate`, most rebuilds "failed" on those,
and every report needed a list of excused flags. With `--isolate`, each effect also
plays as a copy where only the nodes drawing a bolt texture show (`--keep`, a regex
on texture file names; by default the user's bolt drawings, their recolours and
padded copies, and the kit's core strip). Lines are found in that copy, but light
(flash, explosion, spark) is measured in the full play, so a pack explosion still
counts as an impact.

The two plays line up because an effect plays identically twice: two captures of
375 differ in 0.06% of pixels, which is the backdrop flicker. That only holds if the
copy draws the same random numbers:
- Blank a node by giving it a fully transparent texture. Do not hide it:
  `IsRendered` false skips the node's render setup and its random draws, which
  shifts everything after it (Levin Barrage had 27 bogus bare-ground frames that
  way).
- Switch nodes that draw through a material file to the default material first.
- Serve the copy's whole folder (`--map`), because the blank texture exists only
  there.

The report checks the alignment by position. It compares the share of our line
pixels that lie on a line edge in the full play with the same share 3 frames off.
Brightness can't be compared, because packs darken the screen with a Blend tint.
If the aligned share doesn't win by 8 points or reach 85%, the result is marked
unproven. On the lightning set, aligned plays scored 66-93% against 35-65%
shifted. If no line is found at all (the Aura's thin arcs), it says NOTHING JUDGED
rather than PASS. Negative control: 375's bolts-only copy played as the full effect
fails with 6 bare-ground and 5 lone-line frames.

`--isolate` also judges **fading** lines, which the normal check misses because it
only finds lines brighter than 170. In the copy, a line under that bar is judged if a
bright line stood in the same place within the last 12 frames: a bolt fading out.
This is safe only in the copy, where no pack lines are left. A fading line needs light
at its foot (or a lit point, for an arc) of at least 1.3 times its own brightness,
capped at the normal bar. Calibration on ThunderFall calm (403, 404):
- old version (bolt and light faded together): fails at f46-49;
- fixed version: passes.

Faint lines that never were bright are not judged, such as the loose drawn fragments
of a fork or dim chain pieces. Judging those flooded the report: 39 flags on Chain
Currents. Two more safeguards:
- pixels that already flicker before the effect starts (the party's idle animation)
  are ignored;
- light up to 80 px straight below a faint piece counts, because a fading bolt is
  found only in pieces and its weaker lower part can go undetected.

These flags carry `faint: true` in the report.
Measuring traps found while calibrating it: take the background from the frames
BEFORE the effect starts, as a per-pixel maximum (a minimum over all frames counted
the pulsing backdrop light as glow); a bolt texture's own halo is not an impact
(measure wider than it); a spark over a bright backdrop adds little light (also
count a near-white core in the frame itself); one arc is found in fragments (join
pieces within 3 px); and a capture can come back with a black backdrop (check
`frame0` brightness before trusting a threshold set on it). Known false alarms
(Levin Barrage, 2026-10-06): thin starburst rays (`DCSD_Slash_Motion` sprites) at an
impact read as lines touching the ground; lines inside a large white blast; and a
strike site far back on the field (its flash is drawn small, the 33x33 window is
not scaled by depth). More (Sparkforge Aura/Shoot, 2026-10-06): lightning drawn ON a lit shell (a B1 sphere) at
its fading edge, and a pack arrow whose shaft and trail fall on through the ground after the hit. Open the flagged
sheet before acting on a flag.

- **Sharp kinks come from the mesh.** A Ribbon strand has one vertex row per
  `1/Vertices` of its profile, straight quads between rows, and shape noise moves
  each finished vertex (`ProceduralModelGenerator.cpp`, RibbonGenerator). Few rows
  (14 over 9 units) plus curl noise that changes fast UP the bolt
  (`CurlNoiseFrequency` Y 1.4) and slowly ACROSS it (X/Z 0.25, so both edges of a
  row move together) give a polyline zigzag. `WaveNoise` X depends on height only
  and adds the overall wander; a different `CurlNoiseOffset` is a different bolt.
- **A Plane strand on a vertical profile faces the battle camera**: its width runs
  along X at sweep angle 0.
- **Plank trap**: the strip keeps its width along X, so a row pushed sideways by
  more than the row spacing becomes a horizontal plank. Keep the X noise below the
  row spacing, and never put a halo on a wide strip; spawn faint glow sprites on
  the bolt's own surface instead (child node, `GenerationLocationValues/Type`
  Model, Random). A texture that has its own glow needs no halo (sprites on it read
  as spots).
- **A tiling bolt texture rides the strand**: model `UV/UVSize` [1, 2] puts two
  tiles up the bolt, and a `UVPosition` V shift per bolt shape shows a different
  stretch. Measure where the bolt leaves the top and enters the bottom first.
- **Draw bolt textures additively.** A texture with a dark outline (the user's beam)
  drawn with normal blending gives a black-outlined bolt the user found awful; with
  `Add` the outline adds nothing and only the light shows.
- **Centre an off-centre line by padding the texture, never by sliding it.** An
  off-centre line lands beside the strike's flash. Sliding it with the UV scroll
  `Start/X` (texture u = model u + Start/width, `Parameter/UV.h` GetUV) wraps the
  texture round: branches on the far side come back at the strip's other edge and
  are cut by the strip's edge, so arcs "end or start in nothing mid-arc" (user,
  2026-10-06, seen close up in the editor). Pad a copy with empty pixels instead
  (`efkbolts.centred`) and widen the strip by the same factor so the line keeps its
  width; size the foot flash by that factor too, or its three glows separate.
- **Fade bolt strips at their sides.** A drawn bolt carries a soft haze over most of
  its tile, which a strip shows as a hard-edged band; vertex alpha 0 on the strip's
  Left/Right columns fades it out (a Plane strip has three vertices across, so the
  fade is linear from the centre line).
- **Fit the drawing to each arc's proportions; keep arc strands gently bent.** A
  fork or arc strand shows the whole tile along its length by default, so a short
  fork squashed the drawing 2.5x and its zigzag collapsed into smooth streaks; with
  6 rows and strong noise the strand also folded into one sharp V, and the two read
  as glossy blades (user, 2026-10-06). Set the UV scroll `Size/Y` to the tile
  height x `efkbolts.arc_tiles(length, width)` (= 2.5 length / width for the kit's
  strand) and let the texture carry the jaggedness (10 rows, curl 0.18).
- **A child draws one update after its parent**: a strike's foot flash missed the
  bolt's first frame. Fade the bolt in over 1 frame (life +1) so both arrive
  together.
- **Recolour a coloured texture by remapping it, not by tinting.** Node colour
  multiplies (yellow x violet = mud). Map each pixel's brightness (max channel)
  through a ramp, black -> hue -> white, so the core stays white.
- **The impact starts while the bolt is still coming down** (user rule, 2026-10-07; in the original ThunderFall the
  explosion starts at f11 while the bolts are still travelling). Instant strikes stood on the ground three frames
  before the pack's explosion: a violation the user saw although `lightning_audit` passed. `strikes(travel=3)` draws
  each strike down from the sky (scale-Y and location-Y FCurves on frame time: the top stays put, the base slides to
  the ground) and is timed so the effect's own impact starts during the travel. While it comes down the tip carries
  only a small white-hot spark (`Strike Head`, `HEAD_SIZE`, the arc spark texture, one at the foot): the full foot
  flash riding the tip read as the explosion falling with the bolt (user, 2026-10-07), and a small dim flash was still
  an unlit tip to the audit. The full flash opens on landing; it must not inherit the bolt's height scale (a flat
  disc), so it gets `ScaleEffectType` NotBind with its size x the strike's scale. Sparks and burst come a frame before
  landing, forks on landing; forks turn 10-35 degrees below horizontal (`FORK_TURN`; at 20-50 a fork from low on the
  bolt still reached the floor beside the flash). The strike burst rests ON the ground (`BURST_LIFT`): centred at the foot, half of it drew below the floor
  over the battle's status window. Check a strike's first frames by eye, not only with the audit.
- **A bolt fades out before its light, never with it** (user, 2026-10-07, ThunderFall calm f47).
  A bolt is brighter than a soft glow, so when the two fade over the same frames, the
  bolt ends up standing dim with no visible light under it. Make the bolt's fade end
  first, and let the foot light last a few frames longer. Measured as bolt brightness
  against the light at its foot: 44 against 40 failed, 45 against 107 passed. The pack's
  ThunderFall keeps a small bright point at the foot of its bolt to the very end.
  `lightning_audit --isolate` checks this (see below).
- **A bolt strikes INTO its explosion, not beside it** (user, 2026-10-07, ThunderFall calm seen in the editor: the
  bolt landed about a unit beside the pack's explosion). Two things move a kit bolt's visible foot off its node's
  origin. The strand's shape noise moves the foot itself: `bolt_foot(i, scale, lean)` computes where (a port of
  Effekseer's wave and curl noise, `shape_noise`; bolt 0 lands 1.56 units right), and `strikes(pin_feet=True)`
  moves each bolt back by that much. And the texture scrolling down the strip moves the drawn line's foot across
  the strip's width (the drawings wander over 21-55% of it): a bolt that HOLDS should not scroll. Measured in
  battle on ThunderFall calm: foot 13-25 px right of the explosion's centre and drifting, pinned and still 0-2 px.
  The kit's own foot flash spawns on the foot vertices and follows the noise, so pinning matters where a bolt must
  hit something else at the origin (a pack explosion). A child placed at a fixed point (the travelling tip spark, a
  foot point) must sit on the foot too, not at the bolt's origin: `foot_point(i, scale, lift)` (pinned without it,
  ThunderFall calm's foot glow ended 19 px beside the bolt).
- **Pinning a foot only works at one lean.** With the kit's old seeds (1, 2, 3) the foot sits 0.6-1.6 units off,
  so a random lean or turn swings it off the pin again. `FOOT_SEEDS` are noise seeds whose foot lands within 0.1 units
  of the origin (found by searching `shape_noise`). Build with `make_kit(seeds=FOOT_SEEDS)` and every strike can
  lean and turn at random while still hitting its spot. A strand's shape is built once per effect, so random lean and
  turn are the only per-cast variety in shape (user, 2026-10-07: "it's always the same shape").
- **Hold, don't machine-gun** (user, 2026-10-07: nine quick strikes read "too fast and chaotic" next to the pack's
  ThunderFall). `held_strike()` gives one strike that travels down, holds and fades. The drawing stands still. One
  set of forks and sparks on landing, then re-strikes as a CHILD of the bolt, so they overlay it at any random lean.
  `restrike_from` makes them flash a second drawing. The foot flash and a foot point outlast the bolt.
  `strikes(life=, flow=)` slows quick strikes instead (Levin: 10 frames, -12 px a frame).
- **A strike grows by reveal, not by scale.** Growing the strand from a short stub squashed the whole drawing into
  flat horizontal streaks with a glow behind them (user, 2026-10-07: "they start like in the screenshot and it looks
  a bit ugly"). A stub of 2% also let the tip glow fall alone for a frame ("glow travels without lightning"). Now the
  strand stands at full size where it lands from its first frame. An alpha flipbook (`reveal_texture` / `reveal`:
  travel+1 cells, one a frame, played once, so it holds the last cell, which carries the soft foot) uncovers it from
  the top. The tip spark (`head_keys`) rides the uncovered edge down the strand's own noisy path (`strand_point`).
  The glow is FAINT down the first segments (`HEAD_FAINT` alpha, "Strike Head Faint") and BRIGHT only on the last one
  into the landing ("Strike Head"), as the user asked: "transparent until the last segment".
- **A new random range reshuffles the whole effect.** Effekseer takes its random numbers in a fixed order per
  instance. Turning a fixed value into a range (or back) adds or removes a draw, and every random value after it
  changes: different sweeps, positions and turns everywhere. Chain Currents went from passing to 13 audit flags
  because of one random UV start. Widening or narrowing an existing range keeps the order. When an audit result
  flips after an unrelated edit, check for an added or removed range first.
- **A strip's bottom is a straight cut.** The bolt is drawn on a flat strip, and the strip ends square across its whole
  width. Tilted by the lean, half of that cut stands under the ground beside the foot, which the editor's camera
  shows from above. `make_kit(soft=SOFT_FOOT)` fades the strand's last 4% into the foot with a clamped alpha ramp
  (`soft_foot`), and `haze=35` cuts the drawings' faint haze, which otherwise showed the strip's outline. Raising the
  bolt instead (`HELD_LIFT`) only made the cut float. `out/edits/lightning/editor-view/view.html` plays an effect
  under the game's runtime from an editor-like camera with a ground grid. Render it headless with Chrome
  `--headless=new --enable-unsafe-swiftshader --virtual-time-budget=30000 --screenshot`; the browser pane's
  screenshots time out on it.
- **A strand's top half fades out; a beam from a source needs `fade_top=False`.** Every kit strand fades over its
  upper half (vertex alpha 0 on the top row), so a bolt from the sky melts into it. Turned on its side as a beam
  whose top sits on a lit source (Sparkforge Blast: held strikes at Z -(90 + rise), feet pinned off the left of the
  screen, revealed from the source outward), the drawing stopped ~6 units short of the source. `make_kit(...,
  fade_top=False)` keeps the strands solid to their top. Scaled up for a long beam the strip widens with it: at
  scale 3.6 it is ~8 units wide, and a drawing whose line swings across its strip (the user's LightningBolt_B) hung
  down to the floor. Use the centred drawing for long beams, and keep a beam's far end and its foot lights off the
  screen (hide the bolt's children 2, 3 and the Foot Point) when nothing lands there.
- **Strike timing**: three bolt shapes take turns, a new strike every 3 frames, each
  turned a little at random round Y (the Z noise then shows). A still bolt held for
  3 frames and then swapped reads as stutter; give each strike a life of 6 with a
  5-frame fade (strikes overlap instead of cutting) and scroll the texture down the
  strand (`UV` Scroll, Size = texture size, Speed Y -24) so every frame moves.
- **Never a bolt without an impact under it** (user rule): the last strikes must
  not outlive the impact. Give every strike its own flash and sparks as children
  of the bolt node (born and fading with it, the flash a frame longer), or make
  the bolt disappear from the ground upwards.
- **Put the strike flash on the bolt's real foot, not at the origin.** Shape noise
  moves the strand's bottom row too (up to ~1 unit here), so a flash at the node
  origin misses the tip. Spawn it with `GenerationLocationValues/Type` Model,
  `Model/Type` Vertex: the n-th child takes vertex n (`Model.cpp`
  GetEmitterFromVertex), and a Plane strand's vertices 0, 1, 2 are the left edge,
  centre and right edge of its bottom row. Three large soft flashes merge into one
  glow along the foot; small ones read as a row of beads.
- **Forks must not reach the floor bare**: keep them short (about 2 units) and
  20-50 degrees below horizontal, so one low enough to touch the floor lands inside
  the foot flash. Free-floating arcs (static, crackle) start from a small spark and
  point up or sideways.
- **Audit on a single target.** The test troop 1 has two goblins: RPG Maker plays
  the animation on each, the second 12 frames later beside the first, which looks
  like a stray bolt. `tools/ingame/run.js --troop 4` (one Treant) shows one strike;
  check every frame of the capture, not a GIF.
- **Rebuilding a pack effect with it**: hide the pack's bolt (`IsRendered` false on
  its drawing nodes) and copy the bolt nodes in with a foreign `add` from the bolt
  effect; their procedural models come along. Test a variant in battle without
  installing: `tools/ingame/run.js --sub <installed name>.efkefc=<variant file>`.
- In the test project's troop 1 (two goblins), RPG Maker plays an animation on
  each target, the second 12 frames later: captures show two strikes.
- **A long free arc needs a core.** The user's drawn bolt is a thin line: stretched to 14 units it read as faint
  grey wisps. A child strand on the same model (same transform, scale X 0.4) with the soft core texture gives it a
  bright centre; at 1.8x the strip's width it became a fat glossy zigzag ribbon instead (Sparkforge Aura, 2026-10-06).
- **A whole-screen black-out in battle is the engine, not the effect.** The pack Sparkforge Aura blacked out the
  battle from f7 for 53 frames, every run, and hiding its sphere removed it. The sphere is a model with Culling Front;
  drawn last, it left CULL_FACE on and the engine's scene blit was culled (a Sub layer did the same through
  blendEquation). Fixed in the engine 2026-10-06 (RPGReactor PR #73; ASSEMBLER TEST PROJECT patched), after which the
  pack Aura plays without it (it was first blamed on its UV distortion, wrongly). Check a capture's corner
  brightness against frame 0; a capture black from frame 0 is the headless backdrop failing: rerun.
- **Clean a lightning texture before using it** (the user's Lightningrep set,
  2026-10-06; study in `out/edits/lightning/lightningrep-study/`). Additive drawing adds ALL
  of a texture's light. Measure the share that comes from dim pixels (<=128): C1
  had no true black (median 16) and 92% of its light was a grey cloud that coated a
  sphere in grey glass. `haze_cut(src, dst, floor)` sets everything under the floor
  to black and stretches the rest (C1: 40, since 60 also took most of its lines; B1's
  thin fog: 14). A tile that does not
  meet itself top to bottom shows a pinch travelling down a scrolling strand: tile
  it with itself and look for joins (the drawing may have been offset once already,
  hiding a second join mid-tile; Lightningrep_A had both, rows 255/0 and 127/128),
  then `seamless_y(src, dst, seams=(0, 128))` crossfades each join into a copy
  shifted by a quarter tile (24 rows each side; 48 smeared a double-exposed band). A texture whose lines run off every side (B1, C1) is
  cut along a hard rectangle on a flat card: `soft_mask` gives a copy faded to a
  circle; vary it by turning the card (`RotatedBillboard`, random Z every 2-3
  frames reads as a live discharge), not by UV (the mask would move). A disc's polar
  UV bends such lines into rings; spheres are fine with a slow scroll. A dense
  drawing reads as wool at battle size (A at ~80 px wide): keep it for a pillar body,
  not a thin bolt.

### Procedural beams

Beams built from procedural models (2026-10-06; study, scripts, captures and dumps
in `out/edits/procedural-beams/`, see its `REPORT.md`). Six archetypes were tried at
the game camera; then a vertical holy column and a piercing beam were built from the
best. Not installed yet.

- **Core + sheath reads as a solid beam; the others mostly do not.** A thin bright
  tube inside a wider, fainter one, scrolling the other way and slower, is the
  clearest beam. A single tube reads as a soft pipe and a Spline4 taper as a
  flared muzzle (past about 2:1 it reads as a comet). Ribbon strands wound round a
  beam read as a coiled spring (the camera sees the coils face-on), a wobbling tube
  as a rope, and the user's `LightningBeam_V` on a strand as a lightning bolt (its
  drawn core is about a fifth of the tile's width). That texture works as crackle
  on top of a tube.
- **Falloff gives a tube soft edges.** `AdvancedRendererCommonValuesValues/FalloffParam`
  (Model renderer only): `ColorBlendType` Mul, `BeginColor` white alpha 255,
  `EndColor` white alpha 0. The shader (`ad_model_unlit_ps.fx`) multiplies alpha by
  `mix(End, Begin, dot(-cameraFront, normal)^Pow)` with the dot clamped at 0, so the
  tube fades towards its silhouette, and back faces get the End colour, so only the
  front half draws. `Pow` cannot go below 1 (`Value.Float(1, 100, 1)`; the edit
  read-back catches it). Higher `Pow` softens further (2.5 for a faint rim). Like the
  other advanced settings, it does not draw on a node with a `.efkmat`.
- **Lay a beam with a non-drawing rig node, not `Shape/AxisType`.** Every beam part
  is a child of a hidden rig: a vertical column at (0, L, 0) rotated X 180, a beam
  from the right at (L, y, 0) rotated Z 90. Between two points, the rig sits at the
  source and is rotated on Z by atan2(-dx, dy) degrees, where (dx, dy) runs from
  source to target; its length is hypot(dx, dy). Growth is then scale Y on each
  part, and a part's own Y rotation spins it round the beam. `AxisType` only moves
  the model (not sprites or children), still needs a rotation to point the beam
  from right to left, and moves growth to scale X. The runtime applies it after
  shape noise and vertex colour.
- **UV on a tube and the seam.** U wraps once round, V runs along. V is 1 at the
  source (the first profile point), so texture v 0 lands at the target end, and a
  positive `UVScroll/Speed/Y` flows from source to target in both orientations. U 0
  (the seam) is at the model's +Z (`ProceduralModelGenerator.cpp`,
  `RotatorMeshGenerator::GetPosition`, angle 0 = +Z). A Z rotation leaves it facing
  the camera along the front of a horizontal beam; an X rotation turns it to the
  back. It shows only with a texture that does not tile in U: then set
  `Mesh/AngleBeginEnd` to (180, 540) (still a closed tube).
- **Soft ends are a length, not a fraction.** Alpha 0 on the Upper and Lower vertex
  rows with `VertexColor/ColorCenterArea` (0, 1 - 2 x end / length) fades both ends.
  4% of a 9-unit beam (0.37 units) left a hard square cut at the source; give each
  end about 1 unit. `CalculateVertexColor` assumes `ColorCenterPosition` 0.5 for
  both sides.
- **Grow, hold, retract.** Grow with a Time-timeline scale curve on Y (0.02 to 1
  over 6-8 frames). To retract into the target, shrink scale Y from 1 to s while
  sliding the part's origin along the beam by (1 - s) x L on the same keys
  (`LocationFCurve` Y): the far end stays put. A glow sprite under the rig with a
  location curve 0 to L over the grow frames rides the tip. A column can instead
  thin out (scale X/Z to 0.08), and its sheath can breathe (X/Z 1.07, 0.96, 1.06)
  during the hold.
- **Lit ends** (the lightning rule applies to beams): a source glow from frame 0, and
  an impact glow from the arrival frame that outlives the beam by a few frames.
  `lightning_audit.py` flags a tilted ground ring's outline as `bare-ground`; open
  the sheet before acting on it.
- **Sprites turn only as `RotatedBillboard`.** With `DrawingValues/Sprite/Billboard`
  `Billboard` (what `efkauthor.sprite()` sets), the runtime builds the quad from the
  camera's up and front alone and drops the particle's rotation
  (`EffekseerRenderer.CommonUtils.cpp`, the billboard switch). A Fixed or PVA Z
  rotation is saved, reads back fine, and is ignored. Spinning crescents and
  randomly turned glyphs need `RotatedBillboard`. A sprite's quad is square: give a
  2:1 texture a 2:1 scale.
- **A Ribbon strand stops one row short of its profile.** `RotatedWireMeshGenerator`
  adds rows `while (currentDepth < endDepth)` in steps of 1/Vertices from 0, so the
  last row sits at (Vertices - 1)/Vertices of the profile, with the full UV range
  spread over that shorter strand. A 10-vertex bolt strand of height 9 ends at 8.1; a
  2-vertex segment draws half its length. To make a strand span exactly L, give its
  profile L x Vertices/(Vertices - 1).
- **A strand can follow a texture's drawn line exactly.** Measure the line's centre in
  every row, simplify it to its corners (Douglas-Peucker), straighten a copy of the
  texture (each row shifted so the line sits at u 0.5), and build a chain of short
  straight Plane segments, one per corner-to-corner stretch, each rotated to that
  stretch and cropped (model UV) to its rows of the straightened texture. The drawing
  stays registered to the geometry, so animate by re-striking chains, not by UV scroll.
  This worked for the Coruscate crackle lines (drawn about 2x and offset from the beam
  axis to read); a branched drawing such as ThornStrand tears when straightened and
  reads better wrapped round the beam tube. Study: `out/edits/procedural-beams/obliterating/texlab/STUDY.md`.
- **A horizontal beam is seen at an angle.** The eye is about 10.8 units from the
  target, so a beam reaching far to the side shows its open end and any coils
  face-on near its source.
- **Fire from the caster, not from a fixed side.** In the ASSEMBLER TEST PROJECT
  (side view, 1280x720, `setActorHome` 600 + 32 i, 280 + 48 i), the leader's hand
  is about (6.35, 9.24) units from troop 4's enemy, up and to the right. A beam
  aimed there at 0.6 width, with the charge at 0.6 scale so it does not swallow the
  small actor sprite, matched the scene; a full-width level beam from x 17 did not.
  An animation plays at its target, so this offset holds only for this layout and
  enemy position.
- **Recolour by remap.** Generated textures are greyscale (tileable periodic noise
  so scrolling never shows a seam), and the user's coloured ones are remapped by
  brightness (`efkbolts.remap`). In battle, the four finished builds froze the first
  cast for 6-14 ms (12 runs) and ended at frames 87 (column) and 67 (beam).

### Trail effects (swing and sword trails)

Before changing a trail that looks doubled, hard-edged or too short, work
through [TRAIL-CHECKLIST.md](TRAIL-CHECKLIST.md). Check the causes in this
order: active layers, UV direction, RGB/alpha and blend mode, edge feathering,
material binding, then timing. Edit the texture or the timing only after all
of those. Measure how long the trail alone stays visible, separately from the
whole effect's lifetime (sparks lengthen it). Count it in simulated updates,
not from a slowed preview. A softer or sharper texture never changes the
trail's life. The checklist marks which points were measured on one Effekseer
Track effect. The (1−V, U) streak mapping and its frame counts are examples
from that effect, not rules.

## Checking curve timing

`lint` reads one effect or recursively checks a folder without saving effects:

```bash
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll lint "../ASSEMBLER TEST PROJECT/effects/Edited" --catalogue out/catalogue.json --json out/edits/my-lint/report.json
```

It reports keys off the Sampling grid (relative to the first key) and Percent
keys outside 0..100, naming the node, curve, Timeline, Sampling and offending
keys. For off-grid curves it also gives the largest difference between the
authored curve (Effekseer's own `GetValue`, Bezier included) and what the
runtime plays, at every integer curve unit; JSON adds intended/played values at
each off-grid key, offsets, full key/handle data and unreadable files. The last
key is checked against the grid too, though it is always sampled, so such a
finding can show zero difference. Lint does not guess whether Percent keys were
meant as frames.

The played model is the runtime's, not a plain join of the samples: when the
curve length isn't a multiple of Sampling, the final, shorter interval still
plays spread over a full Sampling and then snaps to the last key on its frame
(the short-interval branch in `FCurve::GetValue` is unreachable in both pinned
runtimes). These are curve measurements, not a substitute for rendering.

With `--catalogue`, an identical keys + Sampling + Timeline signature anywhere
in the catalogue marks the curve `pack`; otherwise it is `ours`. Key identity
includes interpolation and handles, but not node/path names or random offsets.
Signatures are cached by source path, size and UTC mtime in `lint-cache.json`
beside the JSON report, or beside the catalogue when no JSON report is given.
Changing either reader DLL invalidates the cache. Each effect loads in a fresh
worker process; an unreadable file is retried with the other build when that
DLL is available in the regular or `--artifacts-path` build layout. Build both
versions first to enable that fallback. Unreadable files are listed, not fatal.
Exit 0 means no `ours` findings, 1 means findings, and 2 means a usage error.

Exit 0 therefore says nothing about effects that could not be read. When any
target effect is unreadable, or a folder holds no `.efkefc` at all, lint ends
with a `LINT INCOMPLETE:` line saying how many of the targets the findings
cover, and the JSON report has `Complete: false` with `TargetEffects` and
`UnreadableEffects`. `--strict` makes that exit 3 (even when there are also
findings), for callers that need every target read, such as a check before
installing. Unreadable *corpus* files are only counted: they can at most turn a
`pack` curve into `ours`, never hide a finding, and one corpus (heavencloud) is
known to be unloadable, so they do not trigger `--strict`.

## Letter sheets from a font

The POLISH Classical Quests and Results packs spell their words from four
1024×1024 sheets of 16 letters each (`DCP_Mask_UnlockAcceptComplete`,
`_AbandonDismiss`, `_VictoryDefeat`, `_SuccessFailure`), each with a `_Blur`
partner for the glow. `tools/fontatlas/make_atlases.py` draws all eight from
any TTF/OTF under the same names, cell order and blank slot, so a copy of the
pack's `effects/` folder with these dropped into its `Texture/` shows the new
font with no effect edits. Keep one font per `Texture/` folder: the names
match on purpose. The packs have capitals and `!` only.

The font must contain all 24 of those characters itself. Before writing
anything, the tool reads the font's Unicode cmap (with fontTools when it is
installed; otherwise it compares each glyph with what the font draws for an
unmapped code point) and refuses, exit 1, with every missing or empty character
and the sheet cells that use it. Without this check a missing `Z` or `!` drew
the font's `.notdef` box in its cell and passed every other check. A character
mapped to glyph 0 counts as missing. Characters that draw the identical glyph
(compared against every capital the font has, including ones the sheets do not
use) are refused too: that is how demo and placeholder fonts stand in for
letters they lack. On the 336 fonts installed here, no complete font was
refused this way. A single letter drawn as some other unique shape cannot be
detected, so still look at `preview.png`. `manifest.json` records the method
under `characterCoverage`.

```bash
python tools/fontatlas/make_atlases.py --font "<font.ttf>" --out out/edits/<task>/sherlock
```

Defaults are measured from the pack's own sheets (read from
`--reference-root`, default `<Effekseer tool folder>`; built-in values
if absent). The `_Blur` sheets are a Gaussian blur of the sharp ones at radius
3.0, gain 1.0, which reproduces the pack's to 0.15 grey levels (`--blur`).
Letters are sized and placed so the capital top and baseline of the flat
letters (E, T, I, ...) land where the pack's do, 62 and 193 px of a 256 px cell
(`--fit pack`); a font whose widest glyph would not fit shrinks as a whole, and
`manifest.json` records it. `--fit cell --blur 7` is the earlier rule (widest
glyph fills the cell, ink centred, softer glow), which made letters 11% smaller
and 20 px higher in one font and 19% larger and 12 px lower in another.
Width and stroke weight stay the font's own. Every sheet is read back
(size, opaque, grey) and checked against the pack's cell occupancy;
`preview.png` shows the saved cells. Review and in-effect renders of Victory
and Success: `out/edits/fontatlas-review-20261001/`.

## Render harness

`tools/render-harness/` plays effects through the project's own
`effekseer.min.js` + `effekseer.wasm`, side by side with a stock effect as a
control, and counts lit pixels so the result is a measurement rather than an
eyeball check. Serve it over HTTP (the wasm will not load from `file://`):

```bash
python -m http.server 8731 --bind 127.0.0.1
```

Then open `http://127.0.0.1:8731/`. Drop any `.efkefc` plus its textures into
that folder and edit the `SLOTS` list at the top of the script to point at it.

## In-game test tool

`tools/ingame/` answers "how does this effect run in the game", which the
render harness cannot: the harness plays effects outside the engine on
SwiftShader, so it never sees load stalls, the engine's compositing or the real
GPU. `run.js` serves a game project read-only with `page.js` injected, opens
one fresh headless Chrome per job on the hardware GPU (ANGLE D3D11), starts a
battle and plays the animation on the troop.

```bash
node tools/ingame/run.js --project "../ASSEMBLER TEST PROJECT" --out out/edits/<task>/ingame --ids 317,318 --repeat 3
```

Time mode (default) plays the animation twice. The first play reports
`worstFrameDuringLoadMs`, the freeze a player sees on the first cast (texture
decode and upload, model parse, material shader compile all run on the main
thread). It also reports `loadMs`, `gameFrames` until the animation ended,
`framesOver20ms` while playing, Effekseer update cost and peak live instances.
The second play is cached and times the draw with `gl.finish` around it. A
stock warm-up animation (`--warmup`, default 1) plays first so the runtime's
one-time start-up cost is not charged to the effect.

Capture mode (`--mode capture`, `--step N`, `--shots N`) saves what the player
sees every Nth game frame as raw RGBA, at half resolution. While an effect plays,
the Effekseer overlay canvas holds the whole composited frame. The game runs
exactly one update per browser frame, so two runs of the same files match
within a small noise floor. `compare.js` counts differing pixels per frame
against a base run, with a second identical run as the noise floor:

```bash
node tools/ingame/run.js --project "../ASSEMBLER TEST PROJECT" --out out/edits/<task>/cap --mode capture --ids 319 --repeat 2
node tools/ingame/run.js --project "../ASSEMBLER TEST PROJECT" --out out/edits/<task>/cap --mode capture --ids 319 --sub "SmallMeteor-DissolveMask.png=<flat 4x4 png>"
node tools/ingame/compare.js --out out/edits/<task>/cap --base capture-319-0 --noise capture-319-1 capture-319
```

Swapping files without touching the project: `--map Edited/Meteors/=<dir>`
serves a whole effects folder from elsewhere, `--sub <file name>=<file>`
replaces one texture/model by name (knockout tests: replace a texture with a
flat image of its average colour to see whether it matters), and
`--override /js/reactor_plugins.js=<file>` replaces any served file (for example
to run with and without a plugin). From Git Bash, run it with `MSYS_NO_PATHCONV=1`:
MSYS rewrites an argument that starts with `/` into a Windows path, so the override
key never matches and the run silently uses the project's own file (a test with an
empty `reactor_core.js` still played normally). Confirm an override took effect,
for example with a `console.warn` marker that shows in the results line's `console`.
A marker at the top of an engine script is logged before the tool's console hook
exists; put it in a function that runs during the battle. `--prepare "<js>"` runs after the new game
is set up and before the battle scene is created, in memory only. For example,
`$dataSkills[$gameParty.leader().attackSkillId()].animationId = 319;` makes the
party's attack use an animation. A jobs file sets any of these per job.
Results go to `<out>/results.jsonl`; each line also carries the console's
warnings and errors, the battle start time and the preload plugin's record.

Every results line has a `status` (`ok`, `warn` or `fail`) with its `failures`
and `warnings` (rules in `tools/ingame/verdict.js`). A job fails when it times
out, the page or engine throws, an effect fails to load or never finishes
loading, or the animation never starts or does not end. "Does not end" means
still playing `--max-frames` frames after its first frame (time mode, default
3000) or `--capture-seconds` after capture began (default 120). Raise them for
a genuinely long animation; a jobs file sets `maxFrames`/`captureSeconds` per
job, and the default `--timeout` grows to fit. Loading has its own allowance:
not started 3000 frames after the request is "never started". A corrupt
`.efkefc` makes the runtime answer neither load nor error, so the game waits
forever; that is reported as "never finished loading". Console errors and the
preload plugin timing out are warnings. All jobs run even after one fails; the
last line is `INGAME: N job(s), ... failed`. `run.js` exits 0 when no job
failed (warnings included), 1 when any job failed, and 2 for a usage error
(missing project or Chrome, busy port, a limit that is not a positive number).
A capture job first deletes the frame files (`f###.rgba`, `shot-f###.jpg`) an
earlier run with the same tag left in its folder, and says how many
(`clearedStaleFrames`), so a shorter rerun cannot leave stale frames behind.

`compare.js` checks each capture before it counts a pixel, against that run's
own line in `results.jsonl`. A capture is `UNUSABLE` when its record is missing
or failed, a frame file is empty, not width × height × 4 bytes, or a different
size from the base's, a frame the record says was captured is missing, an extra
frame file is present (left from an earlier run with the same tag), or its
backdrop differs from the base. An unusable run gets `null` pixel counts, not
0. An unusable base voids every comparison, and an unusable noise run (or one
whose length differs from the base's) leaves `framesAboveNoise` null. Two
complete captures of different length are a real result: `LENGTH DIFFERS`, and
the frames only one side has are listed in `compare.json`, not counted.
`compare.js` exits 0 when every capture is usable, 1 when any is unusable
(`compare.json` has `valid: false` and each run's `problems`), and 2 for a usage
error. `--allow-unrecorded` accepts captures without a `results.jsonl` line
(folders put together by hand): every frame file is still checked, the frame
step is inferred from the file names so a frame lost in the middle is caught,
and the run carries a warning that its end is unverified. A capture without a
record that is shorter or longer than the base stays unusable, since it may
simply have been cut off.

Things the tool handles that cost time to find:

- The engine pauses when its window has no focus (`SceneManager.isGameActive`
  is `document.hasFocus()`), and a headless or hidden page never has focus. The
  page forces it on. In Claude's browser pane the same applies, and Chrome also
  throttles a hidden pane, so frame times measured there are meaningless.
- The PIXI ticker keeps its own bound copy of `Graphics._onTick`; replacing it
  does nothing. Hook `Sprite_Animation.renderActive`, which the engine looks up
  by name after each render.
- The battle is held still once it starts (`BattleManager.update` and sound
  effects off). Otherwise turns run, sounds load and the test project's missing
  `Blow1.ogg` stops the game loop with a LoadError.
- The battle backdrop (a snapshot of the title screen) sometimes fails to load
  in a headless run, which turns a pixel comparison into nonsense. Every result
  records `sceneMean` before the animation; `compare.js` flags a run whose
  backdrop differs as `UNUSABLE` (exit 1), and that run must be repeated.
- Sampling every 3rd frame misses short events. The small-rock breakups in
  Extinction Event last three frames and fall between samples: the dissolve mask
  looked unused at `--step 3` and clearly matters at `--step 1`. Use step 1
  before concluding that something has no effect.

Measured with it on the meteor effects (`out/edits/meteor-texsize-20261003/REPORT.md`):
2048 rock maps froze the first cast 218-473 ms against 24-42 ms for the pack
originals; 512 maps cut it to 79-133 ms with no visible change. The rest is
models and shader compile, which `plugins/ReactorEffectPreload.js` moves out
of the spell.

## Installing an effect into a game project

`tools/install/install.py` installs an effect built by an `edit` spec, with its
animation entry, in one checked step:

```bash
python tools/install/install.py --project "../ASSEMBLER TEST PROJECT" --spec out/edits/<task>/X.edit.json --dest Edited/<Folder>/X --name "Edited: X" --ingame
```

It builds the spec into a staging folder (`out/install/<name>`, `--work`) and
requires `VERIFY OK`, runs `lint --strict --catalogue`, refuses if the effect
already exists (`--replace` reinstalls and keeps its animation), builds straight
into `<project>/effects/<dest>.efkefc` (textures beside it in the folder's shared
`Texture/`) and requires that to be byte-identical to the staged build. It then
appends the animation to `data/Animations.json` in the file's own one-entry-per-line
format, re-reading the file just before writing and refusing if another session
changed it meanwhile, and with `--ingame` plays it twice in a real battle with
`tools/ingame/run.js`. A regenerated texture that clashes with a stale same-named
copy in the project fails the read-back with an explanation. `--tool` picks the
`Niagara2Efk.dll`. Its test: `python tools/install/test_install.py --work out/tests/install`.

Before building, it checks that every file the spec names is on disk (its `source` effect, `add.file` sources, and
the textures, models and materials in its values) and refuses with the list (exit 2) when one is missing. The edit
tool itself only reports `exit 1`. Two ways to get there (2026-10-10): a spec written by a post-step builds on the
effect that step read (OT2 Boost's `boost_light.py` on `Lv<n>-flash.efkefc`, which the build script deleted), and a
`build_effekseer.py` spec names the bakes in its work folder's `tex/` and `mesh/`, not the copies in `build/`: those
folders are inputs of the spec, not leftovers, and recycling them leaves the built effect playable but no longer
installable from its spec.

To move an effect to another folder, install it at the new `--dest` with `--id N` (instead of `--name`): existing
animation N is pointed at it and keeps its name, sounds, flashes and timings; refused if another animation already
plays that effect. The old file stays where it was until you delete it (check first that no animation names it). A
spec that no longer rebuilds the installed file byte for byte (an older tool build) is better moved by copying the
`.efkefc` and the resources it names, unchanged. The 25 lightning effects moved into `Edited/Lightning` this way
(2026-10-07, `out/edits/lightning/move-to-lightning/move.py`): 24 with `--id`, Hit Thunder 306 by copying.

## Battle effect preload plugin

`plugins/ReactorEffectPreload.js` is an RPGReactor plugin. When a battle scene
is created it loads the Effekseer effects that battle can play: the party's
attacks, skills and battle items, the troop enemies' actions, Show Animation
commands in the troop's events, and any IDs listed in `extraIds`. The engine's
`Scene_Base.isReady` already waits for every cached effect, so the battle starts
after they load (behind the transition) instead of the first cast freezing.
`maxWaitMs` (default 3000) caps the wait. Effects stay cached until the next map
transfer clears them.

Install: copy it into a project's `js/plugins/` and add an entry to `$plugins` in
`js/reactor_plugins.js` (`"name": "ReactorEffectPreload", "status": true`, plus
parameters as strings). ASSEMBLER TEST PROJECT has it since 2026-10-03.

Measured in ASSEMBLER TEST PROJECT with `tools/ingame`, party attack set to
Extinction Event (319), three runs each: first cast froze 127-140 ms without the
plugin and 6 ms with it. The battle became ready about 165-195 ms after the scene
was created. A battle that does not use the effect is unaffected.

The Effekseer web runtime treats any HTTP response as a loaded file
(`_loadBinFile` only calls `onerror` on a network error), so a missing `.efkefc`
served over HTTP never fails and never finishes loading. The plugin checks each
file before loading it and skips missing ones, so they cannot hold the battle.
Not covered: effects drawn inside a 3D battle room load through
`Reactor3D.GpuEffects` and are not preloaded.

## Why the version pin is `170e` and not `1807`

RPGReactor ships **Effekseer 1.70e** (`js/plugins/EffekseerForRPGMakerMZ_Ex.js`
declares `v1.70e`), which reads effect binaries up to version **1710**.

Building against tag `1807` was tried first and produced files stamped `SKFE`
**1810** — unloadable by the shipped runtime. The generator was repinned to
`170e` rather than upgrading the runtime.

If the runtime is ever moved to 1.8x, a repin is **not** sufficient — see
"1.8x gotcha" below.

## Verification performed

The write path is confirmed four independent ways:

1. `efkefc_dump.py` decodes the output; the decoded tree consumes the
   decompressed EDIT buffer with zero leftover bytes.
2. Effekseer's own `Core.LoadFrom()` reads the file back with every value
   intact (name, life, generation time, max generation, renderer type).
3. Two effects differing only in life (60→90) and generation time (2.0→5.5)
   differ in the compiled `BIN_` chunk by exactly 4 bytes — 3 for Life's
   center/max/min, 1 for the float. The runtime binary really is derived from
   the node tree, not a stub.
4. RPGReactor's own `editor/src/forge/EffekseerGenerator/format/efk_format.js`
   parses the output and reports the correct `Root → Sprite` tree.
5. **It renders.** `sprite` output played under the project's own 1.70e WebGL
   runtime in `tools/render-harness/`, side by side with stock
   `HitPhysical.efkefc`: 40 additive billboard sprites, texture resolved from
   `Texture/Particle100.png`, PVA motion and fade-out all correct. Measured by
   lit-pixel count, not by eye.
6. **The node model holds up across the whole stock corpus.** All 405 stock
   effects load, re-save and re-load through EffekseerCore 170e without a single
   failure or exception, and all 405 emit binary version 1710.

## Findings that correct the original handoff

**`EffekseerCore` at `170e` is not an SDK-style net9.0 project.** That was true
of the 1.8x line only. At `170e` upstream ships a legacy .NET Framework 4.5
csproj with `packages.config`, plus an `EffekseerCore.Std.csproj` targeting the
out-of-support `netcoreapp3.1`. `src/EffekseerCore170e/` exists so the clone can
stay untouched while still building on a modern SDK, using the same
`SCRIPT_ENABLED;DOTNET_STARNDARD` defines as the `.Std` variant.

**Default-value omission is real and proven, not just plausible.**
`RendererValues`' constructor sets `Type = Sprite`
(`Data/RendererValues.cs:280`), and `Data/IO.cs:734` returns null for any enum
equal to its default. So a node with no `<DrawingValues><Type>` element **is** a
sprite renderer. Our own output reproduces the omission. This closes the open
question raised by the `Glitter` node in the reference sample.

**1.8x gotcha (does not apply at `170e`, but will bite on any retarget).** On
1.8x, `CommonValues.GenerationTime`, `GenerationTimeOffset` and the
`RemoveWhen*` booleans are annotated `[IO(Import = true, Export = false)]` —
load-only shims. Writing them succeeds in memory and is silently discarded on
save. The live fields moved to `CommonValues.Generation.*` and
`CommonValues.Removal.*`. At `170e` the flat names are correct and exported.

**Locale hazard.** This machine is `it-IT`, whose decimal separator is `,`.
`Core`'s static constructor patches `CurrentCulture`, but only for whichever
thread first touches `Core`. `Program.cs` pins invariant culture process-wide so
no float can serialize as `1,5` into the EDIT chunk's XML.

**`Effekseer.Utils.Logger.LogPath` must be set.** It is a bare static that only
the GUI ever assigns. Left null, every internal `Logger.Write()` throws, gets
swallowed, and dumps a stack trace to stdout.

## What the corpus audit showed

Run against the 405 stock effects (copied out of the project — the audit writes
only a temporary file beside each effect and deletes it). The renderer census
below is wider, and comes from the catalogue rather than the audit; everything
after it is stock-only.

**Renderer usage.** Measured by the 2026-09-12 catalogue over its 47 corpora — 1,819
effects, 1,817 of them loadable, 43,741 nodes (the 2026-09-29 rebuild adds
`premium-44` and the restored 1.7 originals: 1,852 effects, 1,851 loadable,
44,086 nodes; the tables below are not re-measured). "Drawing" excludes nodes with
`IsRendered` off, which the runtime never draws.

| Renderer | Nodes | Drawing nodes | Effects | Corpora |
|---|---|---|---|---|
| Sprite | 27678 | 21541 | 1792 | 47 |
| None (organisational nodes) | 8503 | – | 1498 | 45 |
| Ring | 4001 | 3733 | 930 | 45 |
| Model | 1859 | 1763 | 414 | 40 |
| Track | 1436 | 1366 | 479 | 38 |
| Ribbon | 264 | 244 | 90 | 26 |

This contradicts the agreed v1 scope of Sprite + Ribbon + Ring + Model. Ribbon
appears in 90 effects; Track, which the scope omits, appears in 479. The stock
corpus alone put that gap at 9 against 108, so widening the sample softened the
ratio without changing the answer: Sprite + Ring + Track + Model is the right
list. Worth settling before the schema is designed.

Two materials are too rare to design around: **Lighting** is on 2 drawing nodes
and **BackDistortion** on 21, against 27,491 Default and 1,133 File.

**Two effects will not load**, both saved by Effekseer Tool 1.80.2 and refused by
170e's version gate — `astrology-effekseer-1-0-4/Starflare` and
`evfx-spellblade-mz-demo/effects/heavencloud ultima blade light hilt needs work`.
The shipped runtime cannot play them either.

**Three resource references do not resolve**, so their two effects cannot be part
sources: stock `MGC_W2_MakeTornadoFromHere` wants two textures under
`../[AFTER EFFECTS PARTS]/`, and `sc-technlogy/Laser03` wants
`../../Texture/EasySTAR/Horizontal_01.png`.

**Parts that stand alone are the minority.** Of 28,647 drawing nodes, 3,227 can
be lifted on their own; 25,420 need their ancestor chain carried across. Chains
run 1 to 17 links, most often 2 or 3. Every Track node is ancestor-dependent —
1,366 of 1,366 — and Ribbon has one standalone node out of 244, so neither
renderer can ever be grafted as a bare node.

Loose parts are almost all Sprite (2,440) and Ring (692); Model contributes 94
and Ribbon 1.

**Source binary versions in the corpus:** 72 files at version 15 (Effekseer
1.4x), 98 at 1500, 198 at 1610, 37 at 1710. Everything re-saves as 1710.

**Re-saving an existing effect is not byte-preserving.** Of the 37 files already
at 1710, only 3 come back textually identical. The differences are:

- the `ToolVersion` stamp being rewritten (e.g. `1.70b` → `1.70e`),
- element reordering — `Name` moves after `CommonValues`, for instance,
- schema migration for anything below 1710 (`<Sprite><ColorAll_Fixed>` becomes
  `<ColorAll><Fixed>`, and so on), applied by `ProjectVersionUpdator`,
- small float text changes (`0.1` → `0.10000001`, `3.8` → `3.8000002`), coming
  out of arithmetic in the migration path.

None of this is data loss, but it does mean the generator should be treated as a
tool for **authoring new effects**, not as a lossless editor for existing ones.
Do not build a workflow that round-trips hand-made effects through it.

**Idempotence: 438 of 439 files are stable.** Resaving each effect in one
process, resaving that output in a second process, and comparing hashes, every
file settles after a single pass except one.

`EVFXShoot/EVFX04_06_ConvergingArrow.efkefc` never settles. It cycles with
period 4 — the fifth resave is byte-identical to the first. The writer itself is
deterministic (the same input always produces the same output), so this is the
load/save transform rotating FCurve Bezier handle values between keys: handle X
coordinates alternate between their real values (69.16663, 88.83307, 89.75) and
degenerate ones equal to the key's own frame (80, 100).

Consequences: it does not affect newly authored effects, since the generator
writes curves from scratch. But do not build a workflow that repeatedly resaves
existing effects, and when the mapper emits FCurves, sort keys by frame and keep
each handle inside its neighbouring keys' frames.

**A caution about in-process state.** Comparing in-memory trees across a
load → save → load cycle inside one process does not work: EffekseerCore carries
static state that survives `Core.New()`, and the second load can yield a subtly
different tree even when the file on disk is correct. The audit therefore
compares decoded EDIT chunks read off disk via `Effekseer.IO.EfkEfc.Load`, never
two in-process trees. Anything built on top of EffekseerCore should assume one
effect per process.

## Prior art already in the repo

`editor/src/forge/EffekseerGenerator/` in the RPGReactor source contains an
in-house `.efkefc` reader/writer — `efk_format.js` (reads 15/1500/1610/1710,
writes 1500), `efk_builder.js` (defaults anchored in the 120 stock MZ effects),
`efk_model.js`, `efk_symbols.js`, and ~18 recipe modules. It overlaps a
significant part of what the C# path would otherwise rebuild, and is worth
reading before designing the intermediate schema.

## Next

1. Revisit the v1 renderer scope against the usage census above.
2. Mine `efk_builder.js` for its field defaults. They are anchored in the stock
   effects, which is exactly the knowledge the C# mapper would otherwise have to
   rediscover by trial and error.
3. Design the intermediate JSON schema, grounded in `170e` property names.
4. Build the JSON-driven mapper.
5. Niagara exporter — blocked, no Unreal Editor available yet, so the
   `NiagaraSystemConversionContext` accessors remain unverified. It can be
   unblocked by hand-authoring the intermediate JSON for a few effects and
   building the mapper against that, leaving the exporter as a replaceable front
   end.

## Model renderer note

Effekseer's **Tool** imports FBX, but the **runtime** loads `.efkmodel`. A
Niagara mesh therefore needs converting to `.efkmodel`, not to FBX:
`tools/meshimport` does that from GLB (next section; v3 single-frame). The
format is also documented byte-exact in RPGReactor's
`editor/src/forge/EffekseerGenerator/format/efk_model.js` (v3 single-frame and
v5 multi-frame), verified there against the stock models. Multi-frame (v5)
models play one frame per update from each particle's birth, looping, with no
blending (`Model.cpp`, `EffectNodeModel.cpp`); `meshimport --bake` for them is
not built yet.

## Importing a mesh

`tools/meshimport/meshimport.py` converts a GLB triangle mesh with UVs into a
v3 single-frame `.efkmodel`, an optional overlay model, RGB PNG maps,
and a native lit 1710 `.efkmat`. It needs system Python and Pillow only:

```bash
python tools/meshimport/meshimport.py rock.glb --out out/edits/my-rock --name Rock --color rock-color.png --normal rock-normal.png --overlay-uv spherical
```

Output is `Model/<name>.efkmodel`, optional `Model/<name>-overlay.efkmodel`,
`Materials/<name>.efkmat`, `Texture/<name>-*.png`, and `<name>.import.json`.
The report includes bounds, fit, weld counts, degenerate UV triangles, map
sources, normal-green inversion, and strict saved-model/map read-back.
These materials have no editable Material Editor graph.

Welding is on by default: stored-identical position, normal, UV and supplied
tangent attributes merge in first-occurrence order (the small meteor GLB stores
every triangle with its own three vertices: 29,925 become 5,555, same shape).
Missing tangents are generated after welding, so they average across
neighbouring triangles; `--no-weld` keeps the duplicates and is only for
reproducing models built that way (per-triangle tangents can show facets).
`--overlay-uv spherical` writes `<name>-overlay.efkmodel` with spherical UVs,
which suit generic overlay textures on an unwrapped rock (small meteor);
`--overlay-uv source` keeps the source UVs from before atlasing (big meteor).
Tangents use Gram–Schmidt and handedness; supplied normals
and tangents are retained as normalized bases. Missing normals are generated
and declared. Animated GLBs, morph targets, and non-identity transforms on a
mesh node or its scene ancestors are refused. A nonanimated skin reads the
mesh-space bind positions; joint transforms are metadata, not applied motion.
`--bake` (animated models baked to multi-frame v5) is not implemented yet and
refuses. Height/displacement, FBX, and OBJ are outside scope.

`--diameter` defaults to 2, measured on the longest dimension. `--scale S` multiplies source units by S instead (a GLB in metres at 0.025 units per cm is `--scale 2.5`), and `--keep-origin` keeps the source pivot instead of centring on the bounds: a game mesh that grows from its base (a twist column) must keep it. Select body
materials with `--fit-sections A,B` to exclude fragments from the fit.
`--lod low.glb` uses that geometry with the main GLB's center and scale.
`--rotate-x`, `--rotate-y`, and `--rotate-z` take degrees, applied in that
order to positions and the complete normal/binormal/tangent basis, rounding
the input to float32 before each rotation.

Maps are explicit: `--color`, `--normal`, `--emission`, `--roughness`,
`--metallic`, and `--ao`. Paths are relative to optional `--maps <folder>`;
templates such as `--color "{material}_C.png"` resolve once per material.
Two to four materials use a 2×2 atlas with replicated borders; tile order
defaults to first primitive occurrence, or specify every material using
`--material-order A,B,C,D`. `--size` defaults to 2048; atlases require a
multiple of 1024 so the prescribed gutter is an integer number of pixels.
Tiles resize directly from source maps. Surface packs roughness/metallic/AO
in R/G/B; missing roles use declared constants (1/0/1) and are not read by
the shader. No missing map files are fabricated. 16-bit scalar maps convert
to 8-bit by division by 257. `--flip-normal-green` explicitly inverts green.
`--dissolve-mask mask.png` adds `opacityMask = mask - (1 - vcolor.w)`.
RGB source encoding is retained when it is smaller and pixel-identical.

Use `edit` on an effect copy to set `DrawingValues/Model/Model` and
`RendererCommonValues/MaterialFile/Path`. The effect node stores its own
material texture paths: `ApplyMaterial` keeps a slot whose name the node
already has, so changing a material path alone can keep old textures, while
a slot with a new name takes the material's default path. `edit` and `dump`
cannot set or print these per-node slots yet (planned as
`RendererCommonValues/MaterialFile/Textures/<Name>`); until then give a
material the texture names it needs as its defaults. Importing a mesh does
not edit or install an effect.

## Gradient-map recolour

`tools/gradientmap/gradientmap.py` recolours an effect through a colour ramp
(an N x 1 PNG, dark end on the left): each converted node gets an unlit 1710
`.efkmat` that looks its texture's tone up on the ramp. Needs system Python and
Pillow, plus the built tool for `plan`:

```bash
python tools/gradientmap/gradientmap.py plan <effect.efkefc> --ramp ramp.png --out out/edits/<task> --catalogue out/catalogue.json
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll edit out/edits/<task>/<name>.edit.json <copy.efkefc>
```

`plan` dumps the effect and sorts every drawn node: **convert**, **keep** (its
active colours are black: brightest reachable channel at or below `--black`,
default 24, so black layers stay black), or **refused** (already a file
material, or an advanced render feature is on). It copies the textures and the
ramp into `--out`, writes one material per base texture and an `edit` spec, and
a `.plan.json` with the verdict and reason for each node. `--lift` looks up
`sqrt(tone)`; `--only`/`--skip` take node paths. `material` writes a single
material. Tests: `tools/gradientmap/test_gradientmap.py` (§ Testing).

The material, per pixel:

- tone = luminance(texture) x texture alpha x the node colour's brightest
  channel. Most EVFX textures are flat white shapes whose form is in alpha,
  so luminance alone would paint a node one colour. Soft edges and dim or
  fading particles take the ramp's dark end, solid bright cores its light end;
  per-particle brightness ranges survive as positions along the ramp. Node hue
  is discarded; node alpha still multiplies opacity.
- colour = ramp(tone), sampled at row 0.5 and kept inside the first and last
  texel centres. The ramp slot is a Color texture with **Clamp** (Repeat
  bleeds the light end into the dark end); the base slot is a Value texture
  (no sRGB conversion of the tone).

What the first tests showed (2026-10-03, ASSEMBLER TEST PROJECT's 1.80.3 runtime):

- **Additive overdraw turns a ramp white.** On stock FireOne1, dozens of
  stacked additive Fog sprites saturated to white with any ramp whose light end
  has all three channels; the original yellow and red never do because a
  channel is 0. Blend-mode layers showed the ramp; dimming each sprite to 35%
  barely helped. Prefer effects with few large layers, or a ramp whose light
  end has a zero channel.
- **A near-black start shrinks soft halos.** Soft glow edges have low tone, so
  a ramp whose first third is almost black turns them dark instead of glowing;
  `--lift` restores part of the glow.
- On EVFXParagon20_04_DisintegratingRing (19 nodes converted, 7 black ones
  kept, dump diff confirms) the recolour was clean, and the effect played and
  ended exactly like the original.
- **A file material drops the node's advanced render settings.** With a
  material file the renderer switches to the generated material shader
  (`EffekseerRenderer.CommonUtils.h`, `RendererShaderType::Material`), which
  never samples an alpha texture, UV distortion or blend texture, and has no
  alpha cutoff, falloff or soft particle. About 5,300 catalogued nodes use an
  alpha texture; `plan` refuses them rather than silently changing their shape.
- `edit` cannot set a node's material texture slots yet (§ Importing a mesh),
  so `plan` gives each base texture its own material whose default path names
  it. A slot name the node didn't have before takes that default.

## Inspecting source assets

`tools/assetinspect/` has two read-only inspectors for assets that arrive with
an effect (an Animator FBX, a `Texture2D` folder, a `Mesh` folder). Each takes
files and/or folders (searched recursively) and a new or empty `--out` folder,
and writes a JSON inventory plus a `REPORT.md` whose every line is computed
from the parsed files. Neither tool knows any effect by name.

```bash
python tools/assetinspect/fbx_inspect.py "<pack>/Animator" --out out/edits/<task>/fbx [--trees]
python tools/assetinspect/asset_sheet.py "<pack>" --out out/edits/<task>/assets [--view front] [--per-page 24]
```

`fbx_inspect.py` (standard library only) reports per file: SHA256, version
and record layout, object counts by record and subclass, the Model hierarchy
(parent links from OO connections, stored `Lcl` transforms, attached objects),
Geometry/Material/Texture/Video/Deformer presence, geometry control points,
polygons and layer elements, each material's stored `ShadingModel` and
`Properties70` with its connected textures, texture/video file names, and
every AnimationCurve with its curve node, channel (`d|X`), target object and
property, layer and stack, keys in ticks, seconds and frames, stored values
and interpolation. Also listed: connections naming an id that `Objects` does
not contain (either end; 0 is the scene root), Definitions counts that
disagree with `Objects`, and, across files, byte-identical files and files
with identical curves. `--trees` adds `trees/NNN-<name>.tree.json`, the full
parsed record tree (raw `R` data summarised as length and SHA256).

- Presence is `present`, `absent` (the Objects record was parsed and has no
  object of that type) or `not_inspected` (the file or the section could not be
  read). A missing `Objects`/`Connections` record or an unreadable curve makes
  the file `incomplete`, never "absent".
- Material and texture entries are the stored fields and connections only;
  nothing about shading is inferred from file, node or material names.
- Seconds use 46186158000 ticks per second and frames use GlobalSettings
  `TimeMode` (or `CustomFrameRate`), both as in the pinned ufbx
  (`external/Effekseer1803/Tool/EffekseerResourceConverter/external/ufbx/ufbx.c`).
  Without a stored TimeMode no frames are given. Interpolation decodes
  `KeyAttrFlags` run-length expanded by `KeyAttrRefCount`, constant before
  cubic before linear, as ufbx does; tangents and weights are not reported.
- Parsing checks the 22-byte magic, the endian byte and every length against
  its parent record: 13-byte record headers before version 7500, 25-byte from
  7500, null terminators, property-list lengths, nesting depth, and arrays
  that must decode (raw or zlib) to exactly count x element size bytes.
  Unknown property types or array encodings are errors, not guesses.
- Unsupported, reported as such: ASCII FBX, big-endian binary, version 6.x
  (objects keyed by name), and non-`.fbx` files given explicitly. Mesh
  deformation, skin weights, layered textures and FBX constraints are counted
  but not decoded.

`asset_sheet.py` (Pillow) handles textures (`.png .jpg .jpeg .tga .bmp .tif
.tiff .webp .dds .gif`, as far as the installed Pillow decodes them) and
`.obj` meshes; other files in folders are listed as skipped. Textures:
stored mode, size, frames, per-channel min/max/mean/standard deviation (16-bit
and float images from the stored values, palette images after RGBA
conversion, which the report states), alpha coverage and whether R, G and B
are equal in every pixel. Sheets (`textures-01.png`...) draw each texture on a
checkerboard; images thinner than 16 px are stretched nearest-neighbour for
display and labelled so. Meshes: record counts, faces by corner count, fan
triangles, line/point elements, UV and normal coverage per face (`absent`,
`all faces`, `partial`, or records present but unreferenced), bounds, UV range,
normal lengths, unreferenced vertices, faces repeating a vertex, and stored
`o`/`g`/`usemtl`/`mtllib` names (a `.mtl` is only checked for existence,
never read). Wireframes (`meshes-01.png`...) use positions only, in an
`oblique` (default), `front`, `side` or `top` orthographic view.

- OBJ indices: negative indices count back from the records read so far,
  positive ones may refer forward and are checked at the end; index 0,
  out-of-range indices, non-numeric values, faces with fewer than 3 corners and
  normals on line elements are errors with the line number. Backslash line
  continuations are joined. Curves/surfaces (`curv`, `surf`, `vp`, ...) are
  counted as unsupported statements and not drawn.
- Labels use `--font`, else the first of DejaVu Sans, Arial, Liberation Sans,
  Helvetica, Segoe UI, FreeSans that Pillow finds, else Pillow's built-in font;
  the inventory records which one was used.

Both tools never write beside the sources: `--out` must be new or empty and
must not lie inside an input folder, and every file is created exclusively
(an existing file is never replaced). Per-file names carry an index prefix,
so two inputs with the same name cannot collide. Exit status: 0 every file
inspected, 1 at least one file could not be (fully) inspected — the report is
still written and names the reason — and 2 for a usage error, no matching
input, or a refused output folder (nothing is written). Tests:
`tools/assetinspect/test_assetinspect.py` (§ Testing).

## Unreal readers

The Cascade and Niagara readers and study builders (`tools/cascade`, `tools/niagara`) are not part of this release.
