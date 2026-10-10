# Niagara2Effekseer

Tools for making and studying [Effekseer](https://effekseer.github.io/) effects (`.efkefc`) for RPG Maker MZ and
RPGReactor, with the start of a converter from Unreal Engine particle effects.

It is research tooling, not a one-click converter. The detailed notes in [docs/REFERENCE.md](docs/REFERENCE.md)
record how each tool works, what was measured and why.

## What is in it

**Effekseer effects**
- `Niagara2Efk` (C#, on Effekseer's own editor core): a command-line tool to read, dump, lint and **edit**
  `.efkefc` files from a JSON spec. Edits are applied to a copy and read back to verify them. It also assembles new
  effects from parts of existing ones and generates small effects from scratch. Builds for Effekseer **1.70e** and
  **1.80.3**.
- `tools/author`: Python helpers to write edit specs, including procedural lightning bolts.
- `tools/gradientmap`, `tools/meshimport`, `tools/fontatlas`: recolour through a gradient-map material, import GLB
  meshes as `.efkmodel`, make letter sheets from a font.
- `tools/hlslcheck`: checks that `.efkmat` file materials compile in the Effekseer editor (DirectX HLSL), not only
  in the WebGL runtime.

**Checking effects**
- `tools/render-harness`, `tools/parts`: play effects headless in Chrome under the shipped WebGL runtime, record
  frames and live-particle counts, and build a browsable contact sheet of parts.
- `tools/ingame`: play effects inside a real RPG Maker MZ / RPGReactor battle on the real GPU and measure the
  first-cast freeze, frame times and the end, or capture every frame.
- `tools/install`: install an edited effect into a game project with its animation entry, checked.
- `plugins/ReactorEffectPreload.js`: a game plugin that loads a battle's effects while the battle opens, so a heavy
  effect does not freeze on its first cast.

**From Unreal**
- `src/Niagara2Efk.Mapper`, `schema/`: the converter's intermediate format and its mapping to Effekseer, for editor
  (uncooked) Niagara data; `Niagara2Efk cascade` turns a Cascade JSON dump into that format.
- The Cascade and cooked-Niagara study builders are not part of this release.

## Scope and responsibility

No game or third-party content is included. You bring the effects, packs, game exports and keys you have the
right to use. Converting or editing an effect does not make its content yours: an effect built from someone else's
assets embeds their art and settings, so do not distribute it without the rights holder's permission. Follow the
terms of any game, pack or tool you use.

This project is not affiliated with or endorsed by Epic Games, the Effekseer project, Gotcha Gotcha Games, or any
game publisher.

## Requirements

- Windows 10/11 for everything. Most Python and C# tools also run elsewhere; `hlslcheck` and the Chrome-driven
  renderers expect Windows paths by default.
- [.NET 9 SDK](https://dotnet.microsoft.com/) (`dotnet --version` prints 9.x).
- Python 3.11+ with `numpy` and `Pillow` (`pip install numpy pillow`).
- Node.js 18+ and Google Chrome for the headless renderers and the in-game tool (no npm packages needed).

## Setup

```bash
git clone https://github.com/Xehanort88/Niagara2Effekseer-public.git
cd Niagara2Effekseer-public
git submodule update --init --depth 1          # Effekseer sources, tags 170e and 1803
dotnet build src/Niagara2Efk/Niagara2Efk.csproj -c Release -p:Platform=x64
dotnet build src/Niagara2Efk1803/Niagara2Efk1803.csproj -c Release
```

Pick the build by the runtime of the project the effect is for: the 1.70e build writes files that play in 1.70 and
1.8 runtimes; the 1.80.3 build writes 1.8 files and can use 1.8 features
([docs/REFERENCE.md § Two builds](docs/REFERENCE.md#two-builds-effekseer-170e-and-1803)).

Generated files go to `out/`, which is ignored by git.

## Quick start

```bash
# list every node and setting of an effect, as the paths the edit command uses
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll dump path/to/Effect.efkefc

# a neutral one-node effect to author from
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll blank out/new/Base.efkefc 60

# apply an edit spec to a copy and verify it (see docs/REFERENCE.md § Editing a copy of an effect)
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll edit out/new/MyEffect.edit.json out/new/MyEffect.efkefc

# check curve timing before installing
dotnet src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll lint --strict out/new/MyEffect.efkefc
```

Never use the generator to re-save an existing effect as a way to clean it up: re-saving is not lossless. Change
existing effects with `edit` on a copy.

## Configuration

`EFFEKSEER_TOOL_DIR` points `tools/fontatlas` at an Effekseer tool folder with the reference letter-sheet demos.
The Python tools take their inputs as command-line arguments (`--help` lists them); running
`Niagara2Efk.dll` without arguments prints its commands.

## Tests

Build the tool first (the edit tests run it on both builds). Most suites take a work folder:

```bash
python tests/edit/run_tests.py --work out/tests/edit
python tools/author/test_efkbolts.py --work out/tests/efkbolts
node tools/ingame/test_ingame.js --work out/tests/ingame --no-chrome
```

Each `tools/<name>/` folder has its own `test_*.py` (or `test_*.js`); run it with `--work out/tests/<name>`.
`hlslcheck` needs Windows (it compiles with `d3dcompiler_47.dll`). The `selftest` and `audit` commands of
`Niagara2Efk` run over your own effect corpora, listed in an `out/catalogue.json` you build with `catalogue`.

## Documentation

- [docs/REFERENCE.md](docs/REFERENCE.md): every command and tool in detail, with the traps found along the way
  (Effekseer runtime behaviour, curve sampling, materials, the battle camera).
- [docs/LEARNING-EFFEKSEER.md](docs/LEARNING-EFFEKSEER.md): notes on how Effekseer effects are structured.
- [docs/TRAIL-CHECKLIST.md](docs/TRAIL-CHECKLIST.md): diagnosing trails that look doubled, sharp or short.
- [docs/ASSET-CHECKLIST.md](docs/ASSET-CHECKLIST.md): what an Unreal effect needs exported to be read.
- [schema/intermediate-v1.md](schema/intermediate-v1.md): the converter's intermediate format.

The reference was written while the tools were built, so it reads as a working record: dates, measurements and
the reasons behind each choice.

## License

[MIT](LICENSE). Third-party components and their licences are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
