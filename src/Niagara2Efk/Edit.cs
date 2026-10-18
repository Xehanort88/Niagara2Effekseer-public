using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using Effekseer;
using Effekseer.Data;

namespace Niagara2Efk
{
	/// <summary>
	/// Edit a copy of an existing effect: load the whole source, set named
	/// values on named nodes, copy its resources beside the output, save.
	///
	/// This deliberately loads the source rather than grafting it, so project
	/// settings, procedural models and dynamic equations arrive untouched and
	/// none of the four graft repairs apply. The source is only ever read.
	///
	/// A value is addressed the way the EDIT chunk spells it, which is the
	/// EffekseerCore property path: "CommonValues/Life", "DrawingValues/Track/
	/// TrackSizeMiddle_Fixed". Any link on that path marked [IO(Export = false)]
	/// is refused, because writing one succeeds in memory and is dropped on
	/// save -- the trap behind the mapper's all-white effects.
	///
	/// Every value set is read back out of the saved file and compared.
	/// </summary>
	static partial class Assembler
	{
		/// <summary>
		/// Every part of a spec collects the keys it doesn't know here, so a typo
		/// ("ops" for "edits", "sets" for "set") is an error instead of an edit
		/// that silently does nothing and still reports VERIFY OK. A key that
		/// starts with "//" is a comment and is allowed anywhere.
		/// </summary>
		abstract class SpecPart
		{
			[JsonExtensionData] public Dictionary<string, JsonElement> Unknown { get; set; }
		}

		class EditSpec : SpecPart
		{
			public RetimeSpec retime { get; set; }
			public string source { get; set; }
			public string name { get; set; }
			public bool allowMissingResources { get; set; }
			/// <summary>
			/// Optional: the Effekseer editor's preview range end (Core.EndFrame,
			/// saved in the project). Not used by the runtime, but the editor's
			/// preview stops or loops there, so a lengthened effect needs it raised.
			/// </summary>
			public int? endFrame { get; set; }
			public List<EditOp> edits { get; set; }
			/// <summary>
			/// Optional: change or create the project's procedural models (the
			/// editor's "Procedural Model" list). Applied before `edits`, so a
			/// node can reference a model created here. Models are shared:
			/// every node whose Model references an index changes with it.
			/// </summary>
			public List<ProceduralOp> procedural { get; set; }
		}

		class RetimeSpec : SpecPart
		{
			public double? factor { get; set; }
		}

		class ProceduralOp : SpecPart
		{
			/// <summary>The model to change. Give this or `add`, not both.</summary>
			public int? index { get; set; }
			/// <summary>
			/// Create a model with this name, appended to the list so no existing
			/// index shifts. Names must be unique: a node references the model
			/// by name ("DrawingValues/Model/Reference": "BeamTube") or index.
			/// </summary>
			public string add { get; set; }
			/// <summary>With `add`: start from a copy of the model at this index instead of the defaults (a Mesh sphere).</summary>
			public int? from { get; set; }
			/// <summary>Optional: the current Name of the model at `index` (or at `from` when adding), as a guard.</summary>
			public string expect { get; set; }
			public string note { get; set; }
			/// <summary>Paths from the model, e.g. "Shape/Point1": [0.7, 0.2], "VertexColor/ColorLowerCenter": [255, 255, 255, 255].</summary>
			public Dictionary<string, JsonElement> set { get; set; }
		}

		class EditOp : SpecPart
		{
			/// <summary>Child indices from the root, e.g. "1/0/0". "" or "root" is the root.</summary>
			public string node { get; set; }
			/// <summary>Optional: the node's current name, as a guard against a wrong path.</summary>
			public string expect { get; set; }
			public string note { get; set; }
			public Dictionary<string, JsonElement> set { get; set; }
			/// <summary>Optional: recolour this node and everything beneath it.</summary>
			public TintSpec tint { get; set; }
			/// <summary>
			/// Optional: append a copy of another node (with its subtree) as the
			/// LAST child of `node`, so no existing index shifts. `expect` then
			/// guards the parent, and `set`/`tint` apply to the new copy, which
			/// later ops address as "&lt;node&gt;/&lt;child count before&gt;".
			/// Uses the editor's own clipboard (Core.Copy/Paste), the same way
			/// the editor duplicates a node; within one project procedural-model
			/// and equation indices stay valid.
			/// </summary>
			public AddSpec add { get; set; }
			/// <summary>
			/// Optional: move FCurve keys in time. Each entry names one curve
			/// (e.g. "ScalingValues/SingleFCurve/S") and shifts every key at or
			/// after `from` by `by` frames, handles included, so the shape is kept.
			/// </summary>
			public List<ShiftSpec> shift { get; set; }
			/// <summary>
			/// Optional: replace every key of a float FCurve (e.g.
			/// "LocationValues/LocationFCurve/FCurve/Y") with `points`, each
			/// [frame, value], joined by straight lines. A smooth path is written
			/// as many close keys, so the saved curve is exactly what was asked.
			/// </summary>
			public List<KeysSpec> keys { get; set; }
		}

		class KeysSpec : SpecPart
		{
			public string curve { get; set; }
			public List<float[]> points { get; set; }
		}

		class ShiftSpec : SpecPart
		{
			public string curve { get; set; }
			public int from { get; set; }
			public int by { get; set; }
			/// <summary>
			/// Optional: on a Percent curve whose node does not live exactly 100
			/// frames, `by` is refused unless this says it is meant as percent of
			/// life rather than frames.
			/// </summary>
			public bool percentOfLife { get; set; }
		}

		class AddSpec : SpecPart
		{
			/// <summary>Path of the node to copy.</summary>
			public string from { get; set; }
			public string name { get; set; }
			/// <summary>
			/// Optional: another .efkefc to copy `from` out of (absolute, or
			/// relative to the spec). The clipboard writes resource paths
			/// absolute (see Assembler), so the pasted subtree still points at
			/// that effect's textures and RewriteResources copies them beside
			/// the output. Procedural models the subtree references are copied
			/// into this project's list and the references repointed (the
			/// assembler's repair). Refused if it uses dynamic equations.
			/// </summary>
			public string file { get; set; }
		}

		class TintSpec : SpecPart
		{
			/// <summary>Target hue in degrees (gold is about 42).</summary>
			public float hue { get; set; }
			/// <summary>Optional multiplier on saturation: 1 keeps it, 0 turns every colour grey-white.</summary>
			public float? saturation { get; set; }
			/// <summary>
			/// Optional: give colours on subtractive (AlphaBlend Sub) nodes the
			/// OPPOSITE hue. Subtracting a colour leaves its complement, so a dark
			/// layer tinted blue darkens towards orange; tinted orange it leaves blue.
			/// </summary>
			public bool subtractiveOpposite { get; set; }
		}

		static string ParentPath(string path)
		{
			int i = path.LastIndexOf('/');
			return i < 0 ? "" : path.Substring(0, i);
		}

		/// <summary>"Percent" or "Time" for the FCurve group holding `curvePath`, or null.</summary>
		static string CurveTimeline(object target, string curvePath)
		{
			if (!TryResolve(target, ParentPath(curvePath), out var leaf, out _) || leaf.Value == null) return null;
			var field = leaf.Value.GetType().GetField("Timeline", BindingFlags.Public | BindingFlags.Instance);
			var v = field?.GetValue(leaf.Value);
			return v?.GetType().GetProperty("Value")?.GetValue(v, null)?.ToString();
		}

		static string DescribeAt(object target, string path)
		{
			return TryResolve(target, path, out var leaf, out _) && TryDescribe(leaf, out var s) ? s : "?";
		}

		/// <summary>
		/// A curve reaches the runtime as samples every `Sampling` units from its
		/// first key, joined by straight lines (EffekseerCore FCurve.GetBytes,
		/// runtime FCurve::GetValue). At the default 10 a key between samples is
		/// smoothed away: a pop at frame 1 becomes a 10-frame ramp, a hold that
		/// ends at 42 starts shrinking at 40. So a curve written by `keys` or
		/// `shift` is sampled every unit, unless the same op sets its Sampling.
		/// It is read back like any other value.
		/// </summary>
		static bool SampleExactly(NodeBase target, string opPath, EditOp op, string curvePath, List<Applied> applied)
		{
			string key = curvePath + "/Sampling";
			if (op.set != null && op.set.Keys.Any(k => k == key)) return true;
			if (!TryResolve(target, key, out var leaf, out var err) || !TryDescribe(leaf, out var before) ||
				!TrySet(leaf, JsonDocument.Parse("1").RootElement, out err))
			{
				Console.Error.WriteLine(string.Format("node '{0}' ({1}) {2}: {3}", opPath, target.Name.Value, key, err ?? "cannot set"));
				return false;
			}
			if (!applied.Any(a => a.Node == opPath && a.Key == key))
				applied.Add(new Applied { Node = opPath, Key = key, Before = before, Wanted = "1" });
			return true;
		}

		static List<string> UnknownSpecKeys(EditSpec spec)
		{
			var found = new List<string>();
			void Check(SpecPart part, string where)
			{
				if (part?.Unknown == null) return;
				var known = part.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance)
					.Where(pr => pr.Name != nameof(SpecPart.Unknown)).Select(pr => pr.Name);
				foreach (var key in part.Unknown.Keys.Where(k => !k.StartsWith("//")))
					found.Add(string.Format("unknown key \"{0}\" {1} (known: {2})", key, where, string.Join(", ", known)));
			}
			Check(spec, "at the top level");
			Check(spec.retime, "in retime");
			for (int i = 0; i < (spec.edits?.Count ?? 0); i++)
			{
				var op = spec.edits[i];
				if (op == null) continue;
				string at = string.Format("in edits[{0}] (node \"{1}\")", i, op.node);
				Check(op, at);
				Check(op.tint, at + " tint");
				Check(op.add, at + " add");
				for (int j = 0; j < (op.shift?.Count ?? 0); j++) Check(op.shift[j], at + " shift[" + j + "]");
				for (int j = 0; j < (op.keys?.Count ?? 0); j++) Check(op.keys[j], at + " keys[" + j + "]");
			}
			for (int i = 0; i < (spec.procedural?.Count ?? 0); i++)
				Check(spec.procedural[i], "in procedural[" + i + "]");
			return found;
		}

		/// <summary>Where relative resource paths in the spec resolve from.</summary>
		static string editSpecDir;

		class Applied
		{
			public string Node, Key, Before, Wanted, After;
		}

		class RetimedCurve
		{
			public string Node, Path;
			public string Timeline;
			public int Sampling;
			public double[][] Keys;
		}

		static double[][] CurveState(Effekseer.Data.Value.IFCurve curve)
		{
			return curve.Keys.Select(k => new double[] { k.Frame, k.ValueAsFloat, k.LeftX, k.LeftY,
				k.RightX, k.RightY, (int)k.InterpolationType }).ToArray();
		}

		static int RetimeInteger(double exact, string where)
		{
			if (!double.IsFinite(exact) || Math.Round(exact) < int.MinValue || Math.Round(exact) > int.MaxValue)
				throw new InvalidOperationException(where + ": retimed integer exceeds the supported range");
			int rounded = (int)Math.Round(exact);
			if (exact != rounded) Console.WriteLine("RETIME ROUND: {0}: {1:R} -> {2} (error {3:R})", where, exact, rounded, rounded - exact);
			return rounded;
		}

		// Only explicit, source-inventoried paths have a multiplier. Do not infer
		// units from a name like Power (falloff exponents are not accelerations).
		static double? RetimeMultiplier(string path, double f)
		{
			string canonical = path.Replace("CommonValues/Generation/", "CommonValues/");
			if (canonical == "CommonValues/Life" || canonical == "CommonValues/GenerationTime" ||
				canonical == "CommonValues/GenerationTimeOffset" || path == "RendererCommonValues/FadeIn/Frame" ||
				path == "RendererCommonValues/FadeOut/Frame" || path.EndsWith("/UVAnimation/FrameLength") ||
				path == "RendererCommonValues/UVAnimation/AnimationParams/FrameLength" ||
				path == "AdvancedRendererCommonValuesValues/AlphaCutoffParam/FourPointInterpolation/TransitionFrameNum" ||
				path == "AdvancedRendererCommonValuesValues/AlphaCutoffParam/FourPointInterpolation/TransitionFrameNum2" ||
				path == "CollisionsValues/LifetimeReductionPerCollision") return f;
			if (path.EndsWith("/UVScroll/Speed")) return 1 / f;
			string[] pvas = { "LocationValues/PVA", "RotationValues/PVA", "RotationValues/AxisPVA",
				"ScalingValues/PVA", "ScalingValues/SinglePVA", "DrawingValues/Ring/Inner_PVA", "DrawingValues/Ring/Outer_PVA" };
			if (pvas.Any(p => path == p + "/Velocity")) return 1 / f;
			if (pvas.Any(p => path == p + "/Acceleration")) return 1 / f / f;
			return null;
		}

