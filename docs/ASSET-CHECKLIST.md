# Stand-in art for the chant effect

## Read this first

The dump names **materials**, not textures. An Unreal material instance points at
its textures through the parent material graph, and none of that is in a cooked
export. So there are no source texture filenames to search a library for. The one
exception is a cutout mask named `FxTX_Env_smoke_C`, which is a draw-call
optimisation and has no Effekseer equivalent, so it is not needed.

What follows is therefore a list of **roles**, read off the material names. Pick
whatever in your library plays the same role.

## Where the files go

The mapper resolves each texture relative to the intermediate document and copies
it next to the effect. Put the files in `intermediate/Texture/` under the exact
name in the first column, then rerun the mapper. Anything missing keeps the
placeholder and is reported as a substitution, so a partial set is fine and you
can see at a glance what is still standing in.

## Sheets, three of them

Each cell should be square-ish and the grid must divide the texture evenly. The
mapper warns if it does not.

| File to supply | Grid | Plays as | Used by |
|---|---|---|---|
| `FxMii_Thunderanime_D.png` | 8 x 1 | lightning, cell blended into the next | 2 emitters |
| `FxMii_Thunderanime_G.png` | 8 x 1 | lightning, cells cut hard | 2 emitters |
| `FxMii_SparkMagic_A.png` | 2 x 2 | spark, one random cell per particle | 2 emitters |

## Single images, seventeen of them

| File to supply | Plays as | Used by |
|---|---|---|
| `FxMii_AuraPar_C.png` | aura mote, rectangle aligned, local space | 1 emitter |
| `FxMii_MagicScroll_A.png` | scrolling rune or magic circle | 3 emitters |
| `FxMM_ky_PolarGlow_UseST.png` | glow in polar coordinates, a radial streak burst | 2 emitters |
| `FxMii_RadiusDepthFade_A.png` | soft radial gradient, depth faded | 2 emitters |
| `FxMii_RadiusDepthFadeAdd_A.png` | the same, additive | 1 emitter |
| `FxMii_CylFresnel_B.png` | cylinder wall with a bright rim | 1 emitter |
| `FxMii_CylFresnel_D.png` | cylinder wall with a bright rim | 2 emitters |
| `FxMii_CylFresnel_F.png` | cylinder wall with a bright rim | 1 emitter |
| `FxMii_GpuSpark_A.png` | spark streak, aligned to travel | 2 emitters |
| `FxMii_GpuSpark_A_NoUseST.png` | spark streak, aligned to travel | 1 emitter |
| `FxMii_GpuSparkAdd_A.png` | spark streak, additive, aligned to travel | 1 emitter |
| `FxMii_GpuFlare_G.png` | flare | 1 emitter |
| `FxMii_Flare_E_Add.png` | flare, additive | 1 emitter |
| `FxMii_Ring_G.png` | ring | 1 emitter |
| `FxMii_Line_B.png` | straight streak or beam | 2 emitters |
| `FxMii_Lightning_C.png` | lightning bolt, not a sheet | 1 emitter |
| `FxMii_DistotionCircle_C.png` | heat-haze circle, see the caveat below | 1 emitter |

Three of the cylinder materials differ only by suffix, and three of the sparks
likewise. They are probably tints or intensities of one texture in the source, so
one image reused three times is a reasonable first pass.

## Two caveats

**Everything renders additive.** The blend mode lives in the material graph, not
in the dump, so the front end writes additive for every node. The names ending in
`_Add` confirm at least some are meant that way; the rest are a guess. Textures
authored for alpha blending will read too hot.

**The distortion circle cannot be checked by eye here.** It needs Effekseer's
back-distortion material type, and distortion draws nothing in this project's
render harness for reasons still unresolved, so a blank result will not tell you
whether it worked.

## Meshes: not yet, please

Fourteen of the thirty-two emitters draw geometry rather than sprites, across
eight distinct meshes: `FxMD_Btl_Ring_C`, `FxMD_Btl_Circle_A`,
`FxMD_Btl_twist_B`, `FxMD_Btl_Locus_C`, `FxMD_Btl_Locus_F`, `FxMD_Btl_Locus_G`,
`FxMD_Btl_CylinderHarf_A`, `FxMD_Circle_C`. Rings, arcs, trail ribbons and a half
cylinder.

Do not go looking for these yet. The front end currently declares a mesh emitter
as a loss and converts it to a sprite, so supplying models would change nothing
until the model renderer path is written. That is the next worthwhile piece of
work on this converter, and it is a bigger one than the flipbooks were.
