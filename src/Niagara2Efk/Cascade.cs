using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;

namespace Niagara2Efk
{
	/// <summary>
	/// Front end: read a cooked Unreal **Cascade** particle system dump and write
	/// the intermediate document defined in schema/intermediate-v1.md. The mapper
	/// turns that document into an .efkefc; this class never touches Effekseer.
	///
	/// Cascade is worth a front end where Niagara is not. Cascade evaluates its
	/// modules at runtime, so cooking keeps them: spawn rate, lifetime, colour and
	/// particle count survive as real numbers. Niagara compiles its graph to
	/// scripts and loses exactly what a converter needs.
	///
	/// Everything Effekseer 1.70e cannot express is written into the document's
	/// `unsupported` array, which the schema treats as a deliverable rather than a
	/// log. The biggest entry is always the vector field: Cascade can push
	/// particles through a baked 3D texture of velocities, and Effekseer has only
	/// parametric force fields. A curling plume can be imitated with turbulence
	/// plus a directional pull; the specific flow cannot be imported.
	///
	/// Units. Cascade works in Unreal units and seconds. Time converts exactly at
	/// 60 frames per second. Distance has no common yardstick between the two, so
	/// it takes a chosen scale.
	/// </summary>
	static class Cascade
	{
		const float FramesPerSecond = 60.0f;

		/// <summary>
		/// Module names this reader has a rule for, by prefix. Anything outside the
		/// list is declared rather than ignored, so the report stays honest as forks
		/// add modules. Adding a reader means adding its name here too.
		/// </summary>
		static readonly string[] HandledModules =
		{
			"ParticleModuleRequired",
			"ParticleModuleSpawn",
			"ParticleModuleTypeData",
			"ParticleModuleLifetime",
			"ParticleModuleSize",
			"ParticleModuleColor",
			"ParticleModuleLocation",
			"ParticleModuleVelocity",
			"ParticleModuleAcceleration",
			"ParticleModuleRotation",
			"ParticleModuleMeshRotation",
			"ParticleModuleCameraOffset",
			"ParticleModuleVectorField",
			"ParticleModuleParameterDynamic",
			"ParticleModuleCollisionGPU",
			"ParticleModuleSubUV",
			"ParticleModuleSQEX_Ring",
			"ParticleModuleSQEX_Range",
			"ParticleModuleSQEX_ParticleRotation",
			"ParticleModuleSQEX_DepthOffset",
		};


		class Ctx
		{
			public JsonElement[] Exports;
			public float WorldScale;
			public float SpriteScale;
			// How hard the parametric stand-ins push. The source flow field cannot
			// be imported, so its strength has no derivable equivalent and is a
			// tuning knob rather than a converted value.
			public float TurbulenceGain = 6.0f;
			public float LiftGain = 1.5f;
			public List<Dictionary<string, object>> Unsupported = new List<Dictionary<string, object>>();
			public List<string> Exact = new List<string>();

			public void Gap(string item, string kind, string reason)
			{
				Unsupported.Add(new Dictionary<string, object>
				{
					["item"] = item,
					["kind"] = kind,      // no-equivalent | material-driven | approximated | gpu-only
					["reason"] = reason,
				});
			}

			public void Kept(string line) { Exact.Add(line); }
		}