		static bool ApplyRetime(double factor, List<Applied> applied, List<RetimedCurve> curves)
		{
			var nodes = new List<Tuple<string, NodeBase>>();
			Walk(Core.Root, "", nodes);
			foreach (var entry in nodes)
			{
				string nodePath = entry.Item1;
				NodeBase node = entry.Item2;
				void Warn(string path, string reason) => Console.WriteLine("RETIME WARNING: node '{0}' ({1}) {2}: {3}; unchanged", nodePath, node.Name.Value, path, reason);
				void Scale(object value, string path, double mul)
				{
					try { ScaleValue(value, path, mul); }
					catch (InvalidOperationException e) { Warn(path, e.Message); }
				}
				void ScaleValue(object value, string path, double mul)
				{
					if (value == null) return;
					if (value.GetType().GetProperty("IsDynamicEquationEnabled")?.GetValue(value) is Effekseer.Data.Value.Boolean dyn && dyn.Value)
					{ Warn(path, "dynamic equation cannot be scaled exactly"); return; }
					if (value is Effekseer.Data.Value.IntWithInifinite inf)
					{ if (!inf.Infinite.Value) Scale(inf.Value, path + "/Value", mul); return; }
					if (value is Effekseer.Data.Value.Vector3D || value is Effekseer.Data.Value.Vector3DWithRandom ||
						value is Effekseer.Data.Value.Vector2D || value is Effekseer.Data.Value.Vector2DWithRandom)
					{
						foreach (string axis in new[] { "X", "Y", "Z" })
							if (value.GetType().GetProperty(axis) is PropertyInfo prop) Scale(prop.GetValue(value), path + "/" + axis, mul);
						return;
					}
					var leaf = new Leaf { Value = value };
					var oldNumbers = Numbers(leaf);
					double? exactCenter = value is Effekseer.Data.Value.IntWithRandom centered ? centered.GetCenter() * mul : null;
					if (!TryDescribe(leaf, out string before)) throw new InvalidOperationException(path + ": unsupported retime value");
					double[] wanted;
					if (value is Effekseer.Data.Value.FloatWithRandom fr) wanted = new[] { fr.GetMin() * mul, fr.GetMax() * mul };
					else if (value is Effekseer.Data.Value.IntWithRandom ir) wanted = new double[] {
						RetimeInteger(ir.GetMin() * mul, nodePath + " " + path + "/Min"), RetimeInteger(ir.GetMax() * mul, nodePath + " " + path + "/Max") };
					else if (value is Effekseer.Data.Value.Float fl) wanted = new[] { fl.Value * mul };
					else if (value is Effekseer.Data.Value.Int it) wanted = new double[] { RetimeInteger(it.Value * mul, nodePath + " " + path) };
					else throw new InvalidOperationException(path + ": unsupported retime numeric type");
					if (wanted.Any(x => !double.IsFinite(x) || Math.Abs(x) > float.MaxValue)) throw new InvalidOperationException(path + ": retimed number exceeds supported range");
					string json = wanted.Length == 1 ? JsonSerializer.Serialize(wanted[0]) : JsonSerializer.Serialize(wanted);
					// Integer ranges bypass TrySet's float-array conversion, which can
					// lose precision for frame counts above 2^24.
					if (value is Effekseer.Data.Value.IntWithRandom integerRange) SetRange(integerRange, (int)wanted[0], (int)wanted[1]);
					else if (!TrySet(leaf, JsonDocument.Parse(json).RootElement, out string err)) throw new InvalidOperationException(path + ": " + err);
					if (value is Effekseer.Data.Value.IntWithRandom centeredAfter && centeredAfter.GetCenter() != exactCenter.Value)
						Console.WriteLine("RETIME ROUND: {0} {1}/Center: {2:R} -> {3} (error {4:R}; center recomputed from rounded range)",
							nodePath, path, exactCenter.Value, centeredAfter.GetCenter(), centeredAfter.GetCenter() - exactCenter.Value);
					// Core setters enforce limits. Declare any approximation and verify
					// the representable result rather than falsely promising the request.
					var actual = Numbers(leaf);
					if (actual.Where((x, i) => Math.Abs(x - wanted[i]) > 1e-6 * Math.Max(1, Math.Abs(wanted[i]))).Any())
					{
						Console.WriteLine("RETIME WARNING: node '{0}' ({1}) {2}: requested {3}, clamped to {4}", nodePath, node.Name.Value, path, json, JsonSerializer.Serialize(actual));
						json = actual.Length == 1 ? JsonSerializer.Serialize(actual[0]) : JsonSerializer.Serialize(actual);
					}
					if (!oldNumbers.SequenceEqual(actual))
						applied.Add(new Applied { Node = nodePath, Key = path, Before = before, Wanted = json });
				}
				var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
				void Visit(object o, string path)
				{
					if (o == null || !seen.Add(o)) return;
					if (o.GetType().GetProperty("IsDynamicEquationEnabled")?.GetValue(o) is Effekseer.Data.Value.Boolean dyn && dyn.Value)
					{ Warn(path, "dynamic equation cannot be scaled exactly"); return; }
					if (RetimeMultiplier(path, factor) is double multiplier) { Scale(o, path, multiplier); return; }
					if (o is LocalForceField ff)
					{
						if (ff.Type.Value == LocalForceFieldType.Gravity) Scale(ff.Gravity.Gravity, path + "/Gravity/Gravity", 1 / factor / factor);
						else if (ff.Type.Value == LocalForceFieldType.Force || ff.Type.Value == LocalForceFieldType.Wind) Scale(ff.Power, path + "/Power", 1 / factor / factor);
						else if (ff.Type.Value != LocalForceFieldType.None) Warn(path + "/Power", ff.Type.Value + " is not a plain acceleration (velocity-dependent force controls)");
						return;
					}
					if (o is Effekseer.Data.Value.IFCurve curve)
					{
						var keys = curve.Keys.ToList();
						if (keys.Count == 0) return;
						string timeline = CurveTimeline(node, path);
						if (timeline == "Percent")
						{
							var outside = keys.Where(k => k.Frame < 0 || k.Frame > 100).Select(k => k.Frame).ToList();
							if (outside.Count > 0) Warn(path, "Timeline Percent keys outside 0..100 never play: " + string.Join(",", outside));
							curves.Add(new RetimedCurve { Node = nodePath, Path = path });
							return;
						}
						if (timeline != "Time") { Warn(path, "unknown FCurve timeline"); return; }
						var state = CurveState(curve);
						// Check the whole curve before moving any key. An unrepresentable
						// stretch warns and preserves this curve while other fields continue.
						if (state.Any(k => !double.IsFinite(k[0] * factor) || Math.Round(k[0] * factor) < int.MinValue ||
							Math.Round(k[0] * factor) > int.MaxValue || !float.IsFinite((float)(k[2] * factor)) || !float.IsFinite((float)(k[4] * factor))))
						{ Warn(path, "retimed key frame or handle exceeds the supported numeric range"); return; }
						int[] frames = state.Select(k => RetimeInteger(k[0] * factor, nodePath + " " + path + " key " + k[0])).ToArray();
						if (frames.Distinct().Count() < frames.Length)
							Console.WriteLine("RETIME WARNING: node '{0}' ({1}) {2}: rounded key frames collide; all keys retained; stretch is not exact", nodePath, node.Name.Value, path);
						// SetFrame shifts handles; the ordinary handle setters couple X
						// and Y. Direct setters preserve the authored values exactly.
						for (int i = 0; i < keys.Count; i++)
						{
							var k = keys[i]; var old = state[i];
							k.SetFrame(frames[i]); k.Commit();
							float lx = (float)(old[2] * factor), rx = (float)(old[4] * factor);
							if (!float.IsFinite(lx) || !float.IsFinite(rx)) throw new InvalidOperationException(path + ": retimed handle exceeds supported range");
							k.GetType().GetMethod("SetLeftDirectly").Invoke(k, new object[] { lx, (float)old[3] });
							k.GetType().GetMethod("SetRightDirectly").Invoke(k, new object[] { rx, (float)old[5] });
						}
						// OffsetMin/OffsetMax stay: the runtime adds a random pick from them
						// to the curve's value (FCurve::GetOffset, e.g. Rotation.cpp
						// `fcurve + offset`), not to the age. `frame -= offset_` uses the first
						// key's frame, which moves with the keys.
						if (!SampleExactly(node, nodePath, new EditOp(), path, applied)) throw new InvalidOperationException(path + ": cannot sample retimed curve");
						Console.WriteLine("RETIME CURVE: node '{0}' ({1}) {2}: Timeline Time, {3} keys, frames {4} -> {5}, handle X x{6}, Sampling 1",
							nodePath, node.Name.Value, path, keys.Count, string.Join(",", state.Select(k => k[0])), string.Join(",", frames), factor);
						curves.Add(new RetimedCurve { Node = nodePath, Path = path });
						return;
					}
#if EFK18
					if (o is GpuParticlesValues gpu)
					{
						if (gpu.Enabled.Value)
						{
							Scale(gpu.Basic.LifeTime, path + "/Basic/LifeTime", factor);
							Scale(gpu.Basic.EmitOffset, path + "/Basic/EmitOffset", factor);
							Scale(gpu.Velocity.InitialSpeed, path + "/Velocity/InitialSpeed", 1 / factor);
							Scale(gpu.Rotation.AngularVelocity, path + "/Rotation/AngularVelocity", 1 / factor);
							Scale(gpu.Force.Gravity, path + "/Force/Gravity", 1 / factor / factor);
							foreach (string control in new[] { "/Basic/EmitPerFrame", "/Velocity/Damping", "/Force/Vortex/Rotation", "/Force/Vortex/Attraction", "/Force/Turbulence/Power" })
								Warn(path + control, "GPU discrete emission or velocity-dependent/noise control cannot be scaled exactly");
						}
						return;
					}
#endif
					if (TryDescribe(o, out _) || o.GetType().IsPrimitive || o is string || o.GetType().IsEnum) return;
					foreach (var prop in o.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
					{
						if (prop.Name == "Parent" || prop.Name == "Children" || prop.GetIndexParameters().Length > 0) continue;
						var io = prop.GetCustomAttributes(typeof(IOAttribute), true).FirstOrDefault() as IOAttribute;
						if (io != null && !io.Export) continue;
						object v = prop.GetValue(o);
						if (v?.GetType().Namespace?.StartsWith("Effekseer.Data") == true)
							Visit(v, path.Length == 0 ? prop.Name : path + "/" + prop.Name);
					}
				}
				Visit(node, "");
				if (node is Node n)
				{
					if (n.DrawingValues.Type.Value == RendererValues.ParamaterType.Ribbon || n.DrawingValues.Type.Value == RendererValues.ParamaterType.Track)
						Warn("DrawingValues/" + n.DrawingValues.Type.Value + "/SplineDivision", "Track/Ribbon sampling and discrete spawning cannot be scaled exactly");
					if (n.SoundValues.Type.Value != SoundValues.ParamaterType.None) Warn("SoundValues/Sound/Delay", "sound delays are outside the retime rule");
					if (n.CommonValues.LocationEffectType.Value.ToString().Contains("FollowParent"))
					{
						Warn("CommonValues/SteeringBehaviorParam/MaxFollowSpeed", "parent-follow feedback cannot be scaled exactly");
						Warn("CommonValues/SteeringBehaviorParam/SteeringSpeed", "parent-follow feedback cannot be scaled exactly");
					}
					if (n.LocationValues.Type.Value.ToString().Contains("Nurbs")) Warn("LocationValues/NurbsCurve/MoveSpeed", "NURBS motion is outside the PVA rule");
				}
			}
			return true;
		}

		public static int RunEdit(string specPath, string outPath)
		{
			EditSpec spec;
			try
			{
				spec = JsonSerializer.Deserialize<EditSpec>(File.ReadAllText(specPath),
					new JsonSerializerOptions { PropertyNameCaseInsensitive = true, ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
			}
			catch (Exception e)
			{
				Console.Error.WriteLine("could not read edit spec: " + e.Message);
				return 2;
			}
			if (spec == null || string.IsNullOrEmpty(spec.source))
			{
				Console.Error.WriteLine("edit spec needs \"source\"");
				return 2;
			}
			var unknown = UnknownSpecKeys(spec);
			if (unknown.Count > 0)
			{
				foreach (var u in unknown) Console.Error.WriteLine("edit spec: " + u);
				return 2;
			}
			if (spec.retime != null && (!spec.retime.factor.HasValue || !double.IsFinite(spec.retime.factor.Value) || spec.retime.factor.Value <= 0))
			{
				Console.Error.WriteLine("edit spec: retime.factor must be a finite positive number");
				return 2;
			}

			string specDir = Path.GetDirectoryName(Path.GetFullPath(specPath));
			editSpecDir = specDir;
			string source = Path.IsPathRooted(spec.source) ? spec.source : Path.GetFullPath(Path.Combine(specDir, spec.source));
			outPath = Path.GetFullPath(outPath);
			if (string.Equals(source, outPath, StringComparison.OrdinalIgnoreCase))
			{
				Console.Error.WriteLine("refusing to overwrite the source; edit writes a copy");
				return 2;
			}

			// Subtrees copied from OTHER effects are read first: Core holds one
			// project at a time, and the source must be the loaded one after.
			var foreignXml = new Dictionary<EditOp, Tuple<string, string>>();
			var foreignRefs = new Dictionary<EditOp, List<RefRecord>>();
			foreach (var op in (spec.edits ?? new List<EditOp>()).Where(o => o.add != null && !string.IsNullOrEmpty(o.add.file)))
			{
				string file = Path.IsPathRooted(op.add.file) ? op.add.file : Path.GetFullPath(Path.Combine(specDir, op.add.file));
				Core.New();
				if (!Core.LoadFrom(file))
				{
					Console.Error.WriteLine("add: could not load " + file);
					return 1;
				}
				var from = Navigate(op.add.from);
				if (from == null)
				{
					Console.Error.WriteLine("add: no node at path '" + op.add.from + "' in " + file);
					return 1;
				}
				// Procedural models are carried (definition copied, reference
				// repointed after the paste); dynamic equations also need the
				// project's inputs, which only the assembler reconciles.
				bool usesDynamic = false;
				var refs = HarvestRefs(from, ref usesDynamic);
				if (usesDynamic)
				{
					Console.Error.WriteLine("add: " + op.add.from + " in " + file + " uses dynamic equations; use the assembler");
					return 1;
				}
				foreignRefs[op] = refs;
				foreignXml[op] = Tuple.Create(Core.Copy(from), Path.GetFileName(file) + ":" + op.add.from + " (" + from.Name.Value + ")");
			}

			Core.New();
			if (!Core.LoadFrom(source))
			{
				Console.Error.WriteLine("could not load " + source);
				return 1;
			}
			int nodesBefore = Core.Root.Children.Internal.Sum(c => CountSubtree(c));
			int modelsAtLoad = Core.ProceduralModel.ProceduralModels.Values.Count;
			if (!string.IsNullOrEmpty(spec.name)) Core.Root.Name.SetValue(spec.name);
			int endFrameBefore = Core.EndFrame;
			if (spec.endFrame.HasValue) Core.EndFrame = spec.endFrame.Value;

			var applied = new List<Applied>();
			var retimedCurves = new List<RetimedCurve>();
			int? expectedEndFrame = spec.endFrame;
			if (spec.retime != null)
			{
				try
				{
					if (!ApplyRetime(spec.retime.factor.Value, applied, retimedCurves)) return 1;
					if (!expectedEndFrame.HasValue)
					{
						try { expectedEndFrame = RetimeInteger(endFrameBefore * spec.retime.factor.Value, "project EndFrame"); }
						catch (InvalidOperationException e)
						{
							Console.WriteLine("RETIME WARNING: project EndFrame: {0}; unchanged", e.Message);
							expectedEndFrame = endFrameBefore;
						}
						Core.EndFrame = expectedEndFrame.Value;
					}
				}
				catch (Exception e) { Console.Error.WriteLine("retime: " + e.Message); return 1; }
			}
			var retimedValues = applied.ToList();

			// Procedural models first, so the node ops below can reference a
			// model this spec creates.
			var procApplied = new List<Applied>();
			var procReport = new List<string>();
			var addedModels = new List<int>();
			for (int pi = 0; pi < (spec.procedural?.Count ?? 0); pi++)
			{
				var pop = spec.procedural[pi];
				var collection = Core.ProceduralModel.ProceduralModels;
				var models = collection.Values;
				string at = "procedural[" + pi.ToString(CultureInfo.InvariantCulture) + "]";
				ProceduralModelParameter model;
				int modelIndex;
				if (pop.add != null)
				{
					if (pop.index.HasValue)
					{
						Console.Error.WriteLine(at + ": give `index` to change a model or `add` to create one, not both");
						return 1;
					}
					if (pop.add.Trim().Length == 0)
					{
						Console.Error.WriteLine(at + ": `add` needs the new model's name");
						return 1;
					}
					int clash = models.ToList().FindIndex(m => m.Name.Value == pop.add);
					if (clash >= 0)
					{
						Console.Error.WriteLine(string.Format("{0}: a model named '{1}' already exists at index {2}; names must be unique so nodes can reference them", at, pop.add, clash));
						return 1;
					}
					model = new ProceduralModelParameter();
					string what = "defaults (Mesh sphere)";
					if (pop.from.HasValue)
					{
						if (pop.from.Value < 0 || pop.from.Value >= models.Count)
						{
							Console.Error.WriteLine(string.Format("{0}: no model at from index {1} ({2} in the project)", at, pop.from.Value, models.Count));
							return 1;
						}
						var fromModel = models[pop.from.Value];
						if (pop.expect != null && fromModel.Name.Value != pop.expect)
						{
							Console.Error.WriteLine(string.Format("procedural model {0} is named '{1}', spec expected '{2}'", pop.from.Value, fromModel.Name.Value, pop.expect));
							return 1;
						}
						if (!Core.Paste("ProceduralModel", model, Core.Copy("ProceduralModel", fromModel)))
						{
							Console.Error.WriteLine(string.Format("{0}: could not copy model {1}", at, pop.from.Value));
							return 1;
						}
						what = string.Format("copy of procedural {0} '{1}'", pop.from.Value, fromModel.Name.Value);
					}
					else if (pop.expect != null)
					{
						Console.Error.WriteLine(at + ": `expect` guards the `from` model; a model added from the defaults has nothing to guard");
						return 1;
					}
					model.Name.SetValue(pop.add);
					if (!collection.Add(model))
					{
						Console.Error.WriteLine(at + ": the project's procedural model list is full");
						return 1;
					}
					modelIndex = collection.Values.Count - 1;
					addedModels.Add(modelIndex);
					procReport.Add(string.Format("  procedural {0,-3} added '{1}': {2}", modelIndex, pop.add, what));
					procApplied.Add(new Applied { Node = "procedural " + modelIndex.ToString(CultureInfo.InvariantCulture), Key = "Name",
						Before = "(new)", Wanted = JsonSerializer.Serialize(pop.add) });
				}
				else
				{
					if (pop.from.HasValue)
					{
						Console.Error.WriteLine(at + ": `from` only applies with `add`");
						return 1;
					}
					if (!pop.index.HasValue)
					{
						Console.Error.WriteLine(at + ": needs `index` (change a model) or `add` (create one)");
						return 1;
					}
					if (pop.index.Value < 0 || pop.index.Value >= models.Count)
					{
						Console.Error.WriteLine(string.Format("procedural: no model at index {0} ({1} in the project)", pop.index.Value, models.Count));
						return 1;
					}
					model = models[pop.index.Value];
					modelIndex = pop.index.Value;
					if (pop.expect != null && model.Name.Value != pop.expect)
					{
						Console.Error.WriteLine(string.Format("procedural model {0} is named '{1}', spec expected '{2}'", pop.index.Value, model.Name.Value, pop.expect));
						return 1;
					}
				}
				string where = "procedural " + modelIndex.ToString(CultureInfo.InvariantCulture);
				foreach (var kv in pop.set ?? new Dictionary<string, JsonElement>())
				{
					string before, err;
					if (!TryResolve(model, kv.Key, out var leaf, out err) ||
						!TryDescribe(leaf, out before) ||
						!TrySet(leaf, kv.Value, out err))
					{
						Console.Error.WriteLine(string.Format("{0} ({1}) {2}: {3}", where, model.Name.Value, kv.Key, err ?? "unsupported value type"));
						return 1;
					}
					var prior = procApplied.FirstOrDefault(x => x.Node == where && x.Key == kv.Key);
					if (prior != null) prior.Wanted = kv.Value.GetRawText();
					else procApplied.Add(new Applied { Node = where, Key = kv.Key, Before = before, Wanted = kv.Value.GetRawText() });
				}
				// A Spline4 model built from the defaults takes its shape from all four
				// points; one left at its default ((1,0) (1,0.5) (1,1) (1,2)) silently
				// bends the profile (a lost Point4 folded a crystal's top into a bowl).
				if (pop.add != null && !pop.from.HasValue && model.Shape.PrimitiveType.Value == ProceduralModelPrimitiveType.Spline4)
				{
					var unset = Enumerable.Range(1, 4).Select(i => "Shape/Point" + i)
						.Where(k => !(pop.set ?? new Dictionary<string, JsonElement>()).Keys.Any(s => s == k)).ToList();
					if (unset.Count > 0)
						Console.WriteLine("WARNING: {0} '{1}' is a Spline4 built from the defaults but leaves {2} at the default ({3}); set all four points",
							where, model.Name.Value, string.Join(", ", unset),
							string.Join(", ", unset.Select(k => { TryResolve(model, k, out var l, out _); TryDescribe(l, out var d); return d; })));
				}
			}
			var carryDefs = new DefinitionTable();

			var tints = new List<Tuple<string, float, bool>>();
			var tintReport = new List<string>();
			var addReport = new List<string>();
			var shifts = new List<Applied>();
			var keySets = new List<Tuple<Applied, float[]>>();
			// Ops layer: a later `keys` or `shift` on the same curve changes what
			// the saved file must hold, so every earlier record for that curve is
			// brought up to the curve's current frames (and values), and only the
			// final state is verified (keys then shift used to fail VERIFY).
			void Resync(string node, string curvePath, Effekseer.Data.Value.IFCurve curve)
			{
				string frames = string.Join(",", curve.Keys.Select(k => k.Frame));
				float[] values = curve.Keys.Select(k => k.ValueAsFloat).ToArray();
				foreach (var s in shifts.Where(s => s.Node == node && s.Key == curvePath)) s.Wanted = frames;
				for (int i = 0; i < keySets.Count; i++)
					if (keySets[i].Item1.Node == node && keySets[i].Item1.Key == curvePath)
					{
						keySets[i].Item1.Wanted = frames;
						keySets[i] = Tuple.Create(keySets[i].Item1, values);
					}
			}
			foreach (var op in spec.edits ?? new List<EditOp>())
			{
				NodeBase target = IsRootPath(op.node) ? (NodeBase)Core.Root : Navigate(op.node);
				if (target == null)
				{
					Console.Error.WriteLine("no node at path '" + op.node + "'");
					return 1;
				}
				if (op.expect != null && target.Name.Value != op.expect)
				{
					Console.Error.WriteLine(string.Format("node '{0}' is named '{1}', spec expected '{2}'",
						op.node, target.Name.Value, op.expect));
					return 1;
				}
				string opPath = op.node;
				if (op.add != null)
				{
					string xml, what;
					if (foreignXml.TryGetValue(op, out var foreign))
					{
						xml = foreign.Item1;
						what = foreign.Item2;
					}
					else
					{
						var from = Navigate(op.add.from);
						if (from == null)
						{
							Console.Error.WriteLine("add: no node at path '" + op.add.from + "'");
							return 1;
						}
						xml = Core.Copy(from);
						what = op.add.from + " (" + from.Name.Value + ")";
					}
					int index = target.Children.Count;
					Node copy = target.AddChild();
					Core.Paste(copy, xml);
					if (!string.IsNullOrEmpty(op.add.name)) copy.Name.SetValue(op.add.name);
					opPath = (IsRootPath(op.node) ? "" : op.node.Trim() + "/") + index.ToString(CultureInfo.InvariantCulture);
					addReport.Add(string.Format("  {0,-14} added: copy of {1} as '{2}'",
						opPath, what, copy.Name.Value));
					// The paste restored the OTHER effect's model indices; carry each
					// definition across and repoint, then check every reference now
					// reaches a model identical to the one it had over there.
					if (foreignRefs.TryGetValue(op, out var carried) && carried.Count > 0)
					{
						var refReport = new List<string>();
						int modelsBefore = Core.ProceduralModel.ProceduralModels.Values.Count;
						ApplyRefs(copy, carried, carryDefs, refReport, false);
						bool unused = false;
						var landed = HarvestRefs(copy, ref unused).ToDictionary(r => r.KeyPath, r => r.DefinitionXml);
						var wrong = carried.Where(r => !landed.TryGetValue(r.KeyPath, out var xml) || xml != r.DefinitionXml).Select(r => r.KeyPath).ToList();
						foreach (var line in refReport.Where(l => l.StartsWith("WARNING"))) Console.Error.WriteLine("add: " + line);
						if (wrong.Count > 0 || refReport.Any(l => l.StartsWith("WARNING")))
						{
							foreach (var w in wrong) Console.Error.WriteLine("add: procedural model reference at " + w + " did not carry over");
							return 1;
						}
						int modelsAfter = Core.ProceduralModel.ProceduralModels.Values.Count;
						addReport.Add(string.Format("  {0,-14} carried {1} procedural model reference(s) from {2}{3}", opPath, carried.Count, Path.GetFileName(op.add.file),
							modelsAfter > modelsBefore ? string.Format(" as new model(s) {0}..{1}", modelsBefore, modelsAfter - 1) : " (models already carried by an earlier add)"));
					}
					target = copy;
				}
				foreach (var kv in op.set ?? new Dictionary<string, JsonElement>())
				{
					string before, err;
					if (!TryResolve(target, kv.Key, out var leaf, out err) ||
						!TryDescribe(leaf, out before) ||
						!TrySet(leaf, kv.Value, out err))
					{
						Console.Error.WriteLine(string.Format("node '{0}' ({1}) {2}: {3}",
							opPath, target.Name.Value, kv.Key, err ?? "unsupported value type"));
						return 1;
					}
					// Specs layer: a later op may set the same value again, and the
					// last one wins. Keep the source's value as "before" and check
					// only the final request.
					string normalized = EfkCompat.NormalizePath(kv.Key);
					// A per-node vector/range override also supersedes retime's
					// component writes. Both compatibility spellings address one value.
					applied.RemoveAll(x => retimedValues.Contains(x) && x.Node == opPath &&
						(EfkCompat.NormalizePath(x.Key).StartsWith(normalized + "/", StringComparison.Ordinal) ||
						 normalized.StartsWith(EfkCompat.NormalizePath(x.Key) + "/", StringComparison.Ordinal)));
					var prior = applied.FirstOrDefault(x => x.Node == opPath && EfkCompat.NormalizePath(x.Key) == normalized);
					if (prior != null) { prior.Wanted = kv.Value.GetRawText(); applied.Remove(prior); applied.Add(prior); }
					else applied.Add(new Applied { Node = opPath, Key = kv.Key, Before = before, Wanted = kv.Value.GetRawText() });
				}
				foreach (var sh in op.shift ?? new List<ShiftSpec>())
				{
					string err;
					if (!TryResolve(target, sh.curve, out var leaf, out err) || !(leaf.Value is Effekseer.Data.Value.IFCurve curve))
					{
						Console.Error.WriteLine(string.Format("node '{0}' ({1}) shift {2}: {3}",
							opPath, target.Name.Value, sh.curve, err ?? "not an FCurve"));
						return 1;
					}
					var keys = curve.Keys.ToList();
					// `by` is in frames. A Percent curve reads its keys as % of the
					// node's life, so frames and keys only agree when the node lives
					// exactly 100 frames, and a key past 100 never plays
					// (shineforge_sequence.py lost its orb fade this way).
					if (CurveTimeline(target, sh.curve) == "Percent" && !sh.percentOfLife)
					{
						string life = DescribeAt(target, "CommonValues/Life");
						var moved = keys.Where(k => k.Frame >= sh.from).Select(k => k.Frame + sh.by).ToList();
						if (life != "100" || moved.Any(f => f < 0 || f > 100))
						{
							Console.Error.WriteLine(string.Format(
								"node '{0}' ({1}) shift {2}: its keys are % of the node's life (Timeline Percent, life {3}), " +
								"but `by` moves them in frames{4}. Set \"{5}/Timeline\": \"Time\" in this op's `set` so every " +
								"key counts in frames, or add \"percentOfLife\": true to the shift to move them by percent.",
								opPath, target.Name.Value, sh.curve, life,
								moved.Any(f => f > 100) ? ", and some would land past 100%, where they never play" : "",
								ParentPath(sh.curve)));
							return 1;
						}
					}
					string before = string.Join(",", keys.Select(k => k.Frame));
					string wanted = string.Join(",", keys.Select(k => k.Frame >= sh.from ? k.Frame + sh.by : k.Frame));
					// Last key first, so a moved key never passes one still to move.
					foreach (var k in keys.Where(k => k.Frame >= sh.from).OrderByDescending(k => sh.by > 0 ? k.Frame : -k.Frame))
					{
						k.SetFrame(k.Frame + sh.by);
						k.Commit();
					}
					Resync(opPath, sh.curve, curve);
					shifts.Add(new Applied { Node = opPath, Key = sh.curve, Before = before, Wanted = wanted });
					if (!SampleExactly(target, opPath, op, sh.curve, applied)) return 1;
				}
				foreach (var ks in op.keys ?? new List<KeysSpec>())
				{
					string err;
					// float curves, and the int curves of colours (FCurveColorRGBA R/G/B/A, 0..255): an int
					// curve takes whole values only, so nothing is rounded silently
					if (!TryResolve(target, ks.curve, out var leaf, out err) ||
						!(leaf.Value is Effekseer.Data.Value.FCurve<float> || leaf.Value is Effekseer.Data.Value.FCurve<int>))
					{
						Console.Error.WriteLine(string.Format("node '{0}' ({1}) keys {2}: {3}",
							opPath, target.Name.Value, ks.curve, err ?? "not a float or int FCurve"));
						return 1;
					}
					if (ks.points == null || ks.points.Count == 0 || ks.points.Any(p => p.Length != 2))
					{
						Console.Error.WriteLine("keys " + ks.curve + ": points must be [frame, value] pairs");
						return 1;
					}
					var icurve = leaf.Value as Effekseer.Data.Value.FCurve<int>;
					if (icurve != null && ks.points.Any(p => p[1] != Math.Round(p[1])))
					{
						Console.Error.WriteLine(string.Format("node '{0}' ({1}) keys {2}: an int curve (a colour channel) takes " +
							"whole values only", opPath, target.Name.Value, ks.curve));
						return 1;
					}
					var curve = (Effekseer.Data.Value.IFCurve)leaf.Value;
					string before = string.Join(",", curve.Keys.Select(k => k.Frame));
					var fresh = ks.points.Select(p =>
					{
						var k = new Effekseer.Data.Value.FCurveKey<float>((int)Math.Round(p[0]), p[1]);
						k.SetInterpolationType(Effekseer.Data.Value.FCurveInterpolation.Linear);
						return k;
					}).OrderBy(k => k.Frame).ToArray();
					if (CurveTimeline(target, ks.curve) == "Percent" && fresh.Any(k => k.Frame < 0 || k.Frame > 100))
					{
						Console.Error.WriteLine(string.Format(
							"node '{0}' ({1}) keys {2}: the curve is Timeline Percent, so key frames are % of the node's life " +
							"and keys outside 0..100 never play. If the points are frames, set \"{3}/Timeline\": \"Time\" " +
							"in this op's `set` (a fresh location FCurve defaults to Percent).",
							opPath, target.Name.Value, ks.curve, ParentPath(ks.curve)));
						return 1;
					}
					if (icurve != null)
						icurve.SetKeys(fresh.Select(f =>
						{
							var k = new Effekseer.Data.Value.FCurveKey<int>(f.Frame, (int)Math.Round(f.Value));
							k.SetInterpolationType(Effekseer.Data.Value.FCurveInterpolation.Linear);
							return k;
						}).ToArray());
					else
						((Effekseer.Data.Value.FCurve<float>)curve).SetKeys(fresh);
					Resync(opPath, ks.curve, curve);
					keySets.Add(Tuple.Create(new Applied { Node = opPath, Key = ks.curve, Before = before + " keys",
						Wanted = string.Join(",", fresh.Select(k => k.Frame)) }, fresh.Select(k => k.Value).ToArray()));
					if (!SampleExactly(target, opPath, op, ks.curve, applied)) return 1;
				}
				if (op.tint != null)
				{
					var t = Tint(target, op.tint.hue, op.tint.saturation ?? 1f, op.tint.subtractiveOpposite);
					tints.Add(Tuple.Create(opPath, op.tint.hue, op.tint.subtractiveOpposite));
					tintReport.Add(string.Format("  {0,-14} tint to hue {1}: {2} colour value(s) recoloured{4}",
						opPath, op.tint.hue, t.Item1, t.Item2,
						t.Item3 > 0 ? string.Format(", {0} LIVE curve/gradient/HSV colour(s) NOT tinted", t.Item3) : ""));
				}
			}

			// Every model added by this spec must be drawn by some node, and
			// every reference set to one must be switched on, or it does nothing.
			var referenced = ProjectRefs(out var usedModels);
			foreach (int idx in addedModels.Where(i => !usedModels.Contains(i)))
				Console.WriteLine("WARNING: procedural {0} '{1}' was added but no node references it",
					idx, Core.ProceduralModel.ProceduralModels.Values[idx].Name.Value);
			foreach (var a in applied)
			{
				NodeBase n = IsRootPath(a.Node) ? (NodeBase)Core.Root : Navigate(a.Node);
				if (n == null || !TryResolve(n, a.Key, out var leaf, out _) || !(leaf.Value is ProceduralModelReference)) continue;
				string group = ParentPath(EfkCompat.NormalizePath(a.Key));          // DrawingValues/Model
				string mode = DescribeAt(n, group + "/ModelReference");
				string type = DescribeAt(n, ParentPath(group) + "/Type");            // DrawingValues/Type
				if (mode != "ProceduralModel" || type != "Model")
					Console.WriteLine("WARNING: node '{0}' ({1}) sets {2}, but {3}/ModelReference is {4} and {5}/Type is {6}; " +
						"the procedural model is drawn only with ModelReference ProceduralModel and Type Model",
						a.Node, n.Name.Value, a.Key, group, mode, ParentPath(group), type);
			}
			var modelXml = Core.ProceduralModel.ProceduralModels.Values.Select(m => Core.Copy("ProceduralModel", m)).ToList();

			// Added nodes are part of what the saved file must hold.
			nodesBefore = Core.Root.Children.Internal.Sum(c => CountSubtree(c));

			Directory.CreateDirectory(Path.GetDirectoryName(outPath));
			var copier = new ResourceCopier(Path.GetDirectoryName(outPath));
			var report = new List<string>();
			foreach (var child in Core.Root.Children.Internal)
				RewriteResources(child, copier, spec.allowMissingResources, report);
			if (copier.Errors.Count > 0)
			{
				foreach (var e in copier.Errors) Console.Error.WriteLine("ERROR: " + e);
				return 1;
			}

			// Snapshot the final curves after explicit keys/shift overrides. Verify
			// handles, values and interpolation as well as frames after saving.
			foreach (var c in retimedCurves)
				if (TryResolve(Navigate(c.Node), c.Path, out var leaf, out _) && leaf.Value is Effekseer.Data.Value.IFCurve curve)
				{
					c.Keys = CurveState(curve);
					c.Sampling = curve.Sampling.Value;
					c.Timeline = CurveTimeline(Navigate(c.Node), c.Path);
				}
			Core.SaveTo(outPath);

			// ---- verify: reload, same tree, every value reads back as set ----
			Core.New();
			if (!Core.LoadFrom(outPath))
			{
				Console.Error.WriteLine("VERIFY FAIL: our own output did not load");
				return 1;
			}
			int nodesAfter = Core.Root.Children.Internal.Sum(c => CountSubtree(c));
			bool ok = nodesAfter == nodesBefore;
			if (!ok) Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} nodes in, {1} out", nodesBefore, nodesAfter));

			foreach (var a in applied)
			{
				NodeBase n = IsRootPath(a.Node) ? (NodeBase)Core.Root : Navigate(a.Node);
				string err;
				if (n == null || !TryResolve(n, a.Key, out var leaf, out err) || !TryDescribe(leaf, out a.After))
				{
					Console.Error.WriteLine("VERIFY FAIL: cannot read back " + a.Node + " " + a.Key);
					ok = false;
					continue;
				}
				// Reading back is not enough: a setter can clamp, or quietly store
				// something other than what was asked. Compare to the request.
				string why;
				if (!Matches(leaf, JsonDocument.Parse(a.Wanted).RootElement, out why))
				{
					Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} {1} asked {2}, file holds {3}{4}",
						a.Node, a.Key, a.Wanted, a.After, why == null ? "" : " (" + why + ")"));
					ok = false;
				}
			}

