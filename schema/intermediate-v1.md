# Intermediate effect schema, version 1

The converter is two halves that meet here. A **front end** reads a Niagara
system and writes this document. A **mapper** reads this document and writes an
`.efkefc`. Neither knows about the other, so the front end can be Unreal Editor
Python, an offline `.uasset` parser, or a person typing JSON by hand.

Every field name below is either a real Effekseer 1.70e property name or a
deliberate rename noted as such. Where a name matches Effekseer, the mapper sets
that property directly and nothing has to be guessed.

## Rules the schema exists to enforce

**Provenance is mandatory, not decorative.** Converted output embeds the source
vendor's art. Every texture and model records where it came from, so the licence
question stays answerable later instead of becoming archaeology across a folder
of PNGs. A document with a resource that has no `source` block is invalid.

**What did not convert is part of the output.** The `unsupported` array is not a
log. It is a deliverable, because an explicit list of what did not come across is
most of the value of the conversion. A front end that silently drops a light
renderer is broken.

**Values are what they are, not what they ought to be.** Every numeric may be a
bare number or a `{center, min, max}` triple, because Effekseer stores randomised
values that way. A front end that only knows a constant writes the bare number.

## Document shape

```jsonc
{
  "schemaVersion": 1,

  // Where this came from, and how. "extractor" distinguishes a real extraction
  // from a hand-authored document, which matters when reading the numbers.
  "provenance": {
    "sourceSystem": "/Game/FX/FX_Burst",
    "sourceEngine": "UE 5.3",
    "extractor": "hand-authored",      // or "ue-python-1.0", "uassetapi-1.0"
    "extractedAt": "2026-09-11",
    "licenceNote": "Epic StarterContent; check terms before redistributing"
  },

  // The effect as a whole.
  "effect": {
    "name": "FX_Burst",
    "scale": 1.0                        // multiplies every distance on import
  },

  // Everything that did not convert. Each entry names the thing, says what kind
  // of gap it is, and why. "kind" is one of:
  //   no-equivalent   Effekseer has no such feature at all
  //   material-driven the behaviour lives in an Unreal material graph
  //   approximated    converted, but not faithfully; "note" says how
  //   gpu-only        requires GPU simulation
  "unsupported": [
    { "item": "NiagaraLightRendererProperties", "kind": "no-equivalent",
      "reason": "Effekseer has no light renderer; the emitter's light is lost." }
  ],

  // One entry per Effekseer node. Order is sibling order; nesting is explicit.
  "nodes": [ /* see below */ ]
}
```

## A node

A node maps one-to-one onto an Effekseer `Node`. Fields are grouped exactly as
Effekseer groups them, so the mapper is a transcription rather than a
translation.