		public static int Run(string dumpPath, string outDocPath, float worldScale, float spriteScale,
			float turbulenceGain = 6.0f, float liftGain = 1.5f)
		{
			if (!File.Exists(dumpPath))
			{
				Console.Error.WriteLine("dump not found: " + dumpPath);
				return 2;
			}

			JsonDocument dump;
			try { dump = JsonDocument.Parse(File.ReadAllText(dumpPath)); }
			catch (Exception e) { Console.Error.WriteLine("could not parse dump: " + e.Message); return 2; }

			using (dump)
			{
				if (dump.RootElement.ValueKind != JsonValueKind.Array)
				{
					Console.Error.WriteLine("expected a JSON array of exports");
					return 2;
				}

				var ctx = new Ctx
				{
					Exports = dump.RootElement.EnumerateArray().ToArray(),
					WorldScale = worldScale,
					SpriteScale = spriteScale,
					TurbulenceGain = turbulenceGain,
					LiftGain = liftGain,
				};

				var system = ctx.Exports.FirstOrDefault(e => TypeOf(e) == "ParticleSystem");
				if (system.ValueKind == JsonValueKind.Undefined)
				{
					Console.Error.WriteLine("no ParticleSystem export in the dump");
					return 2;
				}

				var emitters = Prop(system, "Emitters");
				if (emitters.ValueKind != JsonValueKind.Array || emitters.GetArrayLength() == 0)
				{
					Console.Error.WriteLine("the ParticleSystem has no emitters");
					return 2;
				}

				var nodes = new List<Dictionary<string, object>>();
				foreach (var reference in emitters.EnumerateArray())
				{
					var emitter = Resolve(ctx, reference);
					if (emitter.ValueKind == JsonValueKind.Undefined) continue;

					var node = BuildEmitter(ctx, emitter);
					if (node != null) nodes.Add(node);
				}

				if (nodes.Count == 0)
				{
					Console.Error.WriteLine("no emitter could be read");
					return 1;
				}

				string package = StringProp(system, "Package") ?? NameOf(system);

				var doc = new Dictionary<string, object>
				{
					["schemaVersion"] = 1,
					["provenance"] = new Dictionary<string, object>
					{
						["sourceSystem"] = package,
						["sourceFile"] = Path.GetFileName(dumpPath),
						["sourceEngine"] = "UE4 Cascade",
						["extractor"] = "cascade-dump-1.0",
						["extractedAt"] = DateTime.UtcNow.ToString("yyyy-MM-dd"),
						["licenceNote"] = "Converted from a cooked Cascade dump. The dump carries parameters only, no art; "
							+ "every texture is a stand-in. Check the rights on the source system before redistributing anything built from it.",
						["note"] = string.Format(
							"Distances scaled by {0}; sprite sizes additionally by {1}. Time is exact at {2} fps.",
							worldScale.ToString(CultureInfo.InvariantCulture),
							spriteScale.ToString(CultureInfo.InvariantCulture),
							FramesPerSecond),
					},
					["effect"] = new Dictionary<string, object>
					{
						["name"] = NameOf(system),
						["scale"] = 1.0f,
					},
					["unsupported"] = ctx.Unsupported,
					["nodes"] = nodes,
				};

				Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outDocPath)));
				File.WriteAllText(outDocPath,
					JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true }),
					new UTF8Encoding(false));

				Console.WriteLine("Read {0} emitter(s) from {1}", nodes.Count, package);
				Console.WriteLine("Intermediate document -> {0}", Path.GetFullPath(outDocPath));
				Console.WriteLine();
				Console.WriteLine("--- carried across exactly ({0}) ---", ctx.Exact.Count);
				foreach (var line in ctx.Exact) Console.WriteLine("  " + line);
				Console.WriteLine();
				Console.WriteLine("--- declared unsupported ({0}) ---", ctx.Unsupported.Count);
				foreach (var u in ctx.Unsupported)
					Console.WriteLine("  [{0}] {1}\n      {2}", u["kind"], u["item"], u["reason"]);
				Console.WriteLine();
				Console.WriteLine("Now run the mapper to produce the effect:");
				Console.WriteLine("  dotnet src/Niagara2Efk.Mapper/bin/x64/Release/net9.0/Niagara2EfkMapper.dll \\");
				Console.WriteLine("    {0} <out.efkefc> <placeholder.png>", outDocPath);

				return 0;
			}
		}

		// --------------------------------------------------------------- build

		static Dictionary<string, object> BuildEmitter(Ctx ctx, JsonElement emitter)
		{
			// Only LOD 0 is read. Effekseer 1.70e has its own LOD concept, but it
			// does not map onto Cascade's per-level module lists.
			var lods = Prop(emitter, "LODLevels");
			if (lods.ValueKind != JsonValueKind.Array || lods.GetArrayLength() == 0) return null;
			if (lods.GetArrayLength() > 1)
				ctx.Gap("LOD levels 1.." + (lods.GetArrayLength() - 1), "no-equivalent",
					"Only LOD 0 is converted; Effekseer's LOD model does not take per-level module lists.");

			var lod = Resolve(ctx, lods[0]);
			if (lod.ValueKind == JsonValueKind.Undefined) return null;

			var required = Resolve(ctx, Prop(lod, "RequiredModule"));
			var spawn = Resolve(ctx, Prop(lod, "SpawnModule"));
			var typeData = Resolve(ctx, Prop(lod, "TypeDataModule"));

			// A disabled level or required module means Cascade never draws this
			// emitter. Converting it anyway puts particles on screen that the
			// source effect does not show. An absent flag means enabled.
			if (Prop(lod, "bEnabled").ValueKind == JsonValueKind.False
				|| Prop(required, "bEnabled").ValueKind == JsonValueKind.False)
			{
				ctx.Kept("Emitter " + NameOf(emitter) + " is switched off in Cascade  ->  not converted");
				return null;
			}

			var modules = new List<JsonElement>();
			var moduleList = Prop(lod, "Modules");
			if (moduleList.ValueKind == JsonValueKind.Array)
				foreach (var m in moduleList.EnumerateArray())
				{
					var r = Resolve(ctx, m);
					if (r.ValueKind != JsonValueKind.Undefined) modules.Add(r);
				}

			var node = new Dictionary<string, object>
			{
				["name"] = NameOf(emitter),
				["source"] = new Dictionary<string, object>
				{
					["emitter"] = NameOf(emitter),
					["modules"] = modules.Select(TypeOf).Concat(new[] { TypeOf(required), TypeOf(spawn), TypeOf(typeData) })
										 .Where(s => !string.IsNullOrEmpty(s)).Distinct().ToList(),
				},
			};

			// ---- lifetime ------------------------------------------------------
			float lifeMinSec = 1.0f, lifeMaxSec = 1.0f;
			var lifetime = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleLifetime");
			if (lifetime.ValueKind != JsonValueKind.Undefined)
				ReadScalar(ctx, Prop(lifetime, "Lifetime"), out lifeMinSec, out lifeMaxSec);

			int frameMin = Math.Max(1, (int)Math.Round(lifeMinSec * FramesPerSecond));
			int frameMax = Math.Max(frameMin, (int)Math.Round(lifeMaxSec * FramesPerSecond));
			ctx.Kept(string.Format("Lifetime {0}-{1} s  ->  life {2}-{3} frames", lifeMinSec, lifeMaxSec, frameMin, frameMax));

			// ---- spawn rate, bursts and count ------------------------------------
			// Cascade spawns from three places at once and they only make sense read
			// together: a continuous rate, a list of bursts, and a duration and loop
			// count that decide when the emitter stops. Reading the rate alone turned
			// a one-shot cast into a permanent emitter, and turned a burst-only
			// emitter into a particle every frame forever, because a rate of zero
			// fell through to a one-frame interval instead of meaning "burst only".
			float rate = 0;
			int burstTotal = 0;
			bool lateBurst = false, randomBurst = false;
			if (spawn.ValueKind != JsonValueKind.Undefined)
			{
				float lo, hi;
				ReadScalar(ctx, Prop(spawn, "Rate"), out lo, out hi);
				rate = Math.Max(lo, hi);

				// A rate can be a curve, and a curve read as two numbers looks exactly
				// like a random range. Taking the peak is the right guess and the wrong
				// shape, so the difference belongs in the report.
				var rateTable = Prop(Prop(spawn, "Rate"), "Table");
				if ((int)FloatProp(rateTable, "EntryCount", 1) > 1 && (int)FloatProp(rateTable, "Op", 1) == 1)
					ctx.Gap(string.Format("Spawn rate ramping from {0} to {1}/s", lo, hi), "approximated",
						"Effekseer generates on one interval for the node's whole life, so the peak rate is used throughout and the ramp is lost.");
				if (ReadScalar(ctx, Prop(spawn, "RateScale"), out lo, out hi))
					rate *= Math.Max(0f, (lo + hi) * 0.5f);

				var bursts = Prop(spawn, "BurstList");
				if (bursts.ValueKind == JsonValueKind.Array)
					foreach (var burst in bursts.EnumerateArray())
					{
						int count = (int)FloatProp(burst, "Count", 0);
						int low = (int)FloatProp(burst, "CountLow", -1);
						// A CountLow of -1 means the count is exact. Otherwise Cascade
						// picks between the two, and maxGeneration needs one number.
						if (low >= 0 && low != count) { count = (low + count + 1) / 2; randomBurst = true; }
						if (count <= 0) continue;
						burstTotal += count;
						if (FloatProp(burst, "Time", 0) > 0.0001f) lateBurst = true;
					}
			}

			// A node has one generation interval, so a rate and a burst cannot both
			// be honoured. A rate wins where there is one and the burst joins the
			// total; with no rate, a near-zero interval is the burst idiom.
			float interval = 1.0f;
			if (rate > 0) interval = FramesPerSecond / rate;
			else if (burstTotal > 0) interval = 0.00001f;

			if (rate > 0)
				ctx.Kept(string.Format("Spawn rate {0}/s  ->  a particle every {1} frames",
					rate, interval.ToString("0.##", CultureInfo.InvariantCulture)));
			else if (burstTotal > 0)
				ctx.Kept(string.Format("Burst of {0}  ->  {0} particles at once", burstTotal));

			// An absent EmitterLoops is Cascade's own default of 0, which never stops.
			float duration = FloatProp(required, "EmitterDuration", 1.0f);
			int loops = (int)FloatProp(required, "EmitterLoops", 0);

			object maxGeneration = "infinite";
			if (loops > 0)
			{
				int total = ((int)Math.Round(rate * duration) + burstTotal) * loops;
				if (total > 0)
				{
					maxGeneration = total;
					ctx.Kept(string.Format(
						"{0} s x {1} loop(s) at rate {2} plus bursts of {3}  ->  maxGeneration {4}",
						duration, loops, rate, burstTotal, total));
				}
			}

			// A burst-only emitter that loops forever would otherwise pair a near-zero
			// interval with no cap, which generates without end from the first frame.
			// Cap it at a single burst and declare the repeat instead.
			if (maxGeneration is string && rate <= 0 && burstTotal > 0)
			{
				maxGeneration = burstTotal;
				ctx.Gap("A burst repeating every "
					+ duration.ToString("0.##", CultureInfo.InvariantCulture) + " s", "approximated",
					"A node bursts once, so the repeat is dropped and only the first burst is generated.");
			}

			if (maxGeneration is string && typeData.ValueKind != JsonValueKind.Undefined)
			{
				// Nothing to derive a total from, so fall back to the cook's own
				// concurrent peak, which is the only count a GPU emitter records.
				var info = Prop(typeData, "EmitterInfo");
				JsonElement count;
				int n;
				if (info.ValueKind == JsonValueKind.Object
					&& info.TryGetProperty("MaxParticleCount", out count) && count.TryGetInt32(out n) && n > 0)
				{
					maxGeneration = n;
					ctx.Kept("MaxParticleCount " + n + "  ->  maxGeneration " + n);
				}
			}

			if (rate > 0 && burstTotal > 0)
				ctx.Gap(string.Format("A burst of {0} on an emitter that also spawns at {1}/s", burstTotal, rate),
					"approximated",
					"Effekseer generates on one interval per node, so the burst arrives spread at the spawn rate rather than together. The count is right and the opening lump is not.");
			if (lateBurst)
				ctx.Gap("A burst scheduled after the emitter starts", "approximated",
					"A node generates from one moment only, so a later burst is folded into the first.");
			if (randomBurst)
				ctx.Gap("A burst whose count is a random range", "approximated",
					"maxGeneration is a single number, so the midpoint of the range is used.");

			// A multi-phase effect is sequenced by per-emitter delays, and Effekseer
			// has the same idea as a per-node offset. Without this every phase of a
			// cast fires at once and the effect reads as one flash.
			float delayFrames = FloatProp(required, "EmitterDelay", 0) * FramesPerSecond;
			if (delayFrames > 0.01f)
				ctx.Kept(string.Format("EmitterDelay {0} s  ->  generationTimeOffset {1} frames",
					FloatProp(required, "EmitterDelay", 0), delayFrames.ToString("0.#", CultureInfo.InvariantCulture)));

			node["common"] = new Dictionary<string, object>
			{
				["life"] = Range(frameMin, frameMax),
				["maxGeneration"] = maxGeneration,
				["generationTime"] = interval,
				["generationTimeOffset"] = delayFrames,
				["removeWhenLifeIsExtinct"] = true,
			};

			// ---- spawn shape ------------------------------------------------------
			// Cascade states its spawn volume in a primitive module, and the size of
			// that volume lives in StartRadius and StartHeight. An earlier version
			// matched any module whose name began with ParticleModuleLocation and
			// then read StartLocation, which on a primitive is the centre offset and
			// is almost always zero: every ring and every shell collapsed to a point.
			var spherePrimitive = modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleLocationPrimitiveSphere"));
			var cylinderPrimitive = modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleLocationPrimitiveCylinder"));
			var offsetModule = modules.FirstOrDefault(m => IsPlainLocation(TypeOf(m)));

			// Cascade is Z-up, Effekseer is Y-up, so the height axis moves from Z to Y.
			float[] offsetLo = null, offsetHi = null;
			if (offsetModule.ValueKind != JsonValueKind.Undefined)
			{
				float[] a, b;
				if (ReadVector(ctx, Prop(offsetModule, "StartLocation"), out a, out b))
				{
					offsetLo = new[] { a[0] * ctx.WorldScale, a[2] * ctx.WorldScale, a[1] * ctx.WorldScale };
					offsetHi = new[] { b[0] * ctx.WorldScale, b[2] * ctx.WorldScale, b[1] * ctx.WorldScale };
				}
			}

			Dictionary<string, object> spawnShape = null;

			// A ring spawn states what Effekseer's Circle wants and no more: a radius, a
			// sweep, and how many points around it. This converts better than the
			// cylinder does, because the division count is stated rather than invented.
			var ringShape = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSQEX_Ring");
			if (ringShape.ValueKind != JsonValueKind.Undefined)
			{
				float radiusLo, radiusHi, sweepLo, sweepHi, verticesLo, verticesHi;
				ReadScalar(ctx, Prop(ringShape, "RangeRadius"), out radiusLo, out radiusHi);
				if (!ReadScalar(ctx, Prop(ringShape, "DirectionAngle"), out sweepLo, out sweepHi)) sweepHi = 360;
				if (!ReadScalar(ctx, Prop(ringShape, "VertexNumber"), out verticesLo, out verticesHi)) verticesHi = 8;

				float ringLo = Math.Min(radiusLo, radiusHi) * ctx.WorldScale;
				float ringHi = Math.Max(radiusLo, radiusHi) * ctx.WorldScale;
				int division = Math.Max(1, Math.Min(64, (int)Math.Round(Math.Max(verticesLo, verticesHi))));

				spawnShape = Wrap("Circle", "circle", new Dictionary<string, object>
				{
					["axisDirection"] = "YAxis",
					["radius"] = Range(ringLo, ringHi),
					["angleStart"] = 0.0f,
					["angleEnd"] = Math.Max(sweepLo, sweepHi),
					["division"] = division,
					["type"] = "Random",
				});
				ctx.Kept(string.Format("Ring spawn radius {0}-{1} over {2} degrees in {3} steps  ->  Circle",
					Math.Min(radiusLo, radiusHi), Math.Max(radiusLo, radiusHi),
					Math.Max(sweepLo, sweepHi), division));

				if (Math.Abs(radiusHi - radiusLo) > 0.01f)
					ctx.Gap("Ring radius changing while the emitter runs", "approximated",
						"The source ring grows or shrinks as time passes, so every particle born at one moment shares a radius. Effekseer randomises the radius per particle instead, which covers the same ground without the sweep.");
				DeclareOutwardSpeed(ctx, ringShape, "Ring");
			}
			// A range spawn is a cone: a direction angle about the emitter's axis. A
			// sphere whose two spawn angles are clamped is that cone.
			else if (modules.Any(m => TypeOf(m) == "ParticleModuleSQEX_Range"))
			{
				var coneShape = modules.First(m => TypeOf(m) == "ParticleModuleSQEX_Range");
				float radiusLo, radiusHi, angleLo, angleHi;
				ReadScalar(ctx, Prop(coneShape, "RangeRadius"), out radiusLo, out radiusHi);
				if (!ReadScalar(ctx, Prop(coneShape, "DirectionAngle"), out angleLo, out angleHi)) angleHi = 180;

				float coneAngle = Math.Min(180.0f, Math.Max(1.0f, Math.Max(angleLo, angleHi)));
				float coneRadius = Math.Max(radiusLo, radiusHi) * ctx.WorldScale;

				spawnShape = new Dictionary<string, object>
				{
					["type"] = "Sphere",
					["radius"] = Range(Math.Min(radiusLo, radiusHi) * ctx.WorldScale, coneRadius),
					["rotationX"] = Range(-coneAngle, coneAngle),
					["rotationY"] = Range(-180, 180),
				};
				spawnShape = Wrap("Sphere", "sphere", spawnShape);
				ctx.Kept(string.Format("Range spawn, {0} degrees wide at radius {1}  ->  Sphere with the spawn angle clamped",
					coneAngle, coneRadius.ToString("0.##", CultureInfo.InvariantCulture)));

				float[] scaleLo, scaleHi;
				if (ReadVector(ctx, Prop(coneShape, "RangeScale"), out scaleLo, out scaleHi))
				{
					float widest = Math.Max(Math.Max(scaleHi[0], scaleHi[1]), scaleHi[2]);
					float narrowest = Math.Min(Math.Min(scaleHi[0], scaleHi[1]), scaleHi[2]);
					if (widest - narrowest > 0.01f)
						ctx.Gap(string.Format("Range stretched {0}/{1}/{2}", scaleHi[0], scaleHi[1], scaleHi[2]),
							"approximated",
							"The source stretches its spawn volume along one axis. Effekseer's sphere is round, so the cone converts at an even width and the stretch does not.");
				}

				float deviationLo, deviationHi;
				if (ReadScalar(ctx, Prop(coneShape, "Deviation"), out deviationLo, out deviationHi)
					&& Math.Max(deviationLo, deviationHi) > 0.001f)
					ctx.Gap("Range deviation " + Math.Max(deviationLo, deviationHi), "no-equivalent",
						"A scatter applied to the spawn direction on top of the cone angle. Effekseer's sphere spreads evenly between its two angles, so the scatter has nowhere to go.");

				DeclareOutwardSpeed(ctx, coneShape, "Range");
			}
			else if (spherePrimitive.ValueKind != JsonValueKind.Undefined)
			{
				float rLo, rHi;
				ReadScalar(ctx, Prop(spherePrimitive, "StartRadius"), out rLo, out rHi);
				float source = Math.Max(rLo, rHi);
				float radius = source * ctx.WorldScale;
				bool surfaceOnly = BoolProp(spherePrimitive, "SurfaceOnly");

				spawnShape = new Dictionary<string, object>
				{
					["type"] = "Sphere",
					// A shell keeps every particle at the full radius; a filled
					// volume spreads them from the centre outwards.
					["radius"] = Range(surfaceOnly ? radius : 0, radius),
					// Without spread on both angles every particle lands on the same
					// point of the sphere and the emitter looks like it made one.
					["rotationX"] = Range(-360, 360),
					["rotationY"] = Range(-360, 360),
				};
				spawnShape = Wrap("Sphere", "sphere", spawnShape);

				ctx.Kept(string.Format("Sphere spawn radius {0} uu, {1}  ->  Sphere radius {2}",
					source, surfaceOnly ? "surface only" : "whole volume",
					radius.ToString("0.##", CultureInfo.InvariantCulture)));
			}
			else if (cylinderPrimitive.ValueKind != JsonValueKind.Undefined)
			{
				float rLo, rHi, hLo, hHi;
				ReadScalar(ctx, Prop(cylinderPrimitive, "StartRadius"), out rLo, out rHi);
				ReadScalar(ctx, Prop(cylinderPrimitive, "StartHeight"), out hLo, out hHi);
				float sourceRadius = Math.Max(rLo, rHi);
				float sourceHeight = Math.Max(hLo, hHi);
				float radius = sourceRadius * ctx.WorldScale;
				bool surfaceOnly = BoolProp(cylinderPrimitive, "SurfaceOnly");

				spawnShape = Wrap("Circle", "circle", new Dictionary<string, object>
				{
					["axisDirection"] = "YAxis",
					["radius"] = Range(surfaceOnly ? radius : 0, radius),
					["angleStart"] = 0.0f,
					["angleEnd"] = 360.0f,
					["division"] = 8,
					["type"] = "Random",
				});

				ctx.Kept(string.Format("Cylinder spawn radius {0} uu  ->  Circle radius {1}",
					sourceRadius, radius.ToString("0.##", CultureInfo.InvariantCulture)));

				if (sourceHeight > 0.01f)
					ctx.Gap(string.Format("Cylinder spawn height {0} uu", sourceHeight), "approximated",
						"Effekseer's nearest spawn shape is a flat circle, so the particles start on one plane instead of "
						+ "being spread along the height of the cylinder.");
			}
			else if (offsetLo != null)
			{
				// A plain location module is a box: each axis has its own range.
				// Effekseer's Point spawn randomises per axis, which is that same
				// box, so this one converts exactly rather than becoming a sphere.
				bool spread = false;
				for (int i = 0; i < 3; i++) if (Math.Abs(offsetHi[i] - offsetLo[i]) > 0.0001f) spread = true;

				spawnShape = Wrap("Point", "point", new Dictionary<string, object>
				{
					["location"] = new Dictionary<string, object>
					{
						["x"] = Range(offsetLo[0], offsetHi[0]),
						["y"] = Range(offsetLo[1], offsetHi[1]),
						["z"] = Range(offsetLo[2], offsetHi[2]),
					},
				});

				ctx.Kept(spread
					? "Box spawn  ->  Point spawn randomised per axis, which is the same box"
					: string.Format("Emitter offset {0}/{1}/{2}  ->  Point spawn",
						offsetLo[0].ToString("0.##", CultureInfo.InvariantCulture),
						offsetLo[1].ToString("0.##", CultureInfo.InvariantCulture),
						offsetLo[2].ToString("0.##", CultureInfo.InvariantCulture)));

				offsetLo = null; // consumed by the spawn shape
			}

			if (spawnShape != null) node["generationLocation"] = spawnShape;

			// ---- where the particle starts and how it moves ----------------------
			// Effekseer integrates in FRAMES: position = location + velocity * t +
			// acceleration * t * t / 2, with t counted in frames. Cascade states
			// velocity per second and acceleration per second squared, so the two
			// need dividing by 60 and by 3600. Writing the raw numbers would run the
			// motion sixty times too fast and the fall three thousand times too fast.
			//
			// A primitive owns the spawn slot, so an offset alongside it becomes the
			// node's own position. These offsets are what stack a spell vertically;
			// dropping them puts every emitter at the origin.
			float cx = 0, cy = 0, cz = 0;
			if (spawnShape != null && offsetLo != null)
			{
				cx = (offsetLo[0] + offsetHi[0]) * 0.5f;
				cy = (offsetLo[1] + offsetHi[1]) * 0.5f;
				cz = (offsetLo[2] + offsetHi[2]) * 0.5f;
			}
			// The emitter can also carry an origin of its own, which is how a fork
			// offsets a whole emitter without a location module. It adds to any offset
			// a module gave, and at four hundred units it is not a rounding detail.
			var emitterOrigin = Prop(required, "EmitterOrigin");
			if (emitterOrigin.ValueKind == JsonValueKind.Object)
			{
				float ox = FloatProp(emitterOrigin, "X", 0), oy = FloatProp(emitterOrigin, "Y", 0), oz = FloatProp(emitterOrigin, "Z", 0);
				if (Math.Abs(ox) + Math.Abs(oy) + Math.Abs(oz) > 0.0001f)
				{
					cx += ox * ctx.WorldScale;
					cy += oz * ctx.WorldScale;
					cz += oy * ctx.WorldScale;
					ctx.Kept(string.Format("EmitterOrigin {0}/{1}/{2}  ->  node position", ox, oy, oz));
				}
			}

			bool hasOffset = Math.Abs(cx) + Math.Abs(cy) + Math.Abs(cz) > 0.0001f;

			// Cascade is Z-up and Effekseer is Y-up, so the height axis moves here too.
			float[] velLo = null, velHi = null;
			var velocityModule = modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleVelocity")
														&& !TypeOf(m).Contains("OverLifetime"));
			if (velocityModule.ValueKind != JsonValueKind.Undefined)
			{
				float[] a, b;
				if (ReadVector(ctx, Prop(velocityModule, "StartVelocity"), out a, out b))
				{
					float k = ctx.WorldScale / FramesPerSecond;
					velLo = new[] { a[0] * k, a[2] * k, a[1] * k };
					velHi = new[] { b[0] * k, b[2] * k, b[1] * k };
					ctx.Kept(string.Format("StartVelocity {0}/{1}/{2} to {3}/{4}/{5} uu/s  ->  PVA velocity per frame",
						a[0], a[1], a[2], b[0], b[1], b[2]));
				}

				float radialLo, radialHi;
				if (ReadScalar(ctx, Prop(velocityModule, "StartVelocityRadial"), out radialLo, out radialHi)
					&& Math.Max(Math.Abs(radialLo), Math.Abs(radialHi)) > 0.0001f)
					ctx.Gap(string.Format("StartVelocityRadial {0} to {1}", radialLo, radialHi), "no-equivalent",
						"The particle is pushed along its own radius from the emitter. Effekseer states velocity on fixed axes rather than radially, so there is nothing to write it into.");
			}

			// Several acceleration modules can stack on one emitter, and they simply
			// add. bAlwaysInWorldSpace is ignored: a node's PVA is its own frame.
			float ax = 0, ay = 0, az = 0;
			float accelScale = ctx.WorldScale / (FramesPerSecond * FramesPerSecond);
			foreach (var accelModule in modules.Where(m => TypeOf(m).StartsWith("ParticleModuleAcceleration")
													  && !TypeOf(m).Contains("Drag")))
			{
				var acceleration = Prop(accelModule, "Acceleration");
				if (acceleration.ValueKind != JsonValueKind.Object) continue;

				// The constant module states a bare vector. The plain one states a
				// distribution, and a real effect ramps gravity in over the first
				// fraction of a second, so the settled end is the value that matters.
				float sx, sy, sz;
				JsonElement direct;
				if (acceleration.TryGetProperty("X", out direct))
				{
					sx = FloatProp(acceleration, "X", 0);
					sy = FloatProp(acceleration, "Y", 0);
					sz = FloatProp(acceleration, "Z", 0);
				}
				else
				{
					float[] first, last;
					if (!ReadCurveEnds(acceleration, 3, out first, out last)) continue;
					sx = last[0]; sy = last[1]; sz = last[2];

					if (Math.Abs(first[0] - last[0]) + Math.Abs(first[1] - last[1])
					  + Math.Abs(first[2] - last[2]) > 0.001f)
						ctx.Gap(string.Format("Acceleration ramping from {0}/{1}/{2}", first[0], first[1], first[2]),
							"approximated",
							"Effekseer integrates one constant acceleration, so the settled value is used and the ramp into it is lost. A particle that eases into its fall starts falling at once.");
				}

				ax += sx * accelScale;
				ay += sz * accelScale;
				az += sy * accelScale;
				ctx.Kept(string.Format("Acceleration {0}/{1}/{2} uu/s2  ->  PVA acceleration per frame", sx, sy, sz));
			}
			bool hasAccel = Math.Abs(ax) + Math.Abs(ay) + Math.Abs(az) > 0.0000001f;

			if (velLo != null || hasAccel)
			{
				var pva = new Dictionary<string, object>();
				if (hasOffset)
					pva["location"] = new Dictionary<string, object> { ["x"] = cx, ["y"] = cy, ["z"] = cz };
				if (velLo != null)
					pva["velocity"] = new Dictionary<string, object>
					{
						["x"] = Range(velLo[0], velHi[0]),
						["y"] = Range(velLo[1], velHi[1]),
						["z"] = Range(velLo[2], velHi[2]),
					};
				if (hasAccel)
					pva["acceleration"] = new Dictionary<string, object> { ["x"] = ax, ["y"] = ay, ["z"] = az };

				node["location"] = new Dictionary<string, object> { ["type"] = "PVA", ["pva"] = pva };
			}
			else if (hasOffset)
			{
				node["location"] = new Dictionary<string, object>
				{
					["type"] = "Fixed",
					["fixedOffset"] = new Dictionary<string, object> { ["x"] = cx, ["y"] = cy, ["z"] = cz },
				};
			}

			if (hasOffset)
				ctx.Kept(string.Format("Emitter offset {0}/{1}/{2}  ->  node position",
					cx.ToString("0.##", CultureInfo.InvariantCulture),
					cy.ToString("0.##", CultureInfo.InvariantCulture),
					cz.ToString("0.##", CultureInfo.InvariantCulture)));

			// A Cascade primitive can also throw the particle outward along the axis
			// it spawned on, which on a shell burst is the whole of the motion.
			foreach (var primitive in new[] { spherePrimitive, cylinderPrimitive })
			{
				if (primitive.ValueKind == JsonValueKind.Undefined) continue;
				if (!BoolProp(primitive, "Velocity")) continue;

				float scaleLo, scaleHi;
				if (!ReadScalar(ctx, Prop(primitive, "VelocityScale"), out scaleLo, out scaleHi)) scaleLo = scaleHi = 1;
				ctx.Gap(string.Format("Spawn shape throwing the particle outward at {0} to {1}", scaleLo, scaleHi),
					"no-equivalent",
					"The shape gives each particle a velocity along its own radius. Effekseer states velocity on fixed axes, so the shape converts and the outward throw does not.");
			}

			if (modules.Any(m => TypeOf(m) == "ParticleModuleVelocityOverLifetime"))
				ctx.Gap("ParticleModuleVelocityOverLifetime", "no-equivalent",
					"Cascade multiplies the particle's velocity by a curve across its life. Effekseer integrates one constant velocity and one acceleration, so a speed that changes shape, or reverses, cannot be written.");
			// ---- spin -------------------------------------------------------------
			// Cascade states rotation in TURNS, where 1.0 is a full circle, and rates
			// in turns per second. Effekseer authors degrees and, like every other
			// rate here, per frame. So a turn is 360 and a turn per second is 6.
			// A fork can state the same thing in DEGREES instead, so the unit belongs
			// to the module and not to the engine. Check for that one first: reading it
			// as turns would multiply a 360 degree spread into a thousand revolutions.
			float[] spinLo = null, spinHi = null, spinRateLo = null, spinRateHi = null;

			var degreeRotation = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSQEX_ParticleRotation");
			if (degreeRotation.ValueKind != JsonValueKind.Undefined)
			{
				float[] a, b;
				if (ReadVector(ctx, Prop(degreeRotation, "StartParticleRotation"), out a, out b))
				{
					spinLo = new[] { a[0], a[2], a[1] };
					spinHi = new[] { b[0], b[2], b[1] };
				}

				// The rate is degrees per second here, so only the frame rate divides it.
				if (ReadVector(ctx, Prop(degreeRotation, "ParticleRotation"), out a, out b))
				{
					float scale = 1.0f / FramesPerSecond;
					spinRateLo = new[] { a[0] * scale, a[2] * scale, a[1] * scale };
					spinRateHi = new[] { b[0] * scale, b[2] * scale, b[1] * scale };
				}

				float[] scaleLo, scaleHi;
				if (ReadVector(ctx, Prop(degreeRotation, "ParticleRotationScale"), out scaleLo, out scaleHi)
					&& Math.Abs(scaleLo[0] - 1.0f) + Math.Abs(scaleHi[0] - 1.0f) > 0.001f)
					ctx.Gap("ParticleRotationScale", "no-equivalent",
						"A second multiplier on the spin, applied by the source on top of the rate. Effekseer has one rate, so the multiplier is dropped rather than folded in blind.");
			}

			var meshRotation = spinLo != null ? default(JsonElement)
				: modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleMeshRotation")
														&& !TypeOf(m).Contains("Rate"));
			if (meshRotation.ValueKind != JsonValueKind.Undefined)
			{
				float[] a, b;
				if (ReadVector(ctx, Prop(meshRotation, "StartRotation"), out a, out b))
				{
					spinLo = new[] { a[0] * 360.0f, a[2] * 360.0f, a[1] * 360.0f };
					spinHi = new[] { b[0] * 360.0f, b[2] * 360.0f, b[1] * 360.0f };
				}
			}
			else
			{
				var spriteRotation = modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleRotation")
															&& !TypeOf(m).Contains("Rate"));
				if (spriteRotation.ValueKind != JsonValueKind.Undefined)
				{
					float turnLo, turnHi;
					if (ReadScalar(ctx, Prop(spriteRotation, "StartRotation"), out turnLo, out turnHi))
					{
						// A billboard turns about the axis facing the camera.
						spinLo = new[] { 0.0f, 0.0f, turnLo * 360.0f };
						spinHi = new[] { 0.0f, 0.0f, turnHi * 360.0f };
					}
				}
			}

			var meshRotationRate = spinRateLo != null ? default(JsonElement)
				: modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleMeshRotationRate"));
			if (meshRotationRate.ValueKind != JsonValueKind.Undefined)
			{
				float[] a, b;
				if (ReadVector(ctx, Prop(meshRotationRate, "StartRotationRate"), out a, out b))
				{
					spinRateLo = new[] { a[0] * 6.0f, a[2] * 6.0f, a[1] * 6.0f };
					spinRateHi = new[] { b[0] * 6.0f, b[2] * 6.0f, b[1] * 6.0f };
				}
			}
			else
			{
				var spriteRotationRate = modules.FirstOrDefault(m => TypeOf(m).StartsWith("ParticleModuleRotationRate"));
				if (spriteRotationRate.ValueKind != JsonValueKind.Undefined)
				{
					float rateLo, rateHi;
					if (ReadScalar(ctx, Prop(spriteRotationRate, "StartRotationRate"), out rateLo, out rateHi))
					{
						spinRateLo = new[] { 0.0f, 0.0f, rateLo * 6.0f };
						spinRateHi = new[] { 0.0f, 0.0f, rateHi * 6.0f };
					}
				}
			}

			// An emitter rotation orients the whole emitter. Effekseer states rotation
			// on the particle, so folding it in puts the geometry the right way up,
			// which is what it is for; it is not the same thing and is said so.
			var emitterRotation = Prop(required, "EmitterRotation");
			if (emitterRotation.ValueKind == JsonValueKind.Object)
			{
				float roll = FloatProp(emitterRotation, "Roll", 0);
				float pitch = FloatProp(emitterRotation, "Pitch", 0);
				float yaw = FloatProp(emitterRotation, "Yaw", 0);
				if (Math.Abs(roll) + Math.Abs(pitch) + Math.Abs(yaw) > 0.001f)
				{
					if (spinLo == null) { spinLo = new float[3]; spinHi = new float[3]; }
					spinLo[0] += roll; spinHi[0] += roll;
					spinLo[1] += yaw;  spinHi[1] += yaw;
					spinLo[2] += pitch; spinHi[2] += pitch;
					ctx.Gap(string.Format("EmitterRotation {0}/{1}/{2} degrees", roll, pitch, yaw), "approximated",
						"Applied to the particle rather than to the emitter, because that is where Effekseer states rotation. The geometry faces the right way; anything that should have been placed by the rotated emitter and then moved independently will not.");
				}
			}

			if (spinLo != null || spinRateLo != null)
			{
				var spin = new Dictionary<string, object>();
				if (spinLo != null)
				{
					spin["rotation"] = new Dictionary<string, object>
					{
						["x"] = Range(spinLo[0], spinHi[0]),
						["y"] = Range(spinLo[1], spinHi[1]),
						["z"] = Range(spinLo[2], spinHi[2]),
					};
					ctx.Kept(string.Format("Start rotation  ->  {0}/{1}/{2} to {3}/{4}/{5} degrees",
						spinLo[0].ToString("0.#", CultureInfo.InvariantCulture),
						spinLo[1].ToString("0.#", CultureInfo.InvariantCulture),
						spinLo[2].ToString("0.#", CultureInfo.InvariantCulture),
						spinHi[0].ToString("0.#", CultureInfo.InvariantCulture),
						spinHi[1].ToString("0.#", CultureInfo.InvariantCulture),
						spinHi[2].ToString("0.#", CultureInfo.InvariantCulture)));
				}
				if (spinRateLo != null)
				{
					spin["velocity"] = new Dictionary<string, object>
					{
						["x"] = Range(spinRateLo[0], spinRateHi[0]),
						["y"] = Range(spinRateLo[1], spinRateHi[1]),
						["z"] = Range(spinRateLo[2], spinRateHi[2]),
					};
					ctx.Kept(string.Format("StartRotationRate  ->  {0} to {1} degrees per frame",
						spinRateLo[2].ToString("0.##", CultureInfo.InvariantCulture),
						spinRateHi[2].ToString("0.##", CultureInfo.InvariantCulture)));
				}
				node["rotation"] = new Dictionary<string, object> { ["type"] = "PVA", ["pva"] = spin };
			}

			// Cascade slides a particle toward the camera to keep it clear of the
			// geometry behind it. Effekseer's depth offset is the same idea.
			var depthModule = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSQEX_DepthOffset");
			if (depthModule.ValueKind != JsonValueKind.Undefined)
			{
				float stated = FloatProp(depthModule, "DepthOffset", 0);
				if (Math.Abs(stated) > 0.001f)
				{
					node["depthOffset"] = stated * ctx.WorldScale;
					ctx.Kept(string.Format("DepthOffset {0}  ->  node depth offset", stated));
				}
			}

			var cameraOffset = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleCameraOffset");
			if (cameraOffset.ValueKind != JsonValueKind.Undefined)
			{
				float offsetLow, offsetHigh;
				if (ReadScalar(ctx, Prop(cameraOffset, "CameraOffset"), out offsetLow, out offsetHigh))
				{
					float depth = (offsetLow + offsetHigh) * 0.5f * ctx.WorldScale;
					node["depthOffset"] = depth;
					ctx.Gap(string.Format("CameraOffset {0} uu", ((offsetLow + offsetHigh) * 0.5f)
							.ToString("0.##", CultureInfo.InvariantCulture)), "approximated",
						"Converted to Effekseer's depth offset, which slides the particle along the view direction the same way. Which way positive points was not verified against a render, so a sprite meant to sit in front may sort behind.");
				}
			}

			// ---- size, and how it changes across the life -------------------------
			// Cascade states a sprite size in world units on X and Y; only X is read
			// because the two engines do not share a base quad and the factor that
			// bridges them is chosen anyway. The min and max are the source's own
			// spread, which is better than inventing one.
			float sizeLo = 1.0f, sizeHi = 1.0f;
			var size = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSize");
			if (size.ValueKind != JsonValueKind.Undefined)
			{
				float[] lo, hi;
				if (ReadVector(ctx, Prop(size, "StartSize"), out lo, out hi))
				{
					sizeLo = Math.Max(0.0001f, Math.Min(lo[0], hi[0]));
					sizeHi = Math.Max(sizeLo, Math.Max(lo[0], hi[0]));
				}
			}
			float drawLo = sizeLo * ctx.SpriteScale, drawHi = sizeHi * ctx.SpriteScale;

			// A size multiplier across the life is two ends and a shape between them.
			// Effekseer's SingleEasing holds the two ends, so a particle that swells
			// and fades arrives swelling; only the shape of the middle is lost.
			float[] multiplierFirst = null, multiplierLast = null;
			// Two module names do this one job, and they name the field differently.
			// A size scale keyed to EMITTER time rather than particle age is a
			// different thing again, and Effekseer eases across the particle's life.
			var sizeOverLife = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSizeMultiplyLife");
			if (sizeOverLife.ValueKind != JsonValueKind.Undefined)
			{
				ReadCurveEnds(Prop(sizeOverLife, "LifeMultiplier"), 3, out multiplierFirst, out multiplierLast);
			}
			else
			{
				sizeOverLife = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSizeScale");
				if (sizeOverLife.ValueKind != JsonValueKind.Undefined)
				{
					ReadCurveEnds(Prop(sizeOverLife, "SizeScale"), 3, out multiplierFirst, out multiplierLast);
					if (BoolProp(sizeOverLife, "bUseEmitterTime"))
						ctx.Gap("SizeScale keyed to emitter time", "approximated",
							"The source scales by how long the emitter has been running, so every particle alive at one moment shares a size. Effekseer eases across each particle's own life instead, so late particles will not be small.");
				}
			}

			if (multiplierFirst != null && multiplierLast != null)
			{
				float startMul = multiplierFirst[0], endMul = multiplierLast[0];
				node["scaling"] = new Dictionary<string, object>
				{
					["type"] = "SingleEasing",
					["singleEasing"] = new Dictionary<string, object>
					{
						["start"] = Range(drawLo * startMul, drawHi * startMul),
						["end"] = Range(drawLo * endMul, drawHi * endMul),
					},
				};
				ctx.Kept(string.Format("SizeMultiplyLife {0} to {1}  ->  scale eased from {2} to {3}",
					startMul.ToString("0.##", CultureInfo.InvariantCulture),
					endMul.ToString("0.##", CultureInfo.InvariantCulture),
					(drawLo * startMul).ToString("0.##", CultureInfo.InvariantCulture),
					(drawHi * endMul).ToString("0.##", CultureInfo.InvariantCulture)));

				var multiplierTable = Prop(Prop(sizeOverLife, "LifeMultiplier"), "Table");
				int multiplierEntries = (int)FloatProp(multiplierTable, "EntryCount", 1);
				if (multiplierEntries > 2)
					ctx.Gap(string.Format("SizeMultiplyLife curve of {0} entries", multiplierEntries), "approximated",
						"Effekseer eases between two ends, so a size that peaks part way through and comes back arrives as a straight ramp between its first and last value.");
			}
			else
			{
				node["scaling"] = new Dictionary<string, object>
				{
					["type"] = "SinglePVA",
					["singlePVA"] = new Dictionary<string, object> { ["scale"] = Range(drawLo, drawHi) },
				};
			}

			ctx.Gap(string.Format("StartSize {0} to {1} uu",
					sizeLo.ToString("0.##", CultureInfo.InvariantCulture),
					sizeHi.ToString("0.##", CultureInfo.InvariantCulture)), "approximated",
				string.Format("Sprite scale {0} to {1}. The two engines do not share a base sprite quad, so this factor is chosen, not derived.",
					drawLo.ToString("0.##", CultureInfo.InvariantCulture),
					drawHi.ToString("0.##", CultureInfo.InvariantCulture)));

			// ---- colour --------------------------------------------------------------
			int r8 = 255, g8 = 255, b8 = 255, a8 = 255;
			var colour = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleColor");
			if (colour.ValueKind != JsonValueKind.Undefined)
			{
				float[] lo, hi;
				if (ReadVector(ctx, Prop(colour, "StartColor"), out lo, out hi))
				{
					float cr = (lo[0] + hi[0]) * 0.5f, cg = (lo[1] + hi[1]) * 0.5f, cb = (lo[2] + hi[2]) * 0.5f;
					float peak = Math.Max(1.0f, Math.Max(cr, Math.Max(cg, cb)));

					r8 = Clamp255(cr / peak * 255.0f);
					g8 = Clamp255(cg / peak * 255.0f);
					b8 = Clamp255(cb / peak * 255.0f);

					if (peak > 1.0f)
						ctx.Gap(string.Format("StartColor {0}/{1}/{2}", cr, cg, cb), "approximated",
							string.Format("Values above 1.0 drive bloom in Unreal. Effekseer colour is 8-bit, so the hue is kept "
								+ "as {0}/{1}/{2} and the overdrive is emulated by additive blending alone.", r8, g8, b8));
					else
						ctx.Kept(string.Format("StartColor {0}/{1}/{2}  ->  RGB {3}/{4}/{5}", cr, cg, cb, r8, g8, b8));
				}

				float alphaMin, alphaMax;
				if (ReadScalar(ctx, Prop(colour, "StartAlpha"), out alphaMin, out alphaMax))
					a8 = Clamp255((alphaMin + alphaMax) * 0.5f * 255.0f);
			}

			// Colour is carried by two different modules and only one of them is a
			// constant. An earlier version read the constant alone, so every emitter
			// whose colour ramps over the particle's life came out white.
			int[] easeStart = null, easeEnd = null;
			var colourOverLife = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleColorOverLife");
			if (colourOverLife.ValueKind != JsonValueKind.Undefined)
			{
				float[] first, last;
				if (ReadCurveEnds(Prop(colourOverLife, "ColorOverLife"), 3, out first, out last))
				{
					// Both ends share one divisor. Normalising them separately would
					// turn a ramp that dims into one that holds steady.
					float peak = 1.0f;
					for (int i = 0; i < 3; i++) peak = Math.Max(peak, Math.Max(first[i], last[i]));

					int alphaHead = a8, alphaTail = a8;
					var alphaCurve = Prop(colourOverLife, "AlphaOverLife");
					float[] aFirst, aLast;
					if (ReadCurveEnds(alphaCurve, 1, out aFirst, out aLast))
					{
						// These curves usually rise from zero, peak, then fall. Easing
						// only interpolates between two ends, so taking the real first
						// sample would leave the particle invisible for its whole life.
						// The peak is the honest stand-in for the head of the curve.
						alphaHead = Clamp255(TablePeak(alphaCurve) * 255.0f);
						alphaTail = Clamp255(aLast[0] * 255.0f);
					}

					easeStart = new[]
					{
						Clamp255(first[0] / peak * 255.0f), Clamp255(first[1] / peak * 255.0f),
						Clamp255(first[2] / peak * 255.0f), alphaHead,
					};
					easeEnd = new[]
					{
						Clamp255(last[0] / peak * 255.0f), Clamp255(last[1] / peak * 255.0f),
						Clamp255(last[2] / peak * 255.0f), alphaTail,
					};

					ctx.Gap(string.Format("ColorOverLife {0}/{1}/{2} to {3}/{4}/{5}",
							first[0], first[1], first[2], last[0], last[1], last[2]),
						"approximated",
						string.Format("Cascade samples colour from a curve of up to 128 entries. Effekseer eases between two ends, "
							+ "so the ramp keeps its start {0}/{1}/{2} and its finish {3}/{4}/{5} and loses the shape between them.",
							easeStart[0], easeStart[1], easeStart[2], easeEnd[0], easeEnd[1], easeEnd[2]));
				}
			}

			int colourScalers = modules.Count(m => TypeOf(m) == "ParticleModuleColorScaleOverLife");
			if (colourScalers > 0)
				ctx.Gap(colourScalers + " x ColorScaleOverLife", "no-equivalent",
					"Cascade multiplies the particle colour by a chain of further curves. An Effekseer node has one colour group, "
					+ "so the chain cannot be folded in. Where the source drives its look from material parameters rather than "
					+ "from vertex colour, multiplying these in would darken the effect rather than tint it.");

			var dynamicParams = modules.Where(m => TypeOf(m) == "ParticleModuleParameterDynamic").ToList();
			if (dynamicParams.Count > 0)
			{
				var names = new List<string>();
				foreach (var m in dynamicParams)
				{
					var list = Prop(m, "DynamicParams");
					if (list.ValueKind != JsonValueKind.Array) continue;
					foreach (var p in list.EnumerateArray())
					{
						string n = StringProp(p, "ParamName");
						if (!string.IsNullOrEmpty(n) && !names.Contains(n)) names.Add(n);
					}
				}

				ctx.Gap(string.Format("{0} x ParameterDynamic ({1})", dynamicParams.Count, string.Join(", ", names)),
					"material-driven",
					"These feed named scalars into the Unreal material, each on its own curve. Effekseer has no path from a "
					+ "particle to a shader parameter, so everything they drive is lost. Where an effect is built this way, "
					+ "they and not the vertex colour are what the viewer actually sees.");
			}

			// ---- material and renderer -------------------------------------------------
			string materialName = "(none)";
			if (required.ValueKind != JsonValueKind.Undefined)
			{
				var material = Prop(required, "Material");
				if (material.ValueKind == JsonValueKind.Object)
					materialName = StringProp(material, "ObjectName") ?? "(unnamed)";

				ctx.Gap("Material " + materialName, "material-driven",
					"An Unreal material cannot become an Effekseer material. The document names a texture the dump does not "
					+ "supply, so the mapper substitutes its placeholder and says so.");

				string alignment = StringProp(required, "ScreenAlignment");
				if (!string.IsNullOrEmpty(alignment) && alignment.Contains("PSA_Velocity"))
					ctx.Gap("ScreenAlignment PSA_Velocity", "no-equivalent",
						"Effekseer sprites cannot align to velocity. Billboard is used instead, so the motion streaking is lost. "
						+ "A Track renderer would trail, but it draws one ribbon per particle rather than a stretched sprite.");
			}

			// ---- flipbooks -----------------------------------------------------------
			// The sheet is declared on the required module, not on the SubUV module:
			// SubUV only says how the cell index moves. A grid of one cell is not a
			// flipbook at all.
			Dictionary<string, object> uv = null;
			int sheetX = Math.Max(1, (int)FloatProp(required, "SubImages_Horizontal", 1));
			int sheetY = Math.Max(1, (int)FloatProp(required, "SubImages_Vertical", 1));

			if (sheetX * sheetY > 1)
			{
				int cells = sheetX * sheetY;
				string method = StringProp(required, "InterpolationMethod") ?? "";
				bool random = method.Contains("Random");
				bool blended = method.Contains("Blend");

				// Cascade sweeps the cell index across the particle's life, so a cell
				// is held for a life divided by the number of cells. Effekseer counts
				// that hold in frames.
				int hold = Math.Max(1, (int)Math.Round((double)frameMin / cells));

				var animation = new Dictionary<string, object>
				{
					["frameCountX"] = sheetX,
					["frameCountY"] = sheetY,
					["frameLength"] = hold,
					["loopType"] = "Once",
					["interpolation"] = blended ? "Lerp" : "None",
				};

				// A random method gives each particle one cell and keeps it there.
				// Effekseer says the same thing with a random starting sheet and a
				// hold longer than the particle lives.
				if (random)
				{
					animation["frameLength"] = Math.Max(1, frameMax);
					animation["startSheet"] = Range(0, cells - 1);
					animation["interpolation"] = "None";
				}

				uv = new Dictionary<string, object> { ["type"] = "Animation", ["animation"] = animation };

				ctx.Kept(string.Format("SubUV {0}x{1}  ->  UV animation, {2} frame(s) per cell{3}",
					sheetX, sheetY, animation["frameLength"],
					random ? ", one random cell per particle" : blended ? ", blended" : ""));

				var subUv = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleSubUV");
				if (subUv.ValueKind != JsonValueKind.Undefined && !random)
					ctx.Gap(string.Format("SubUV index curve across {0} cells", cells), "approximated",
						"Cascade drives the cell from a curve, which can dwell on a frame or run backwards. Effekseer holds "
						+ "every cell for the same number of frames, so the sheet plays at an even rate.");
			}

			// Blending had been assumed additive, on the grounds that the mode lives in
			// the material graph and a dump only names the material. That holds for
			// stock Cascade, but a fork can put the mode on the required module, and one
			// does. Where it is stated, use it; an additive guess turns every soft smoke
			// into a glowing one.
			string blendMode = "Add";
			string declaredBlend = StringProp(required, "EmitterBlendType");
			if (!string.IsNullOrEmpty(declaredBlend))
			{
				if (declaredBlend.Contains("Additive")) blendMode = "Add";
				else if (declaredBlend.Contains("Modulate")) blendMode = "Mul";
				else if (declaredBlend.Contains("Translucent")) blendMode = "Blend";
				else if (declaredBlend.Contains("Masked") || declaredBlend.Contains("Opaque")) blendMode = "Opacity";
				else declaredBlend = null;

				if (declaredBlend != null)
					ctx.Kept(declaredBlend + "  ->  alphaBlend " + blendMode);
			}
			if (string.IsNullOrEmpty(declaredBlend))
				ctx.Gap("Blend mode", "material-driven",
					"Not stated on the emitter, so it lives in the material graph, which a dump does not carry. Assumed additive, which is wrong for anything authored to blend.");

			string textureFile = "Texture/" + Sanitise(StripClass(materialName)) + ".png";
			var rendererCommon = new Dictionary<string, object>
			{
				["colorTexture"] = new Dictionary<string, object>
				{
					["file"] = textureFile,
					["source"] = new Dictionary<string, object>
					{
						["unrealMaterial"] = StripClass(materialName),
						["note"] = "Cascade names a material, not a texture. No art is present in the dump.",
					},
				},
				["alphaBlend"] = blendMode,
			};

			// The automatic fade is a stand-in for an alpha curve we could not read.
			// Once the curve is read and ends at zero, adding a fade on top of it
			// would make the particle vanish twice.
			bool easingFades = easeEnd != null && easeEnd[3] <= 8;
			if (!easingFades)
				rendererCommon["fadeOut"] = new Dictionary<string, object>
				{
					["type"] = "WithinLifetime",
					["frame"] = Math.Max(4.0f, frameMin * 0.35f),
				};

			if (uv != null) rendererCommon["uv"] = uv;

			node["rendererCommon"] = rendererCommon;

			var sprite = new Dictionary<string, object> { ["billboard"] = "Billboard" };
			if (easeStart != null)
			{
				sprite["colorAll"] = "Easing";
				sprite["colorAll_Easing"] = new Dictionary<string, object>
				{
					["start"] = easeStart,
					["end"] = easeEnd,
				};
			}
			else
			{
				sprite["colorAll"] = "Fixed";
				sprite["colorAll_Fixed"] = new[] { r8, g8, b8, a8 };
			}

			node["renderer"] = new Dictionary<string, object>
			{
				["type"] = "Sprite",
				["sprite"] = sprite,
			};

			// ---- the flow field -----------------------------------------------------------
			var forceFields = new List<Dictionary<string, object>>();
			var vectorField = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleVectorFieldLocal");
			if (vectorField.ValueKind != JsonValueKind.Undefined)
			{
				var field = Prop(vectorField, "VectorField");
				string fieldName = field.ValueKind == JsonValueKind.Object
					? StripClass(StringProp(field, "ObjectName") ?? "(unnamed)") : "(unnamed)";

				float intensity = FloatProp(vectorField, "Intensity", 1.0f);
				float tightness = FloatProp(vectorField, "Tightness", 1.0f);

				float fieldScale = 40.0f;
				var relativeScale = Prop(vectorField, "RelativeScale3D");
				if (relativeScale.ValueKind == JsonValueKind.Object) fieldScale = FloatProp(relativeScale, "X", 40.0f);

				float lift = 0;
				var translation = Prop(vectorField, "RelativeTranslation");
				if (translation.ValueKind == JsonValueKind.Object) lift = FloatProp(translation, "Z", 0);

				// Effekseer can turn a force field before it acts, so a field the source
				// turned converts. UE writes that turn as a Rotator, whose Pitch and Yaw
				// are about Y and Z, and the axis swap moves them the way a position
				// moves. Handedness is immaterial here: mirrored noise is still noise.
				Dictionary<string, object> fieldRotation = null;
				var relativeRotation = Prop(vectorField, "RelativeRotation");
				if (relativeRotation.ValueKind == JsonValueKind.Object)
				{
					float roll = FloatProp(relativeRotation, "Roll", 0);
					float pitch = FloatProp(relativeRotation, "Pitch", 0);
					float yaw = FloatProp(relativeRotation, "Yaw", 0);
					if (Math.Abs(roll) + Math.Abs(pitch) + Math.Abs(yaw) > 0.001f)
					{
						fieldRotation = new Dictionary<string, object> { ["x"] = roll, ["y"] = yaw, ["z"] = pitch };
						ctx.Kept(string.Format("VectorField turned {0}/{1}/{2} degrees  ->  the force field's own orientation",
							roll, pitch, yaw));
					}
				}

				// Turbulence supplies the curl. Force strength is deliberately NOT
				// multiplied by the world scale: scaling the distances up and the
				// forces with them compounds, and the particles get thrown clear
				// of the effect instead of milling around inside it.
				var turbulenceField = new Dictionary<string, object>
				{
					["type"] = "Turbulence",
					["power"] = intensity * ctx.TurbulenceGain,
					["turbulence"] = new Dictionary<string, object>
					{
						["fieldScale"] = Math.Max(0.5f, fieldScale * ctx.WorldScale),
						["octave"] = Math.Max(1, Math.Min(10, (int)Math.Round(tightness))),
						["seed"] = 1,
					},
				};
				if (fieldRotation != null) turbulenceField["rotation"] = fieldRotation;
				forceFields.Add(turbulenceField);

				// The field sits above the emitter, so the sign of its offset gives
				// the direction of the rise.
				if (Math.Abs(lift) > 0.001f)
				{
					float up = Math.Sign(lift) * intensity * ctx.LiftGain;
					forceFields.Add(new Dictionary<string, object>
					{
						["type"] = "Gravity",
						["gravity"] = new Dictionary<string, object> { ["x"] = 0.0f, ["y"] = up, ["z"] = 0.0f },
					});
				}

				ctx.Gap(string.Format("VectorField {0} (intensity {1}, tightness {2}, scale {3})",
						fieldName, intensity, tightness, fieldScale),
					"approximated",
					"THE MAIN LOSS. A baked 3D texture of velocities cannot be imported; Effekseer has only parametric force "
					+ "fields. The curl is imitated with Turbulence and the rise with Gravity, so the motion reads similarly "
					+ "but does not match the source flow.");
			}

			// A field can also SPIN, which is a different thing from being turned once.
			// Effekseer orients a force field and then leaves it. The rate is stated in
			// turns per second, and turning it into degrees per frame is what tells the
			// reader which of the two losses this is: a field that rotates further than
			// half a turn between frames is a different field every frame, so the source
			// motion was never a swirl to begin with.
			var fieldSpin = modules.FirstOrDefault(m => TypeOf(m) == "ParticleModuleVectorFieldRotationRate");
			if (fieldSpin.ValueKind != JsonValueKind.Undefined)
			{
				var turnsPerSecond = Prop(fieldSpin, "RotationRate");
				float perFrame = 360.0f / FramesPerSecond;
				float spinX = FloatProp(turnsPerSecond, "X", 0) * perFrame;
				float spinY = FloatProp(turnsPerSecond, "Y", 0) * perFrame;
				float spinZ = FloatProp(turnsPerSecond, "Z", 0) * perFrame;
				float fastest = Math.Max(Math.Abs(spinX), Math.Max(Math.Abs(spinY), Math.Abs(spinZ)));

				if (fastest > 0.001f)
					ctx.Gap(string.Format("VectorFieldRotationRate {0}/{1}/{2} degrees per frame",
							spinX.ToString("0.#", CultureInfo.InvariantCulture),
							spinY.ToString("0.#", CultureInfo.InvariantCulture),
							spinZ.ToString("0.#", CultureInfo.InvariantCulture)),
						"no-equivalent",
						fastest >= 180.0f
							? "The field turns further than half a circle between frames, so the source is sampling a different field every frame and its motion is high-frequency jitter rather than a flow. Effekseer orients a force field once and leaves it, so neither converts."
							: "The field turns while the particles drift through it. Effekseer orients a force field once and leaves it, so the field converts with a fixed orientation and the turning does not.");
			}

			if (modules.Any(m => TypeOf(m) == "ParticleModuleVectorFieldRotation"))
				ctx.Gap("ParticleModuleVectorFieldRotation", "approximated",
					"A random starting orientation for the field, rolled per emitter instance. The field converts with one fixed orientation instead, so every instance of the effect flows the same way.");
			// Cascade slows a particle by its own velocity times a coefficient per
			// second, and Effekseer's Drag field is that same shape per frame, so the
			// coefficient converts directly once the time unit is right. Several drag
			// modules on one emitter add up the way they do in the source.
			float dragCoefficient = 0;
			foreach (var dragModule in modules.Where(m => TypeOf(m).StartsWith("ParticleModuleAccelerationDrag")))
			{
				float dragLo, dragHi;
				if (ReadScalar(ctx, Prop(dragModule, "DragCoefficientRaw"), out dragLo, out dragHi))
					dragCoefficient += (dragLo + dragHi) * 0.5f;
			}
			if (dragCoefficient > 0.0001f)
			{
				float dragPower = dragCoefficient / FramesPerSecond;
				forceFields.Add(new Dictionary<string, object>
				{
					["type"] = "Drag",
					["power"] = dragPower,
				});
				ctx.Kept(string.Format("Drag coefficient {0}/s  ->  a Drag field of {1} per frame",
					dragCoefficient.ToString("0.##", CultureInfo.InvariantCulture),
					dragPower.ToString("0.####", CultureInfo.InvariantCulture)));
			}

			if (forceFields.Count > 0) node["forceFields"] = forceFields;

			// ---- what has no room at all ------------------------------------------------
			foreach (var m in modules)
			{
				if (TypeOf(m) != "ParticleModuleCollisionGPU") continue;

				// Resilience is a reference to a DistributionFloatConstant export,
				// so it has to be resolved before its Constant can be read.
				var distribution = Resolve(ctx, Prop(Prop(m, "Resilience"), "Distribution"));
				float value = FloatProp(distribution, "Constant", 0);

				ctx.Gap("ParticleModuleCollisionGPU (resilience "
						+ value.ToString("0.##", CultureInfo.InvariantCulture) + ")", "no-equivalent",
					"Effekseer 1.70e has no scene collision, only a kill plane. Particles will pass through geometry.");
			}

			if (typeData.ValueKind != JsonValueKind.Undefined)
			{
				if (TypeOf(typeData) == "ParticleModuleTypeDataGpu")
					ctx.Gap("GPU sprite emitter", "gpu-only",
						"The shipped Effekseer runtime simulates on the CPU. The particle count here is small enough that "
						+ "this costs nothing, but a GPU emitter spawning thousands would not survive the change.");

				if (TypeOf(typeData) == "ParticleModuleTypeDataMesh")
				{
					var mesh = Prop(typeData, "Mesh");
					string meshName = mesh.ValueKind == JsonValueKind.Object
						? StripClass(StringProp(mesh, "ObjectName") ?? "(unnamed)") : "(unnamed)";

					ctx.Gap("Mesh emitter drawing " + meshName, "no-equivalent",
						"Effekseer can render a model, but a dump carries parameters and not geometry, so there is nothing to "
						+ "point it at. Converted as a sprite, which loses the silhouette this emitter exists to draw.");
				}

				var info = Prop(typeData, "EmitterInfo");
				if (info.ValueKind == JsonValueKind.Object && BoolProp(info, "bUseVelocityForMotionBlur"))
					ctx.Gap("bUseVelocityForMotionBlur", "no-equivalent", "The Effekseer runtime has no motion blur.");
			}

			// ---- anything this reader has never heard of --------------------------
			// Every bug in this front end has been a module read as a plausible zero or
			// not read at all, and the second kind is only findable by reading a dump
			// module by module. Saying so here turns the whole class from silent into
			// loud: a name nobody has taught it becomes a line in the report rather
			// than nothing. A forked engine adds modules faster than this can chase.
			foreach (var unknown in modules)
			{
				string kind = TypeOf(unknown);
				if (kind.Length == 0 || HandledModules.Any(known => kind.StartsWith(known))) continue;

				ctx.Gap(kind, "no-equivalent",
					"Not read. The converter has no rule for this module, so whatever it does to the particle is absent rather than approximated.");
			}

			return node;
		}

		// ------------------------------------------------------------- plumbing

		/// <summary>
		/// A Cascade uniform pair is not guaranteed to be written low then high: a
		/// radius of 30 falling to 10, or a rotation of one turn falling to none, are
		/// both real and both legal. Ordering here means no caller has to remember.
		/// </summary>
		/// <summary>
		/// A custom spawn shape can also throw the particle outward at a stated speed,
		/// which is radial and so has the same nowhere to go as the stock primitives'
		/// own outward velocity. Said once, in one place, for every such shape.
		/// </summary>
		static void DeclareOutwardSpeed(Ctx ctx, JsonElement shape, string what)
		{
			float speedLo, speedHi;
			if (!ReadScalar(ctx, Prop(shape, "Speed"), out speedLo, out speedHi)) return;
			if (Math.Max(Math.Abs(speedLo), Math.Abs(speedHi)) < 0.001f) return;

			ctx.Gap(string.Format("{0} throwing the particle outward at {1} to {2} uu/s", what, speedLo, speedHi),
				"no-equivalent",
				"The shape gives each particle a speed along its own radius. Effekseer states velocity on fixed axes, so the shape converts and the throw does not.");
		}

		static Dictionary<string, object> Range(float min, float max)
		{
			if (min > max) { float swap = min; min = max; max = swap; }

			return new Dictionary<string, object>
			{
				["center"] = (min + max) * 0.5f,
				["min"] = min,
				["max"] = max,
			};
		}

		/// <summary>A spawn shape, in the {type, &lt;name&gt;: {...}} shape the schema uses.</summary>
		static Dictionary<string, object> Wrap(string type, string key, Dictionary<string, object> body)
		{
			body.Remove("type");
			return new Dictionary<string, object> { ["type"] = type, [key] = body };
		}

		/// <summary>
		/// An offset module rather than a spawn volume. The name has to be matched
		/// exactly: a prefix match also catches every primitive, which is what made
		/// spawn volumes collapse to a point.
		/// </summary>
		static bool IsPlainLocation(string type)
		{
			return type == "ParticleModuleLocation" || type == "ParticleModuleLocation_Seeded";
		}

		/// <summary>
		/// The first and last entry of a Cascade curve table.
		///
		/// Table.Op says what the table means: 1 is a curve sampled across the
		/// particle's life, 2 is a uniform random range between two sub-entries.
		/// Reading a 128-entry ramp as if it were a min/max pair, which is what
		/// <see cref="ReadVector"/> does, silently turns a colour ramp into two
		/// arbitrary numbers taken from the wrong places.
		/// </summary>
		static bool ReadCurveEnds(JsonElement raw, int stride, out float[] first, out float[] last)
		{
			first = new float[stride];
			last = new float[stride];
			if (raw.ValueKind != JsonValueKind.Object) return false;

			JsonElement table;
			if (!raw.TryGetProperty("Table", out table) || table.ValueKind != JsonValueKind.Object) return false;

			var values = TableValues(raw);
			if (values == null || values.Length < stride) return false;

			int op = (int)FloatProp(table, "Op", 1);

			// A random range is not a ramp. Both ends take its midpoint, so the
			// particle gets one colour instead of easing between two randoms.
			if (op == 2)
			{
				int sub = (int)FloatProp(table, "SubEntryStride", stride);
				if (sub <= 0) sub = stride;
				for (int i = 0; i < stride; i++)
				{
					float a = values[i];
					float b = i + sub < values.Length ? values[i + sub] : a;
					first[i] = last[i] = (a + b) * 0.5f;
				}
				return true;
			}

			int entryStride = (int)FloatProp(table, "EntryStride", stride);
			if (entryStride <= 0) entryStride = stride;
			int count = (int)FloatProp(table, "EntryCount", 1);

			int lastStart = Math.Max(0, (count - 1) * entryStride);
			for (int i = 0; i < stride; i++)
			{
				first[i] = values[i];
				last[i] = lastStart + i < values.Length ? values[lastStart + i] : values[i];
			}
			return true;
		}

		/// <summary>The largest sample in a table, used as the head of an alpha ramp.</summary>
		static float TablePeak(JsonElement raw)
		{
			var values = TableValues(raw);
			if (values == null || values.Length == 0) return 1.0f;

			float peak = values[0];
			foreach (var v in values) if (v > peak) peak = v;
			return peak;
		}

		/// <summary>
		/// References look like "/Game/Path/Asset.7", where 7 is the export index
		/// and matches this array's ordering. The resolved export's name is
		/// checked against the reference before it is trusted.
		/// </summary>
		static JsonElement Resolve(Ctx ctx, JsonElement reference)
		{
			if (reference.ValueKind != JsonValueKind.Object) return default(JsonElement);

			string path = StringProp(reference, "ObjectPath");
			string objectName = StringProp(reference, "ObjectName");

			if (!string.IsNullOrEmpty(path))
			{
				int dot = path.LastIndexOf('.');
				int index;
				if (dot >= 0 && int.TryParse(path.Substring(dot + 1), out index)
					&& index >= 0 && index < ctx.Exports.Length)
				{
					var candidate = ctx.Exports[index];
					string name = NameOf(candidate);
					if (string.IsNullOrEmpty(objectName) || (name.Length > 0 && objectName.Contains(name)))
						return candidate;
				}
			}

			if (!string.IsNullOrEmpty(objectName))
			{
				string leaf = StripClass(objectName);
				int colon = leaf.LastIndexOf(':');
				if (colon >= 0) leaf = leaf.Substring(colon + 1);
				int lastDot = leaf.LastIndexOf('.');
				if (lastDot >= 0) leaf = leaf.Substring(lastDot + 1);

				foreach (var e in ctx.Exports)
					if (NameOf(e) == leaf) return e;
			}

			return default(JsonElement);
		}

		/// <summary>Turn Class'Outer:Name' into Outer:Name.</summary>
		static string StripClass(string objectName)
		{
			if (string.IsNullOrEmpty(objectName)) return "";
			int quote = objectName.IndexOf('\'');
			return quote >= 0 ? objectName.Substring(quote + 1).TrimEnd('\'') : objectName;
		}

		static string Sanitise(string s)
		{
			var sb = new StringBuilder();
			foreach (char c in s) sb.Append(char.IsLetterOrDigit(c) || c == '_' || c == '-' ? c : '_');
			return sb.Length == 0 ? "texture" : sb.ToString();
		}

		// The export's own identity, which is always at the top level. These two
		// deliberately do NOT look inside "Properties": a module's own fields can
		// be called Type, and shadowing the structural one would be fatal.
		static string TypeOf(JsonElement e) { return RawString(e, "Type") ?? ""; }
		static string NameOf(JsonElement e) { return RawString(e, "Name") ?? ""; }

		static string RawString(JsonElement e, string name)
		{
			if (e.ValueKind != JsonValueKind.Object) return null;
			JsonElement v;
			if (e.TryGetProperty(name, out v) && v.ValueKind == JsonValueKind.String) return v.GetString();
			return null;
		}

		static JsonElement Prop(JsonElement e, string name)
		{
			if (e.ValueKind != JsonValueKind.Object) return default(JsonElement);

			JsonElement properties, value;
			if (e.TryGetProperty("Properties", out properties) && properties.ValueKind == JsonValueKind.Object
				&& properties.TryGetProperty(name, out value))
				return value;

			if (e.TryGetProperty(name, out value)) return value;
			return default(JsonElement);
		}

		/// <summary>
		/// Reads a string, looking inside the export's "Properties" envelope as
		/// well as at the top level, exactly as <see cref="FloatProp"/> does.
		/// Reading only the top level silently returns null for every module
		/// field, which is how ScreenAlignment and InterpolationMethod went
		/// unnoticed: a missing string looks the same as a field that is not set.
		/// </summary>
		static string StringProp(JsonElement e, string name)
		{
			var v = Prop(e, name);
			return v.ValueKind == JsonValueKind.String ? v.GetString() : null;
		}

		/// <summary>
		/// Reads a number, looking inside the export's "Properties" envelope as
		/// well as at the top level. Exports wrap their real fields; nested value
		/// objects such as RelativeScale3D do not. Going through <see cref="Prop"/>
		/// handles both, and forgetting to do so silently yields the fallback.
		/// </summary>
		static float FloatProp(JsonElement e, string name, float fallback)
		{
			if (e.ValueKind == JsonValueKind.Number)
			{
				double direct;
				return e.TryGetDouble(out direct) ? (float)direct : fallback;
			}

			var v = Prop(e, name);
			double d;
			if (v.ValueKind == JsonValueKind.Number && v.TryGetDouble(out d)) return (float)d;
			return fallback;
		}

		static bool BoolProp(JsonElement e, string name)
		{
			return Prop(e, name).ValueKind == JsonValueKind.True;
		}

		/// <summary>
		/// A raw Cascade value either carries its numbers inline or points at a
		/// separate Distribution export. Reading only the inline form yields a silent
		/// zero for everything authored the other way round, and the two forms appear
		/// side by side inside one effect, so which one a module uses says nothing
		/// about the value. Follow the reference before giving up.
		/// </summary>
		static bool ReadScalar(Ctx ctx, JsonElement raw, out float min, out float max)
		{
			if (ReadScalarInline(raw, out min, out max)) return true;

			var distribution = Resolve(ctx, Prop(raw, "Distribution"));
			if (distribution.ValueKind != JsonValueKind.Object) return false;

			string kind = TypeOf(distribution);
			// A curve distribution is a keyframe list, not two numbers. Saying no here
			// leaves the caller's own fallback in place rather than inventing a value.
			if (kind.Contains("Curve")) return false;
			if (kind.Contains("Constant")) { min = max = FloatProp(distribution, "Constant", 0); return true; }
			if (kind.Contains("Uniform"))
			{
				min = FloatProp(distribution, "Min", 0);
				max = FloatProp(distribution, "Max", 0);
				return true;
			}
			return false;
		}

		/// <summary>The vector form of <see cref="ReadScalar(Ctx, JsonElement, out float, out float)"/>.</summary>
		static bool ReadVector(Ctx ctx, JsonElement raw, out float[] min, out float[] max)
		{
			if (ReadVectorInline(raw, out min, out max)) return true;

			var distribution = Resolve(ctx, Prop(raw, "Distribution"));
			if (distribution.ValueKind != JsonValueKind.Object) return false;

			string kind = TypeOf(distribution);
			if (kind.Contains("Curve")) return false;
			if (kind.Contains("Constant"))
			{
				var c = Prop(distribution, "Constant");
				for (int i = 0; i < 3; i++) min[i] = max[i] = FloatProp(c, "XYZ"[i].ToString(), 0);
				return true;
			}
			if (kind.Contains("Uniform"))
			{
				var lo = Prop(distribution, "Min");
				var hi = Prop(distribution, "Max");
				for (int i = 0; i < 3; i++)
				{
					min[i] = FloatProp(lo, "XYZ"[i].ToString(), 0);
					max[i] = FloatProp(hi, "XYZ"[i].ToString(), 0);
				}
				return true;
			}
			return false;
		}

		/// <summary>
		/// A raw distribution carries MinValue/MaxValue, or a Table whose Op says
		/// whether it holds a constant or a uniform range.
		/// </summary>
		static bool ReadScalarInline(JsonElement raw, out float min, out float max)
		{
			min = max = 0;
			if (raw.ValueKind != JsonValueKind.Object) return false;

			JsonElement lo, hi;
			if (raw.TryGetProperty("MinValue", out lo) && raw.TryGetProperty("MaxValue", out hi)
				&& lo.ValueKind == JsonValueKind.Number && hi.ValueKind == JsonValueKind.Number)
			{
				min = (float)lo.GetDouble();
				max = (float)hi.GetDouble();
				return true;
			}

			var values = TableValues(raw);
			if (values == null || values.Length == 0) return false;
			min = values[0];
			max = values[values.Length - 1];
			return true;
		}

		static bool ReadVectorInline(JsonElement raw, out float[] min, out float[] max)
		{
			min = new float[3];
			max = new float[3];
			if (raw.ValueKind != JsonValueKind.Object) return false;

			JsonElement lo, hi;
			if (raw.TryGetProperty("MinValueVec", out lo) && raw.TryGetProperty("MaxValueVec", out hi)
				&& lo.ValueKind == JsonValueKind.Object && hi.ValueKind == JsonValueKind.Object)
			{
				min[0] = FloatProp(lo, "X", 0); min[1] = FloatProp(lo, "Y", 0); min[2] = FloatProp(lo, "Z", 0);
				max[0] = FloatProp(hi, "X", 0); max[1] = FloatProp(hi, "Y", 0); max[2] = FloatProp(hi, "Z", 0);
				return true;
			}

			var values = TableValues(raw);
			if (values == null) return false;

			if (values.Length >= 6)
			{
				for (int i = 0; i < 3; i++) { min[i] = values[i]; max[i] = values[i + 3]; }
				return true;
			}
			if (values.Length >= 3)
			{
				for (int i = 0; i < 3; i++) { min[i] = values[i]; max[i] = values[i]; }
				return true;
			}
			return false;
		}

		static float[] TableValues(JsonElement raw)
		{
			JsonElement table, values;
			if (!raw.TryGetProperty("Table", out table) || table.ValueKind != JsonValueKind.Object) return null;
			if (!table.TryGetProperty("Values", out values) || values.ValueKind != JsonValueKind.Array) return null;

			return values.EnumerateArray()
						 .Where(v => v.ValueKind == JsonValueKind.Number)
						 .Select(v => (float)v.GetDouble())
						 .ToArray();
		}

		static int Clamp255(float v)
		{
			if (v < 0) return 0;
			if (v > 255) return 255;
			return (int)Math.Round(v);
		}
	}
}