			foreach (var a in procApplied)
			{
				int idx = int.Parse(a.Node.Substring("procedural ".Length), CultureInfo.InvariantCulture);
				var models = Core.ProceduralModel.ProceduralModels.Values;
				string err, why;
				if (idx >= models.Count || !TryResolve(models[idx], a.Key, out var leaf, out err) || !TryDescribe(leaf, out a.After))
				{
					Console.Error.WriteLine("VERIFY FAIL: cannot read back " + a.Node + " " + a.Key);
					ok = false;
					continue;
				}
				if (!Matches(leaf, JsonDocument.Parse(a.Wanted).RootElement, out why))
				{
					Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} {1} asked {2}, file holds {3}{4}",
						a.Node, a.Key, a.Wanted, a.After, why == null ? "" : " (" + why + ")"));
					ok = false;
				}
			}
			applied.AddRange(procApplied);

			// The whole model list, and the model every node references, must
			// come back exactly as saved: a reference that lands on another
			// index, or a definition that changed, draws a different shape.
			var modelXmlAfter = Core.ProceduralModel.ProceduralModels.Values.Select(m => Core.Copy("ProceduralModel", m)).ToList();
			if (modelXmlAfter.Count != modelXml.Count)
			{
				Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} procedural model(s) saved, {1} in the file", modelXml.Count, modelXmlAfter.Count));
				ok = false;
			}
			for (int i = 0; i < Math.Min(modelXml.Count, modelXmlAfter.Count); i++)
				if (modelXml[i] != modelXmlAfter[i])
				{
					Console.Error.WriteLine("VERIFY FAIL: procedural model " + i + " differs from what was saved");
					ok = false;
				}
			var referencedAfter = ProjectRefs(out _);
			foreach (var r in referenced)
				if (!referencedAfter.TryGetValue(r.Key, out var xml) || xml != r.Value)
				{
					Console.Error.WriteLine("VERIFY FAIL: project reference at " + r.Key + " does not reach the same definition after saving");
					ok = false;
				}
			foreach (var k in referencedAfter.Keys.Where(k => !referenced.ContainsKey(k)))
			{
				Console.Error.WriteLine("VERIFY FAIL: unexpected project reference at " + k + " after saving");
				ok = false;
			}

			if (expectedEndFrame.HasValue && Core.EndFrame != expectedEndFrame.Value)
			{
				Console.Error.WriteLine(string.Format("VERIFY FAIL: preview EndFrame asked {0}, file holds {1}", expectedEndFrame.Value, Core.EndFrame));
				ok = false;
			}
			foreach (var c in retimedCurves)
			{
				if (c.Keys == null || !TryResolve(Navigate(c.Node), c.Path, out var leaf, out _) || !(leaf.Value is Effekseer.Data.Value.IFCurve curve))
				{ Console.Error.WriteLine("VERIFY FAIL: cannot read retimed curve " + c.Node + " " + c.Path); ok = false; continue; }
				var got = CurveState(curve);
				bool same = curve.Sampling.Value == c.Sampling && CurveTimeline(Navigate(c.Node), c.Path) == c.Timeline &&
					got.Length == c.Keys.Length && got.Select((key, i) => key.Select((v, j) =>
					j == 0 || j == 6 ? v == c.Keys[i][j] : Math.Abs(v - c.Keys[i][j]) <= 1e-5 * Math.Max(1, Math.Abs(c.Keys[i][j]))).All(x => x)).All(x => x);
				Console.WriteLine("VERIFY {0}: retime curve {1} {2}, {3} keys (frames, values, handles, interpolation, Timeline {4}, Sampling {5})", same ? "OK" : "FAIL", c.Node, c.Path, got.Length, c.Timeline, c.Sampling);
				if (!same) ok = false;
			}

			// Shifted curves: the saved key frames must be the requested ones.
			foreach (var a in shifts)
			{
				NodeBase n = IsRootPath(a.Node) ? (NodeBase)Core.Root : Navigate(a.Node);
				string err;
				if (n == null || !TryResolve(n, a.Key, out var leaf, out err) || !(leaf.Value is Effekseer.Data.Value.IFCurve c))
				{
					Console.Error.WriteLine("VERIFY FAIL: cannot read back curve " + a.Node + " " + a.Key);
					ok = false;
					continue;
				}
				a.After = string.Join(",", c.Keys.Select(k => k.Frame));
				if (a.After != a.Wanted)
				{
					Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} {1} keys asked at {2}, file holds {3}", a.Node, a.Key, a.Wanted, a.After));
					ok = false;
				}
			}

			// Replaced curves: same key frames and values, all linear.
			foreach (var ksv in keySets)
			{
				var a = ksv.Item1;
				NodeBase n = IsRootPath(a.Node) ? (NodeBase)Core.Root : Navigate(a.Node);
				string err;
				if (n == null || !TryResolve(n, a.Key, out var leaf, out err) ||
					!(leaf.Value is Effekseer.Data.Value.FCurve<float> || leaf.Value is Effekseer.Data.Value.FCurve<int>))
				{
					Console.Error.WriteLine("VERIFY FAIL: cannot read back curve " + a.Node + " " + a.Key);
					ok = false;
					continue;
				}
				var got = ((Effekseer.Data.Value.IFCurve)leaf.Value).Keys.ToList();
				a.After = got.Count + " keys, " + got.First().Frame + ".." + got.Last().Frame;
				bool same = got.Count == ksv.Item2.Length &&
					string.Join(",", got.Select(k => k.Frame)) == a.Wanted &&
					got.Select((k, i) => Math.Abs(k.ValueAsFloat - ksv.Item2[i]) < 1e-4f).All(x => x) &&
					got.All(k => k.InterpolationType == Effekseer.Data.Value.FCurveInterpolation.Linear);
				if (!same)
				{
					Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} {1} keys differ from the request", a.Node, a.Key));
					ok = false;
				}
			}

			// Every colour under a tinted node must now sit at the tint's hue
			// (or be grey), read back from the saved file.
			foreach (var tt in tints)
			{
				NodeBase n = IsRootPath(tt.Item1) ? (NodeBase)Core.Root : Navigate(tt.Item1);
				int off = 0;
				foreach (var c in CollectColours(n).Item1)
				{
					var ends = c.Read();
					float wantHue = tt.Item3 && c.Subtractive ? (tt.Item2 + 180f) % 360f : tt.Item2;
					int targetH = (int)Math.Round(wantHue / 360.0 * HsvHueUnits) % (int)HsvHueUnits;
					if (c.Hsv)
					{
						if (Math.Abs((ends[0][0] + ends[1][0]) / 2.0 - targetH) > 1.01 || ends[1][0] - ends[0][0] > 2 * HsvShimmer)
						{
							off++;
							Console.Error.WriteLine("  off-hue (HSV): " + c.Where + " = H " + ends[0][0] + ".." + ends[1][0]);
						}
						continue;
					}
					bool bad = !c.Multi && ends.Count == 2 && (ends[0][0] != ends[1][0] || ends[0][1] != ends[1][1] || ends[0][2] != ends[1][2]);
					foreach (var rgb in ends)
					{
						double h, sat, v; RgbToHsv(rgb[0], rgb[1], rgb[2], out h, out sat, out v);
						// One 8-bit step in a channel moves the hue by 60/(max-min)
						// degrees, so a near-grey colour cannot sit exactly on the
						// target; allow that quantisation and no more.
						int spread = Math.Max(rgb[0], Math.Max(rgb[1], rgb[2])) - Math.Min(rgb[0], Math.Min(rgb[1], rgb[2]));
						double tol = Math.Max(4.0, spread > 0 ? 60.0 / spread : 360.0);
						if (sat > 0.04 && v > 0.04 && HueDistance(h, wantHue) > tol) bad = true;
					}
					if (bad)
					{
						off++;
						Console.Error.WriteLine("  off-hue: " + c.Where + " = " + string.Join(" .. ", ends.Select(e => "(" + string.Join(",", e) + ")")));
					}
				}
				if (off > 0) { Console.Error.WriteLine(string.Format("VERIFY FAIL: {0} colour(s) under {1} are not at hue {2} or still hold a per-channel random range", off, tt.Item1, tt.Item2)); ok = false; }
			}

			Console.WriteLine("Edited copy of {0}", source);
			Console.WriteLine("  -> {0}", outPath);
			foreach (var line in copier.Summary()) Console.WriteLine("  " + line);
			foreach (var line in report) Console.WriteLine("  " + line);
			foreach (var line in procReport) Console.WriteLine(line);
			foreach (var line in addReport) Console.WriteLine(line);
			foreach (var a in applied)
				Console.WriteLine(string.Format("  {0,-14} {1}: {2} -> {3}", a.Node, a.Key, a.Before, a.After));
			if (expectedEndFrame.HasValue)
				Console.WriteLine(string.Format("  preview EndFrame: {0} -> {1}", endFrameBefore, Core.EndFrame));
			foreach (var a in shifts)
				Console.WriteLine(string.Format("  {0,-14} {1} key frames: {2} -> {3}", a.Node, a.Key, a.Before, a.After));
			foreach (var ksv in keySets)
				Console.WriteLine(string.Format("  {0,-14} {1}: {2} -> {3}", ksv.Item1.Node, ksv.Item1.Key, ksv.Item1.Before, ksv.Item1.After));
			foreach (var line in tintReport) Console.WriteLine(line);
			Console.WriteLine(ok ? string.Format("VERIFY OK: {0} nodes, {1} value(s) set{3}{4} and read back from the saved file{2}{5}", nodesAfter, applied.Count,
									tints.Count > 0 ? ", every tinted colour at its hue" : "", shifts.Count > 0 ? ", " + shifts.Count + " curve(s) shifted" : "",
									keySets.Count > 0 ? ", " + keySets.Count + " curve(s) rewritten" : "",
									modelXml.Count > 0 ? string.Format(", {0} procedural model(s) ({1} added) and {2} project reference(s) identical after saving", modelXml.Count, modelXml.Count - modelsAtLoad, referenced.Count) : "")
								 : "VERIFY FAILED");
			// The editor core reloads any count; the runtime refuses an effect with more than 1024
			// rendering nodes, images, models... (ElementLimits) without an error. The file is still
			// written (a step towards a smaller effect may need it), but lint --strict fails it.
			var limits = ElementLimits.Measure();
			foreach (string problem in ElementLimits.Problems(limits.Counts))
				Console.WriteLine("WARNING: {0}: the runtime will not load this effect (Effekseer.Effect.cpp elementCountMax {1}; the game shows no error)", problem, ElementLimits.Max);
			if (limits.Error != null)
				Console.WriteLine("WARNING: the runtime's 1024 caps were not all checked: " + limits.Error);
			return ok ? 0 : 1;
		}

		static bool IsRootPath(string p)
		{
			return string.IsNullOrWhiteSpace(p) || p.Trim().Equals("root", StringComparison.OrdinalIgnoreCase);
		}

		/// <summary>
		/// Every bound procedural-model and dynamic-equation reference in the
		/// loaded effect, keyed by node path and property path, mapped to the
		/// definition it reaches (kind, index and the definition's XML).
		/// </summary>
		static Dictionary<string, string> ProjectRefs(out HashSet<int> usedModels)
		{
			var found = new Dictionary<string, string>(StringComparer.Ordinal);
			var used = new HashSet<int>();
			int i = 0;
			foreach (var child in Core.Root.Children.Internal)
			{
				WalkSubtree(child, "/" + i.ToString(CultureInfo.InvariantCulture), (keyPath, value) =>
				{
					string kind = ReferenceKind(value);
					int idx = kind == null ? -1 : ReferenceIndex(value);
					if (idx < 0) return;
					string xml;
					if (kind == "ProceduralModel")
					{
						var list = Core.ProceduralModel.ProceduralModels.Values;
						xml = idx < list.Count ? Core.Copy("ProceduralModel", list[idx]) : "(dangling)";
						used.Add(idx);
					}
					else
					{
						var list = Core.Dynamic.Equations.Values;
						xml = idx < list.Count ? Core.Copy("DynamicEquation", list[idx]) : "(dangling)";
					}
					found[keyPath] = kind + " " + idx.ToString(CultureInfo.InvariantCulture) + " " + xml;
				});
				i++;
			}
			usedModels = used;
			return found;
		}

		// ------------------------------------------------------------ resolve

		/// <summary>
		/// Resolved address of one value. For a Center/Max/Min component of a
		/// WithRandom value, Owner is the WithRandom and Component names the part.
		/// </summary>
		class Leaf
		{
			public object Value;
			public object Owner;
			public string Component;
		}

		static bool TryResolve(object node, string key, out Leaf leaf, out string err)
		{
			leaf = null; err = null;
			const string materialPrefix = "RendererCommonValues/MaterialFile/";
			if (key.StartsWith(materialPrefix + "Uniforms/", StringComparison.Ordinal) ||
				key.StartsWith(materialPrefix + "Textures/", StringComparison.Ordinal))
			{
				bool texture = key.StartsWith(materialPrefix + "Textures/", StringComparison.Ordinal);
				string name = key.Substring(materialPrefix.Length + (texture ? "Textures/" : "Uniforms/").Length);
				if (!(node is Node n) || n.RendererCommonValues.Material.Value != RendererCommonValues.MaterialType.File)
				{ err = "named material values require Material File on a drawing node"; return false; }
				var slots = n.RendererCommonValues.MaterialFile.GetValueStatus()
					.Where(s => s.IsShown && s.Key.UniformName == name &&
						(texture ? s.Value is Effekseer.Data.Value.PathForImage : !(s.Value is Effekseer.Data.Value.Path))).ToArray();
				if (slots.Length != 1)
				{ err = "unknown or ambiguous material " + (texture ? "texture" : "uniform") + " name: " + name; return false; }
				leaf = new Leaf { Value = slots[0].Value };
				return true;
			}
			key = EfkCompat.NormalizePath(key);   // 1.7 and 1.8 spellings of the moved CommonValues settings
			var segs = key.Split(new[] { '/', '.' }, StringSplitOptions.RemoveEmptyEntries);
			object cur = node;
			for (int i = 0; i < segs.Length; i++)
			{
				string seg = segs[i];
				bool last = i == segs.Length - 1;

				if (last && (cur is Effekseer.Data.Value.FloatWithRandom || cur is Effekseer.Data.Value.IntWithRandom)
					&& (seg == "Center" || seg == "Max" || seg == "Min"))
				{
					leaf = new Leaf { Owner = cur, Component = seg };
					return true;
				}

				var prop = cur.GetType().GetProperty(seg, BindingFlags.Public | BindingFlags.Instance);
				// A few values are public fields, e.g. FCurveVector3D.Timeline
				// (which defaults to Percent: keys are then % of the node's life).
				var field = prop == null ? cur.GetType().GetField(seg, BindingFlags.Public | BindingFlags.Instance) : null;
				if (field != null)
				{
					var fio = field.GetCustomAttributes(typeof(IOAttribute), true).FirstOrDefault() as IOAttribute;
					if (fio != null && !fio.Export)
					{
						err = string.Format("'{0}' is [IO(Export = false)]: it would be dropped on save", seg);
						return false;
					}
					cur = field.GetValue(cur);
					if (cur == null) { err = "'" + seg + "' is null"; return false; }
					continue;
				}
				if (prop == null)
				{
					err = string.Format("'{0}' has no property '{1}'", cur.GetType().Name, seg);
					return false;
				}
				var io = prop.GetCustomAttributes(typeof(IOAttribute), true).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export)
				{
					err = string.Format("'{0}' is [IO(Export = false)]: it would be dropped on save", seg);
					return false;
				}
				cur = prop.GetValue(cur, null);
				if (cur == null)
				{
					err = "'" + seg + "' is null";
					return false;
				}
			}
			leaf = new Leaf { Value = cur };
			return true;
		}

		// ------------------------------------------------------------ read

		static string F(float f) { return f.ToString("0.###", CultureInfo.InvariantCulture); }

		static bool TryDescribe(Leaf leaf, out string s)
		{
			s = null;
			if (leaf.Owner is Effekseer.Data.Value.FloatWithRandom fo)
			{
				s = F(leaf.Component == "Center" ? fo.GetCenter() : leaf.Component == "Max" ? fo.GetMax() : fo.GetMin());
				return true;
			}
			if (leaf.Owner is Effekseer.Data.Value.IntWithRandom io)
			{
				s = (leaf.Component == "Center" ? io.GetCenter() : leaf.Component == "Max" ? io.GetMax() : io.GetMin()).ToString(CultureInfo.InvariantCulture);
				return true;
			}
			return TryDescribe(leaf.Value, out s);
		}

		static bool TryDescribe(object v, out string s)
		{
			s = null;
			switch (v)
			{
				case Effekseer.Data.Value.Float f: s = F(f.Value); return true;
				case Effekseer.Data.Value.Int i: s = i.Value.ToString(CultureInfo.InvariantCulture); return true;
				case Effekseer.Data.Value.Boolean b: s = b.Value ? "true" : "false"; return true;
				case Effekseer.Data.Value.IntWithInifinite ii: s = ii.Infinite.Value ? "inf" : ii.Value.Value.ToString(CultureInfo.InvariantCulture); return true;
				case Effekseer.Data.Value.FloatWithRandom fr:
					s = fr.GetMin() == fr.GetMax() ? F(fr.GetMin()) : "[" + F(fr.GetMin()) + ".." + F(fr.GetMax()) + "]";
					return true;
				case Effekseer.Data.Value.IntWithRandom ir:
					s = ir.GetMin() == ir.GetMax() ? ir.GetMin().ToString(CultureInfo.InvariantCulture) : "[" + ir.GetMin() + ".." + ir.GetMax() + "]";
					return true;
				case Effekseer.Data.Value.Vector3D v3:
					s = "(" + F(v3.X.Value) + ", " + F(v3.Y.Value) + ", " + F(v3.Z.Value) + ")";
					return true;
				case Effekseer.Data.Value.Vector4D v4:
					s = "(" + F(v4.X.Value) + ", " + F(v4.Y.Value) + ", " + F(v4.Z.Value) + ", " + F(v4.W.Value) + ")";
					return true;
				case Effekseer.Data.Value.Vector2D v2:
					s = "(" + F(v2.X.Value) + ", " + F(v2.Y.Value) + ")";
					return true;
				case Effekseer.Data.Value.Path p:
					s = string.IsNullOrEmpty(p.AbsolutePath) ? "(none)" : Path.GetFileName(p.AbsolutePath);
					return true;
				case Effekseer.Data.Value.String str:
					s = "\"" + str.Value + "\"";
					return true;
				case Effekseer.Data.Value.Color c:
					s = "rgba(" + c.R.Value + ", " + c.G.Value + ", " + c.B.Value + ", " + c.A.Value + ")";
					return true;
				case Effekseer.Data.Value.ColorWithRandom cr:
					string d1, d2, d3, d4;
					TryDescribe(cr.R, out d1); TryDescribe(cr.G, out d2); TryDescribe(cr.B, out d3); TryDescribe(cr.A, out d4);
					s = "rgba(" + d1 + ", " + d2 + ", " + d3 + ", " + d4 + ")";
					return true;
				// The index into the project's list and the model's name; the
				// model's own values are dumped once, under "### procedural <i>".
				case ProceduralModelReference pr:
					s = pr.Value == null ? "(none)" : "#" + pr.Index.ToString(CultureInfo.InvariantCulture) + " \"" + pr.Value.Name.Value + "\"";
					return true;
			}
			var t = v.GetType();
			if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(Effekseer.Data.Value.Enum<>))
			{
				s = t.GetProperty("Value").GetValue(v, null).ToString();
				return true;
			}
			return false;
		}

		// ------------------------------------------------------------ write

		static bool TrySet(Leaf leaf, JsonElement j, out string err)
		{
			err = null;
			if (leaf.Owner is Effekseer.Data.Value.FloatWithRandom fo)
			{
				if (j.ValueKind != JsonValueKind.Number) { err = "expected a number"; return false; }
				float x = j.GetSingle();
				if (leaf.Component == "Center") fo.SetCenter(x); else if (leaf.Component == "Max") fo.SetMax(x); else fo.SetMin(x);
				return true;
			}
			if (leaf.Owner is Effekseer.Data.Value.IntWithRandom io)
			{
				if (!TryInt(j, out int x)) { err = "expected a whole number, got " + j.GetRawText(); return false; }
				if (leaf.Component == "Center") io.SetCenter(x); else if (leaf.Component == "Max") io.SetMax(x); else io.SetMin(x);
				return true;
			}
			return TrySet(leaf.Value, j, out err);
		}

		/// <summary>
		/// A number sets a scalar, or fixes a random range at that value.
		/// [lo, hi] sets a random range. [x, y, z] sets a vector, [r, g, b] or
		/// [r, g, b, a] a colour. An enum takes its integer or its name.
		/// </summary>

		// Private extension: explicit byte-valued RGBA bounds, without flattening randomness.
		static bool TryColourRange(JsonElement j, out int[] lo, out int[] hi, out string err)
		{
			lo = null; hi = null; err = null;
			if (j.ValueKind != JsonValueKind.Object || j.EnumerateObject().Count() != 2 ||
				!j.TryGetProperty("min", out var min) || !j.TryGetProperty("max", out var max) ||
				min.ValueKind != JsonValueKind.Array || max.ValueKind != JsonValueKind.Array ||
				min.GetArrayLength() != 4 || max.GetArrayLength() != 4)
			{ err = "colour range requires min and max arrays of four RGBA bytes"; return false; }
			lo = new int[4]; hi = new int[4];
			for (int i = 0; i < 4; i++)
			{
				if (min[i].ValueKind != JsonValueKind.Number || max[i].ValueKind != JsonValueKind.Number ||
					!TryInt(min[i], out lo[i]) || !TryInt(max[i], out hi[i]) ||
					lo[i] < 0 || hi[i] > 255 || lo[i] > hi[i])
				{ err = "RGBA range bounds must be ordered integers in 0..255"; return false; }
			}
			return true;
		}

		static bool TrySet(object v, JsonElement j, out string err)
		{
			err = null;
			float[] arr = j.ValueKind == JsonValueKind.Array ? j.EnumerateArray().Select(e => e.GetSingle()).ToArray() : null;
			if ((arr != null && arr.Any(x => !float.IsFinite(x))) ||
				(j.ValueKind == JsonValueKind.Number && !float.IsFinite(j.GetSingle())))
			{ err = "expected finite floats"; return false; }
			bool num = j.ValueKind == JsonValueKind.Number;
			bool whole = TryInt(j, out int iv);
			if (num && !whole && (v is Effekseer.Data.Value.Int || v is Effekseer.Data.Value.IntWithInifinite ||
				v is Effekseer.Data.Value.IntWithRandom || IsEnum(v)))
			{ err = "expected a whole number, got " + j.GetRawText(); return false; }
			if (arr != null && v is Effekseer.Data.Value.IntWithRandom && arr.Any(a => a != Math.Floor(a)))
			{ err = "expected whole numbers, got " + j.GetRawText(); return false; }

			switch (v)
			{
				case Effekseer.Data.Value.Float f when num: f.SetValue(j.GetSingle()); return true;
				case Effekseer.Data.Value.Int i when whole: i.SetValue(iv); return true;
				case Effekseer.Data.Value.Boolean b when j.ValueKind == JsonValueKind.True || j.ValueKind == JsonValueKind.False:
					b.SetValue(j.GetBoolean()); return true;
				case Effekseer.Data.Value.IntWithInifinite ii when whole:
					ii.Infinite.SetValue(false); ii.Value.SetValue(iv); return true;
				// "inf", as dump prints it: an endless count (MaxGeneration of a looping emitter)
				case Effekseer.Data.Value.IntWithInifinite ii when j.ValueKind == JsonValueKind.String && j.GetString() == "inf":
					ii.Infinite.SetValue(true); return true;
				case Effekseer.Data.Value.FloatWithRandom fr when num:
					SetRange(fr, j.GetSingle(), j.GetSingle()); return true;
				case Effekseer.Data.Value.FloatWithRandom fr when arr != null && arr.Length == 2:
					SetRange(fr, Math.Min(arr[0], arr[1]), Math.Max(arr[0], arr[1])); return true;
				case Effekseer.Data.Value.IntWithRandom ir when whole:
					SetRange(ir, iv, iv); return true;
				case Effekseer.Data.Value.IntWithRandom ir when arr != null && arr.Length == 2:
					SetRange(ir, (int)Math.Min(arr[0], arr[1]), (int)Math.Max(arr[0], arr[1])); return true;
				// A resource path: absolute, or relative to the spec. The file must
				// exist; RewriteResources then copies it beside the output.
				case Effekseer.Data.Value.Path p when j.ValueKind == JsonValueKind.String:
				{
					string file = j.GetString();
					// "" clears the slot (e.g. a Track with no colour texture, so
					// its vertex colours alone make the cross-section).
					if (file.Length == 0) { p.SetAbsolutePath(""); return true; }
					if (!Path.IsPathRooted(file)) file = Path.GetFullPath(Path.Combine(editSpecDir, file));
					if (!File.Exists(file)) { err = "no such file: " + file; return false; }
					p.SetAbsolutePath(file);
					return true;
				}
				// Text, e.g. "Name": relabel a node so it can be found and
				// switched off in the editor.
				case Effekseer.Data.Value.String str when j.ValueKind == JsonValueKind.String:
					str.SetValue(j.GetString()); return true;
				case Effekseer.Data.Value.Vector2D v2 when arr != null && arr.Length == 2:
					v2.X.SetValue(arr[0]); v2.Y.SetValue(arr[1]); return true;
				case Effekseer.Data.Value.Vector4D v4 when arr != null && arr.Length == 4:
					v4.X.SetValue(arr[0]); v4.Y.SetValue(arr[1]); v4.Z.SetValue(arr[2]); v4.W.SetValue(arr[3]); return true;
				case Effekseer.Data.Value.Vector3D v3 when arr != null && arr.Length == 3:
					v3.X.SetValue(arr[0]); v3.Y.SetValue(arr[1]); v3.Z.SetValue(arr[2]); return true;
				// The alpha argument must always be passed: Effekseer's own
				// default of -1 is stored as-is and clamps to 0, so an [r, g, b]
				// colour set through it comes out fully transparent.
				case Effekseer.Data.Value.Color c when arr != null && (arr.Length == 3 || arr.Length == 4):
					c.SetValue((int)arr[0], (int)arr[1], (int)arr[2], arr.Length == 4 ? (int)arr[3] : c.A.Value); return true;
				case Effekseer.Data.Value.ColorWithRandom cr when j.ValueKind == JsonValueKind.Object:
				{
					if (!TryColourRange(j, out var lo, out var hi, out err)) return false;
					// Setters can move the opposite bound; land both endpoints explicitly.
					cr.SetMax(hi[0], hi[1], hi[2], hi[3]);
					cr.SetMin(lo[0], lo[1], lo[2], lo[3]);
					cr.SetMax(hi[0], hi[1], hi[2], hi[3]);
					return true;
				}
				case Effekseer.Data.Value.ColorWithRandom cr when arr != null && (arr.Length == 3 || arr.Length == 4):
					cr.SetMax((int)arr[0], (int)arr[1], (int)arr[2], arr.Length == 4 ? (int)arr[3] : cr.A.GetMax());
					cr.SetMin((int)arr[0], (int)arr[1], (int)arr[2], arr.Length == 4 ? (int)arr[3] : cr.A.GetMin());
					return true;
				// A procedural model by its index in the project's list, or by
				// its name when exactly one model has it.
				case ProceduralModelReference pr:
				{
					var models = Core.ProceduralModel.ProceduralModels.Values;
					int target;
					if (whole) target = iv;
					else if (j.ValueKind == JsonValueKind.String)
					{
						string wanted = j.GetString();
						var hits = Enumerable.Range(0, models.Count).Where(k => models[k].Name.Value == wanted).ToList();
						if (hits.Count != 1)
						{
							err = hits.Count == 0
								? string.Format("no procedural model named '{0}' (the project has: {1})", wanted,
									models.Count == 0 ? "none" : string.Join(", ", models.Select((m, k) => k + " '" + m.Name.Value + "'")))
								: string.Format("{0} procedural models are named '{1}' (indices {2}); reference one by index", hits.Count, wanted, string.Join(", ", hits));
							return false;
						}
						target = hits[0];
					}
					else { err = "a procedural model reference takes a model index or a unique model name"; return false; }
					if (target < 0 || target >= models.Count)
					{
						err = string.Format("no procedural model at index {0} ({1} in the project)", target, models.Count);
						return false;
					}
					pr.SetValueWithIndex(target);
					if (pr.Index != target) { err = "the reference did not take index " + target; return false; }
					return true;
				}
			}

			var t = v.GetType();
			if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(Effekseer.Data.Value.Enum<>))
			{
				var et = t.GetGenericArguments()[0];
				object ev;
				if (whole) ev = System.Enum.ToObject(et, iv);
				else if (j.ValueKind == JsonValueKind.String) ev = System.Enum.Parse(et, j.GetString(), true);
				else { err = "expected an enum integer or name"; return false; }
				t.GetMethod("SetValue", new[] { et }).Invoke(v, new[] { ev });
				return true;
			}

			err = string.Format("cannot set a {0} from {1}", t.Name, j.GetRawText());
			return false;
		}

		/// <summary>
		/// A whole number, also when written with a fraction part: Python's json
		/// writes a float 60 as 60.0, and GetInt32 throws on that.
		/// </summary>
		static bool TryInt(JsonElement j, out int x)
		{
			x = 0;
			if (j.ValueKind != JsonValueKind.Number) return false;
			if (j.TryGetInt32(out x)) return true;
			double d = j.GetDouble();
			if (d != Math.Floor(d) || d < int.MinValue || d > int.MaxValue) return false;
			x = (int)d;
			return true;
		}

		static bool IsEnum(object v)
		{
			var t = v.GetType();
			return t.IsGenericType && t.GetGenericTypeDefinition() == typeof(Effekseer.Data.Value.Enum<>);
		}

		// ------------------------------------------------------------ tint

		/// <summary>One colour slot: read its RGB triples, write them back.</summary>
		class ColourSlot
		{
			public Func<List<int[]>> Read;
			public Action<List<int[]>> Write;
			public string Where;
			/// <summary>
			/// Stored as HSVA: Read/Write carry raw [H, S, V] ends (H 0..252 is a
			/// full turn in the runtime's HSVToRGB), not RGB.
			/// </summary>
			public bool Hsv;
			/// <summary>
			/// A gradient: Read/Write carry one RGB triple per colour marker
			/// (0..255), not a random range, so they are never collapsed.
			/// </summary>
			public bool Multi;
			/// <summary>On a node drawn with AlphaBlend Sub.</summary>
			public bool Subtractive;
		}

		/// <summary>The runtime's hue unit: 42 per 60 degrees, 252 a full turn.</summary>
		const double HsvHueUnits = 252.0;
		/// <summary>Widest hue range a tint keeps on an HSV colour: +-7 units, about +-10 degrees.</summary>
		const int HsvShimmer = 7;

		/// <summary>
		/// Every exported Color and ColorWithRandom on a node and its descendants,
		/// and a count of colours this cannot recolour (FCurves, gradients).
		/// HSV colours are slots too, flagged Hsv.
		/// Dead slots (a Fixed colour on a node set to Easing) are included on
		/// purpose: recolouring them is harmless and keeps a later type switch in hue.
		/// </summary>
		static Tuple<List<ColourSlot>, int> CollectColours(NodeBase root)
		{
			var slots = new List<ColourSlot>();
			int skipped = 0;
			var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
			Action<object, int, string, bool> walk = null;
			walk = (o, depth, where, sub) =>
			{
				if (o == null || depth > 24) return;
				var t = o.GetType();
				if (t.Namespace == null || !t.Namespace.StartsWith("Effekseer")) return;
				if (o is NodeBase.ChildrenCollection) return;
				if (!seen.Add(o)) return;

				if (o is Effekseer.Data.Value.Color c)
				{
					if (c.GetColorSpace() != ColorSpace.RGBA)
					{
						slots.Add(new ColourSlot
						{
							Where = where, Hsv = true, Subtractive = sub,
							Read = () => new List<int[]> { new[] { c.R.Value, c.G.Value, c.B.Value }, new[] { c.R.Value, c.G.Value, c.B.Value } },
							Write = l => c.SetValue(l[0][0], l[0][1], l[0][2], c.A.Value),
						});
						return;
					}
					slots.Add(new ColourSlot
					{
						Where = where, Subtractive = sub,
						Read = () => new List<int[]> { new[] { c.R.Value, c.G.Value, c.B.Value } },
						Write = l => c.SetValue(l[0][0], l[0][1], l[0][2], c.A.Value),
					});
					return;
				}
				if (o is Effekseer.Data.Value.ColorWithRandom cr)
				{
					if (cr.GetColorSpace() != ColorSpace.RGBA)
					{
						// A random HSV colour (often a random hue: rainbow sparkles).
						slots.Add(new ColourSlot
						{
							Where = where, Hsv = true, Subtractive = sub,
							Read = () => new List<int[]> {
								new[] { cr.R.GetMin(), cr.G.GetMin(), cr.B.GetMin() },
								new[] { cr.R.GetMax(), cr.G.GetMax(), cr.B.GetMax() } },
							Write = l =>
							{
								cr.SetMax(l[1][0], l[1][1], l[1][2], cr.A.GetMax());
								cr.SetMin(l[0][0], l[0][1], l[0][2], cr.A.GetMin());
								cr.SetMax(l[1][0], l[1][1], l[1][2], cr.A.GetMax());
							},
						});
						return;
					}
					slots.Add(new ColourSlot
					{
						Where = where, Subtractive = sub,
						Read = () => new List<int[]> {
							new[] { cr.R.GetMin(), cr.G.GetMin(), cr.B.GetMin() },
							new[] { cr.R.GetMax(), cr.G.GetMax(), cr.B.GetMax() } },
						Write = l =>
						{
							// Recolouring each end can cross a channel's min over its max.
							int[] lo = new int[3], hi = new int[3];
							for (int k = 0; k < 3; k++) { lo[k] = Math.Min(l[0][k], l[1][k]); hi[k] = Math.Max(l[0][k], l[1][k]); }
							cr.SetMax(hi[0], hi[1], hi[2], cr.A.GetMax());
							cr.SetMin(lo[0], lo[1], lo[2], cr.A.GetMin());
							cr.SetMax(hi[0], hi[1], hi[2], cr.A.GetMax());
						},
					});
					return;
				}
				// A StandardColor carries all its slots at once; only the one its
				// Type selects is live. Count a curve or gradient only when live.
				if (o is StandardColor sc)
				{
					var ty = sc.Type.Value;
					if (ty == StandardColorType.FCurve) skipped++;
				}
				if (o is Effekseer.Data.Value.Gradient gr)
				{
					// Marker colours are 0..1 floats scaled by a separate Intensity,
					// which is left alone (it is brightness, not hue).
					slots.Add(new ColourSlot
					{
						Where = where, Multi = true, Subtractive = sub,
						Read = () => gr.GetValue().ColorMarkers.Select(m => new[] {
							(int)Math.Round(Math.Max(0, Math.Min(1, m.ColorR)) * 255),
							(int)Math.Round(Math.Max(0, Math.Min(1, m.ColorG)) * 255),
							(int)Math.Round(Math.Max(0, Math.Min(1, m.ColorB)) * 255) }).ToList(),
						Write = l =>
						{
							var st = (Effekseer.Data.Value.Gradient.State)gr.GetValue().Clone();
							for (int k = 0; k < st.ColorMarkers.Length && k < l.Count; k++)
							{
								st.ColorMarkers[k].ColorR = l[k][0] / 255f;
								st.ColorMarkers[k].ColorG = l[k][1] / 255f;
								st.ColorMarkers[k].ColorB = l[k][2] / 255f;
							}
							gr.SetValue(st);
						},
					});
					return;
				}
				if (t.Name == "FCurveColor") return;
				if (t.Name.StartsWith("FCurve")) return;

				if (o is NodeBase nb)
				{
					bool nbSub = nb is Node nd && nd.RendererCommonValues.AlphaBlend.Value == AlphaBlendType.Sub;
					foreach (var prop in nb.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
					{
						if (prop.Name == "Children" || prop.Name == "Parent") continue;
						if (prop.GetIndexParameters().Length > 0) continue;
						var io = prop.GetCustomAttributes(typeof(IOAttribute), false).FirstOrDefault() as IOAttribute;
						if (io != null && !io.Export) continue;
						object v; try { v = prop.GetValue(nb, null); } catch { continue; }
						walk(v, depth + 1, nb.Name.Value + ":" + prop.Name, nbSub);
					}
					foreach (var child in nb.Children.Internal) walk(child, depth + 1, where, false);
					return;
				}

				foreach (var prop in t.GetProperties(BindingFlags.Public | BindingFlags.Instance))
				{
					if (prop.GetIndexParameters().Length > 0) continue;
					var io = prop.GetCustomAttributes(typeof(IOAttribute), false).FirstOrDefault() as IOAttribute;
					if (io != null && !io.Export) continue;
					object v; try { v = prop.GetValue(o, null); } catch { continue; }
					walk(v, depth + 1, where + "/" + prop.Name, sub);
				}
			};
			walk(root, 0, "", false);
			return Tuple.Create(slots, skipped);
		}

		/// <summary>
		/// Move every colour to one hue, keeping its saturation and brightness:
		/// white stays white, black stays black, and alpha is never touched, so
		/// fades and dark layers behave as before. Only worth doing where the
		/// textures are grey, since then the vertex colour is the whole hue.
		/// </summary>
		static Tuple<int, int, int> Tint(NodeBase root, float baseHue, float saturation = 1f, bool subtractiveOpposite = false)
		{
			var found = CollectColours(root);
			int changed = 0, grey = 0, collapsed = 0;
			foreach (var slot in found.Item1)
			{
				float hueDegrees = subtractiveOpposite && slot.Subtractive ? (baseHue + 180f) % 360f : baseHue;
				int target = (int)Math.Round(hueDegrees / 360.0 * HsvHueUnits) % (int)HsvHueUnits;
				var cols = slot.Read();
				if (slot.Hsv)
				{
					// Hue range onto the target, narrowed to a shimmer of at most
					// +-10 degrees; saturation scaled, brightness kept. Channels
					// are drawn independently, but hue alone varying is fine here.
					int half = Math.Min(HsvShimmer, (cols[1][0] - cols[0][0]) / 2);
					int lo = Math.Max(0, target - half), hi = Math.Min((int)HsvHueUnits, target + half);
					Func<int, int> sat = x => (int)Math.Round(Math.Max(0, Math.Min(255, x * saturation)));
					slot.Write(new List<int[]> {
						new[] { lo, sat(cols[0][1]), cols[0][2] },
						new[] { hi, sat(cols[1][1]), cols[1][2] } });
					changed++;
					continue;
				}
				// The runtime draws a random colour one channel at a time, so a
				// range whose two ends share a hue still yields other hues when
				// red comes from one end and green from the other (seen as pink
				// and green streaks in a gold impact). Collapse a range to its
				// average; alpha keeps its own range, so particles still vary.
				bool wasRange = !slot.Multi && cols.Count == 2 && (cols[0][0] != cols[1][0] || cols[0][1] != cols[1][1] || cols[0][2] != cols[1][2]);
				if (wasRange)
				{
					var mid = new[] { (cols[0][0] + cols[1][0]) / 2, (cols[0][1] + cols[1][1]) / 2, (cols[0][2] + cols[1][2]) / 2 };
					cols = new List<int[]> { mid, (int[])mid.Clone() };
					collapsed++;
				}
				bool any = wasRange;
				var outCols = new List<int[]>();
				foreach (var rgb in cols)
				{
					double h, s, v; RgbToHsv(rgb[0], rgb[1], rgb[2], out h, out s, out v);
					if (s <= 0.0001) { outCols.Add(rgb); continue; }
					any = true;
					outCols.Add(HsvToRgb(hueDegrees, Math.Max(0, Math.Min(1, s * saturation)), v));
				}
				if (any) { slot.Write(outCols); changed++; } else grey++;
			}
			return Tuple.Create(changed, grey, found.Item2);
		}

		static void RgbToHsv(int r8, int g8, int b8, out double h, out double s, out double v)
		{
			double r = r8 / 255.0, g = g8 / 255.0, b = b8 / 255.0;
			double max = Math.Max(r, Math.Max(g, b)), min = Math.Min(r, Math.Min(g, b)), d = max - min;
			v = max; s = max <= 0 ? 0 : d / max;
			if (d <= 0) { h = 0; return; }
			if (max == r) h = 60 * (((g - b) / d) % 6);
			else if (max == g) h = 60 * ((b - r) / d + 2);
			else h = 60 * ((r - g) / d + 4);
			if (h < 0) h += 360;
		}

		static int[] HsvToRgb(double h, double s, double v)
		{
			double c = v * s, x = c * (1 - Math.Abs((h / 60) % 2 - 1)), m = v - c;
			double r, g, b;
			if (h < 60) { r = c; g = x; b = 0; } else if (h < 120) { r = x; g = c; b = 0; }
			else if (h < 180) { r = 0; g = c; b = x; } else if (h < 240) { r = 0; g = x; b = c; }
			else if (h < 300) { r = x; g = 0; b = c; } else { r = c; g = 0; b = x; }
			Func<double, int> q = u => (int)Math.Round(Math.Max(0, Math.Min(255, (u + m) * 255)));
			return new[] { q(r), q(g), q(b) };
		}

		static double HueDistance(double a, double b)
		{
			double d = Math.Abs(a - b) % 360;
			return d > 180 ? 360 - d : d;
		}

		// ------------------------------------------------------------ compare

		/// <summary>The value's numbers in the same order a request states them.</summary>
		static float[] Numbers(Leaf leaf)
		{
			if (leaf.Owner is Effekseer.Data.Value.FloatWithRandom fo)
				return new[] { leaf.Component == "Center" ? fo.GetCenter() : leaf.Component == "Max" ? fo.GetMax() : fo.GetMin() };
			if (leaf.Owner is Effekseer.Data.Value.IntWithRandom io)
				return new[] { (float)(leaf.Component == "Center" ? io.GetCenter() : leaf.Component == "Max" ? io.GetMax() : io.GetMin()) };
			switch (leaf.Value)
			{
				case Effekseer.Data.Value.Float f: return new[] { f.Value };
				case Effekseer.Data.Value.Int i: return new[] { (float)i.Value };
				case Effekseer.Data.Value.IntWithInifinite ii: return new[] { (float)ii.Value.Value };
				case Effekseer.Data.Value.FloatWithRandom fr: return new[] { fr.GetMin(), fr.GetMax() };
				case Effekseer.Data.Value.IntWithRandom ir: return new[] { (float)ir.GetMin(), ir.GetMax() };
				case Effekseer.Data.Value.Vector3D v: return new[] { v.X.Value, v.Y.Value, v.Z.Value };
				case Effekseer.Data.Value.Vector4D v4: return new[] { v4.X.Value, v4.Y.Value, v4.Z.Value, v4.W.Value };
				case Effekseer.Data.Value.Vector2D v2: return new[] { v2.X.Value, v2.Y.Value };
				case Effekseer.Data.Value.Color c: return new float[] { c.R.Value, c.G.Value, c.B.Value, c.A.Value };
				case Effekseer.Data.Value.ColorWithRandom cr:
					return new float[] { cr.R.GetMax(), cr.G.GetMax(), cr.B.GetMax(), cr.A.GetMax() };
			}
			return null;
		}

		static bool Matches(Leaf leaf, JsonElement wanted, out string why)
		{
			why = null;
			if (leaf.Value is Effekseer.Data.Value.ColorWithRandom cr && wanted.ValueKind == JsonValueKind.Object)
			{
				if (!TryColourRange(wanted, out var lo, out var hi, out why)) return false;
				int[] actualMin = { cr.R.GetMin(), cr.G.GetMin(), cr.B.GetMin(), cr.A.GetMin() };
				int[] actualMax = { cr.R.GetMax(), cr.G.GetMax(), cr.B.GetMax(), cr.A.GetMax() };
				if (!actualMin.SequenceEqual(lo) || !actualMax.SequenceEqual(hi))
				{ why = "saved RGBA range endpoints differ"; return false; }
				return true;
			}
			// A path is copied beside the output, so only its file name survives.
			if (leaf.Value is Effekseer.Data.Value.Path pv && wanted.ValueKind == JsonValueKind.String)
			{
				string saved = Path.GetFileName(pv.AbsolutePath ?? ""), asked = Path.GetFileName(wanted.GetString());
				if (string.Equals(saved, asked, StringComparison.OrdinalIgnoreCase)) return true;
				// The resource copier never overwrites: a DIFFERENT file with the same
				// name already beside the output gets this one saved under a hash suffix.
				// Usually that is a stale copy from an earlier build of a regenerated texture.
				if (System.Text.RegularExpressions.Regex.IsMatch(saved,
						"^" + System.Text.RegularExpressions.Regex.Escape(Path.GetFileNameWithoutExtension(asked)) + "_[0-9A-Fa-f]{6}" +
						System.Text.RegularExpressions.Regex.Escape(Path.GetExtension(asked)) + "$", System.Text.RegularExpressions.RegexOptions.IgnoreCase))
					why = "a different " + asked + " was already beside the output, so this one was saved as " + saved +
						  "; if that copy is stale (an earlier build of a regenerated texture), delete it and rebuild, or give the new texture its own name";
				return false;
			}
			if (leaf.Value is Effekseer.Data.Value.String sv && wanted.ValueKind == JsonValueKind.String)
				return sv.Value == wanted.GetString();
			if (leaf.Value is Effekseer.Data.Value.IntWithInifinite iw)
			{
				bool askInf = wanted.ValueKind == JsonValueKind.String && wanted.GetString() == "inf";
				if (askInf || iw.Infinite.Value)
				{
					if (askInf != iw.Infinite.Value) why = askInf ? "saved as a finite count" : "saved as infinite";
					return askInf == iw.Infinite.Value;
				}
			}
			if (leaf.Value is ProceduralModelReference pr)
			{
				if (wanted.ValueKind == JsonValueKind.String) return pr.Value != null && pr.Value.Name.Value == wanted.GetString();
				return TryInt(wanted, out int wi) && pr.Index == wi;
			}
			var have = Numbers(leaf);
			if (have == null) return true; // booleans and enums: read-back equality is the check
			float[] want;
			if (wanted.ValueKind == JsonValueKind.Number)
				want = have.Length == 2 ? new[] { wanted.GetSingle(), wanted.GetSingle() } : new[] { wanted.GetSingle() };
			else if (wanted.ValueKind == JsonValueKind.Array)
				want = wanted.EnumerateArray().Select(e => e.GetSingle()).ToArray();
			else return true;

			// Two numbers are a [lo, hi] random range only for WithRandom values;
			// a Vector2D (e.g. a procedural model's spline point) keeps its order.
			bool isRange = leaf.Value is Effekseer.Data.Value.FloatWithRandom || leaf.Value is Effekseer.Data.Value.IntWithRandom;
			if (isRange && have.Length == 2 && want.Length == 2) want = new[] { Math.Min(want[0], want[1]), Math.Max(want[0], want[1]) };
			for (int i = 0; i < want.Length; i++)
			{
				if (i >= have.Length) { why = "too many components"; return false; }
				if (Math.Abs(have[i] - want[i]) > 1e-4f * Math.Max(1f, Math.Abs(want[i]))) return false;
			}
			// [r, g, b] leaves alpha alone, so a colour whose alpha fell to zero
			// is a defect even though no component asked for it.
			if (want.Length == 3 && have.Length == 4 && have[3] == 0) { why = "alpha is 0"; return false; }
			return true;
		}

		// Max first, then Min: SetMax drags Min down if it would cross, SetMin
		// then lands exactly, and both recompute the centre.
		static void SetRange(Effekseer.Data.Value.FloatWithRandom r, float lo, float hi)
		{
			r.SetMax(hi); r.SetMin(lo); r.SetMax(hi);
		}

		static void SetRange(Effekseer.Data.Value.IntWithRandom r, int lo, int hi)
		{
			r.SetMax(hi); r.SetMin(lo); r.SetMax(hi);
		}
	}
}