```jsonc
{
  "name": "Sparks",
  "children": [],

  // Traceability, not behaviour. Which Niagara emitter and which module scripts
  // produced this node. Lets a reviewer ask "where did this number come from".
  "source": {
    "emitter": "Burst",
    "modules": ["/Niagara/Modules/Spawn/Initialization/V2/InitializeParticle"]
  },

  // -> Node.CommonValues
  //
  // A node generates on one interval, so a source that spawns continuously AND
  // bursts cannot be written literally. "maxGeneration" is how many particles
  // the node will ever make, so a finite source run becomes a number here and
  // only a genuinely endless one becomes "infinite". A burst is the pair
  // maxGeneration = the burst count and a near-zero generationTime; a rate is
  // maxGeneration = rate x duration with the interval derived from the rate.
  // A front end that reads the rate and ignores the rest turns a one-shot cast
  // into a permanent emitter, and reports nothing, because both are legal.
  "common": {
    "life": { "center": 60, "min": 45, "max": 75 },
    "maxGeneration": 200,               // or "infinite"
    "generationTime": 0.00001,          // near-zero is the burst idiom
    "generationTimeOffset": 0,
    "removeWhenLifeIsExtinct": true
  },

  // -> Node.GenerationLocationValues. "type" is Effekseer's own enum:
  //   Point | Sphere | Model | Circle | Line
  //
  // Effekseer places a sphere-spawned particle at (radius, rotationX,
  // rotationY). The angles are NOT optional in practice: leave them at their
  // fixed default of 0 and every particle spawns on the same point, so an
  // emitter making 62 particles looks like it made one. The stock corpus uses
  // -360..360 on both. The mapper records a loss if they are omitted.
  //
  // A radius given as a range spawns through the volume; a radius pinned to one
  // value spawns on the shell.
  "generationLocation": {
    "type": "Sphere",
    "sphere": {
      "radius": { "min": 0, "max": 10.0 },
      "rotationX": { "min": -360, "max": 360 },
      "rotationY": { "min": -360, "max": 360 }
    }
  },

  // Point randomises each axis on its own, so a point whose location carries a
  // range on every axis IS a box. Nothing is approximated here, and a front end
  // converting a box spawn should reach for this rather than for a sphere.
  //
  //   "generationLocation": {
  //     "type": "Point",
  //     "point": { "location": {
  //       "x": { "min": -10, "max": 10 },
  //       "y": { "min":   0, "max":  5 },
  //       "z": { "min": -10, "max": 10 } } }
  //   }
  //
  // Circle is a flat ring. "axisDirection" is its normal, so a Z-up source
  // becomes YAxis here. It is the nearest shape to a source cylinder, and a
  // document that names one has already accepted losing the cylinder's height:
  // say so in "unsupported".
  //
  //   "generationLocation": {
  //     "type": "Circle",
  //     "circle": {
  //       "axisDirection": "YAxis",     // XAxis | YAxis | ZAxis
  //       "radius": { "min": 0, "max": 100 },
  //       "angleStart": 0, "angleEnd": 360,
  //       "division": 8,
  //       "type": "Random"              // Random | Order | ReverseOrder
  //     }
  //   }

  // -> Node.LocationValues. This is where the particle goes, and with type
  // "Fixed" it is also where the node itself sits. A source emitter offset
  // belongs here once a spawn shape has claimed the generationLocation slot,
  // because a sphere or a circle has no centre of its own. Written as
  // "fixedOffset" rather than "fixed", which is a C# keyword:
  //
  //   "location": { "type": "Fixed", "fixedOffset": { "x": 0, "y": 45, "z": 0 } }
  // PVA carries the start position as well, so a node can hold its own offset and
  // still move; reaching for "Fixed" instead forces a choice between the two.
  //
  // The units are FRAMES, not seconds. Effekseer integrates
  // position = location + velocity * t + acceleration * t * t / 2 with t counted
  // in frames, and "life" above is in frames for the same reason. A front end
  // reading a source that states velocity per second must divide by the frame
  // rate, and acceleration per second squared by its square. Getting this wrong
  // is not subtle in the result but is invisible in the document.
  "location": {
    "type": "PVA",                      // Fixed | PVA | Easing | LocationFCurve
    "pva": {
      "location":     { "x": 0, "y": 45, "z": 0 },
      "velocity":     { "x": {"min":-250,"max":250}, "y": {"min":0,"max":400}, "z": {"min":-250,"max":250} },
      "acceleration": { "x": 0, "y": 0, "z": 0 }
    }
  },

  // -> Node.ScalingValues. SinglePVA drives one uniform scale, which is what a
  // uniform sprite size wants. A size that CHANGES across the life is two ends
  // and a shape between them, so it belongs in SingleEasing instead:
  //
  //   "scaling": {
  //     "type": "SingleEasing",
  //     "singleEasing": { "start": {"min":8,"max":12}, "end": 0 }
  //   }
  //
  // Both ends carry the source's own spread. A curve with more than two samples
  // keeps only its ends, so say so in "unsupported": a size that swells part way
  // through and comes back arrives as a straight ramp.
  "scaling": {
    "type": "SinglePVA",                // Fixed | PVA | SinglePVA | SingleEasing | FCurve | SingleFCurve
    "singlePVA": { "scale": {"min":0.6,"max":1.4}, "velocity": -0.01 }
  },

  // -> Node.RotationValues. Omit entirely for no rotation. Spelled
  // "fixedRotation" for the same reason location says "fixedOffset".
  //
  // Effekseer authors rotation in DEGREES and converts to radians on export, and
  // its velocity and acceleration are per frame like everything else here. A
  // source that states turns multiplies by 360, and turns per second by 6.
  "rotation": {
    "type": "PVA",                      // Fixed | PVA | Easing | AxisPVA | AxisEasing | RotationFCurve
    "pva": {
      "rotation": { "x": 0, "y": 0, "z": {"min":0,"max":360} },
      "velocity": { "x": 0, "y": 0, "z": {"min":3,"max":6} }
    }
  },

  // -> Node.DepthValues.DepthOffset. Slides the particle along the view
  // direction, which is what a camera offset in the source is for. One number.
  "depthOffset": 25,

  // -> Node.LocalForceFieldValues, which has exactly four slots
  // (LocalForceField1..4). A source with more forces than that overflows, and
  // the overflow goes in "unsupported" rather than being dropped quietly.
  // "type" is Effekseer's own LocalForceFieldType: None, Force, Wind, Vortex,
  // Turbulence, Drag, Gravity, AttractiveForce.
  "forceFields": [
    { "type": "Gravity",    "gravity": { "x": 0, "y": -980, "z": 0 } },
    // Every type except Gravity and Turbulence is a bare strength, and they all
    // read it from "power". Drag is that strength against the particle's own
    // velocity per frame, so a coefficient stated per second divides by 60.
    { "type": "Drag",       "power": 0.0083 },
    { "type": "Turbulence", "power": 2.0,
      "turbulence": { "fieldScale": 20.0, "octave": 2, "seed": 1 },
      // Any field can be turned before it acts. Degrees per axis, and static: a
      // source field that SPINS has no equivalent, because Effekseer orients a
      // field once and then leaves it.
      "rotation": { "x": -90, "y": 0, "z": -90 } }
  ],

  // -> Node.RendererCommonValues. Shared by every renderer type.
  "rendererCommon": {
    "colorTexture": {
      "file": "Texture/T_Spark.png",
      "source": {
        "unrealTexture": "/Game/FX/Textures/T_Spark",
        "material": "/Game/FX/M_Spark",
        "materialParameter": "ColorTexture(Default)"
      }
    },
    "alphaTexture": null,
    "alphaBlend": "Add",                // Opacity | Blend | Add | Sub | Mul
    "fadeOut": { "type": "WithinLifetime", "frame": 10.0 },
    // How the node reads its texture. Effekseer states UV rectangles in TEXELS
    // and its exporter divides them by the real texture size, so a rectangle
    // written here would tie the document to one resolution: reauthor the sheet
    // at twice the size and every number would be wrong. This block carries the
    // grid and the timing instead, and the mapper measures the cell against the
    // texture it actually copied.
    //
    // A flipbook. The sheet is frameCountX by frameCountY cells filling the
    // whole texture, and each cell is held for frameLength frames. A grid that
    // does not divide the texture evenly is reported as a loss, because the
    // cells will not land on pixel boundaries.
    "uv": {
      "type": "Animation",              // Default | Fixed | Animation | Scroll | FCurve
      "animation": {
        "frameCountX": 8,
        "frameCountY": 1,
        "frameLength": 3,               // frames each cell is held
        "loopType": "Once",             // Once | Loop | ReverceLoop, Effekseer's own spelling
        "interpolation": "Lerp",        // None | Lerp, blends one cell into the next
        "startSheet": { "min": 0, "max": 0 }
      }
    }

    // Scrolling instead. Speeds are in textures per frame, so they survive a
    // resize too, and the window is the whole texture:
    //
    //   "uv": { "type": "Scroll", "scroll": { "speedU": 1.0, "speedV": 0.5 } }
  },

  // -> Node.DrawingValues
  // "colorAll" selects which of the colour blocks below is read, and only that
  // one is used. A source whose colour ramps across the particle's life converts
  // to "Easing", whose two ends are [r,g,b,a] in 0..255:
  //
  //   "colorAll": "Easing",
  //   "colorAll_Easing": { "start": [255,64,16,255], "end": [64,16,6,0] }
  //
  // A ramp of many samples keeps only its two ends. Normalise both ends against
  // one shared divisor when the source is above 1.0, or a ramp that dims will
  // arrive looking like one that holds steady. If the easing already fades the
  // alpha to zero, leave "fadeOut" out of rendererCommon: the particle would
  // otherwise be faded twice.
  "renderer": {
    "type": "Sprite",                   // Sprite | Ribbon | Ring | Model | Track | None
    "sprite": {
      "billboard": "Billboard",
      "colorAll": "Fixed",              // Fixed | Random | Easing | FCurve
      "colorAll_Fixed": [255, 200, 120, 255]
    }
  }
}
```

## Renderer scope

`Sprite`, `Ring`, `Track` and `Model` are in scope for v1. `Ribbon` is not.
Measured over the 405 stock effects, Track appears in 108 and Ribbon in 9, and
Track is the trail renderer a Niagara ribbon emitter actually wants. A node whose
source is a ribbon emitter converts to `Track` and records an `approximated`
entry in `unsupported`.

`None` is legal and means an organisational node that only carries children.

## Materials

The `rendererCommon` block assumes the **extract** route: read the material's
texture parameters and blend mode, write them into Effekseer's own slots. The
`source` block on each texture records which material parameter it came from, so
a later bake route can replace the file without losing the trail.

This is a better bet than it looks. A real effects project builds on a small
library of shared parent materials with named parameters, and those names line up
with Effekseer's slots almost directly: a colour texture, an alpha or edge mask,
a normal map, a blend mode, and scalar scroll and tiling values. Extraction
recovers all of that. What it cannot recover is anything genuinely procedural
computed in the graph, which is what an `approximated` entry is for.
