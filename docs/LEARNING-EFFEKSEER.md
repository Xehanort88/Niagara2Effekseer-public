# Learning Effekseer from your own effects

A reading order through the 405 stock effects in `REACTOR DRAFT PROJECT/effects`,
chosen so each step introduces one new idea. Built from the audit data, not from
the manual — every "what it teaches" line below was checked against what the file
actually contains.

## Before you start

**Work on copies.** Opening an effect in the tool and saving it rewrites the
file. We measured this: of the 37 stock effects already at the current binary
version, only 3 come back byte-identical after a save. Nothing is lost in the
process, but the file does change — version stamps, element order, migrated
schema. One effect (`EVFXShoot/EVFX04_06_ConvergingArrow.efkefc`) never settles
at all. So copy an effect somewhere else before you poke at it, and leave the
originals alone.

**You already have everything you need:**

| What | Where |
|---|---|
| Effekseer Tool 1.70e | `<Effekseer tool folder>\Tool\Effekseer.exe` |
| Docs | `<Effekseer docs>` |
| 405 worked examples | `REACTOR DRAFT PROJECT/effects` |

The tool is version 1.70e, which matches the runtime your game ships and the
version this project's generator is pinned to. No mismatch to worry about.

**Three ways to look at an effect**, and they're complementary:

1. Open it in the tool. Best for changing things and seeing the result.
2. Decode it to readable XML: `python efkefc_dump.py <effect.efkefc> out.xml`.
   Best for seeing *everything* at once, including what's set to defaults.
3. Play it in `tools/render-harness/`. Best for a quick "does this still work"
   after an edit, without the tool's UI in the way.

## The whole model in one paragraph

An effect is a **tree of nodes**. Each node either draws particles or exists to
organise and drive its children. A node whose renderer type is `None` draws
nothing — it's a controller, and its motion and colour flow down to its children.
Every node carries the same handful of parameter groups: how many particles and
how long they live, where they spawn, how they move, how they rotate, how they
scale, what colour they are, and which renderer draws them. That's it. The rest
is detail.

Those groups are exactly what the converter maps onto, so the vocabulary here is
the vocabulary in `README.md`.

## The ladder

### 1. `HitPhysical.efkefc` — the anatomy of an effect
Four nodes: one controller plus sprite leaves. The smallest complete example of
the shape every other effect follows.

Look for: the controller node with renderer `None` carrying a rotation range that
drives its children; particle lifetime and spawn count; a texture with additive
blending; fade in and fade out.

### 2. `BallsOfLight.efkefc` — sprites and nothing else
No controller tricks, no ring, no easing. Position, rotation, scale, texture,
blend, fade. This is the one to make your first edits in.

Try: change the lifetime, swap the texture for another file in
`effects/Texture/`, change the blend mode from Add to Blend and watch it go flat.

### 3. `PiercePhysical.efkefc` — easing versus velocity
Introduces easing. Up to now motion has been velocity and acceleration; easing
instead interpolates from a start value to an end value over the particle's life.
Knowing when each is the right tool is most of what makes motion look deliberate.

### 4. `Flash.efkefc` — the ring renderer
First non-sprite renderer. Rings are how expanding shockwaves and magic circles
are made, and they're the second most used renderer in your library after sprite.

### 5. `Goo_01.efkefc` — density in a tiny tree
Only three nodes, but it uses force fields, a spawn shape, UV animation and
easing. Proof that node count and complexity are different things, and a good
example of how much one node can carry.

Look for: the spawn shape, which decides *where* particles appear rather than how
they move, and the local force field.

### 6. `Breath.efkefc` — the track renderer
Tracks are Effekseer's trails and beams. Worth spending time on: tracks appear in
108 of your 405 effects, and they're the target the converter should use for
Niagara ribbon emitters.

### 7. `EE01_09_Fortify.efkefc` — the model renderer
First effect using a 3D model instead of a flat quad. Models are `.efkmodel`
files, referenced the same way textures are.

### 8. `EE01_20_Resurrect.efkefc` — the ribbon renderer
Fifteen nodes, and the first use of ribbons. Ribbon is the rarest renderer in
your library, appearing in only 9 effects, so this is the last renderer to learn
rather than the first.

### 9. `Survey2023Reward_A_Ally.efkefc` — the capstone
Decode it with `python efkefc_dump.py` to read it as text alongside the tool. Nested controllers, rings, models, procedural
geometry. If this one makes sense, you can read anything in the library.

## Suggested first session

Copy `BallsOfLight.efkefc` and its textures somewhere scratch. Open the copy.
Change exactly one thing, save, and play it in the render harness. Then change it
back. Do that five or six times with different parameters — lifetime, particle
count, scale, colour, blend mode, fade.

The goal isn't to make something good. It's to build the mapping between a number
in a panel and a change on screen. Once that mapping exists, the manual becomes
readable and the converter's output becomes something you can finish.
