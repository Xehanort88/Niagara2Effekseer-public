using System;
using System.Collections.Generic;
using System.Linq;
using Effekseer;
using Effekseer.Data;

namespace Niagara2Efk
{
	/// <summary>
	/// The counts the runtime reads with a cap. EffectImplemented::LoadBody (Effekseer.Effect.cpp,
	/// 1.70e and 1.80.7 alike) reads each resource count, the dynamic input and equation counts, the
	/// rendering-node count and every resource path length with Read(value, 0, elementCountMax = 1024);
	/// a value above 1024 fails the read and LoadEffect returns null: in the browser runtime neither
	/// onload nor onerror fires, so the game just never plays it. Our `edit` VERIFY reloads through the
	/// editor core, which has no such cap, so nothing else notices.
	///
	/// Counts are taken from the effect loaded in Core, the way Binary/Exporter.cs counts them when it
	/// writes the file: the resource sets from the exporter itself, rendering nodes as its get_nodes
	/// does (every node whose subtree draws: IsRenderedNodeGroup).
	/// </summary>
	static class ElementLimits
	{
		public const int Max = 1024;

		public class Count
		{
			public string Name { get; set; }
			public int Value { get; set; }
			public int Limit { get; set; } = Max;
			public bool Over => Value > Limit;
		}

		public class Result
		{
			public List<Count> Counts { get; set; } = new List<Count>();
			/// <summary>Set when the exporter could not run, so the resource counts are missing
			/// (the rendering-node and dynamic counts are still there).</summary>
			public string Error { get; set; }
		}

		/// <summary>Counts for the effect currently loaded in Core.</summary>
		public static Result Measure()
		{
			var result = new Result();
			result.Counts.Add(new Count { Name = "rendering nodes", Value = RenderingNodes(Core.Root) });
			try
			{
				var exporter = new Effekseer.Binary.Exporter();
				exporter.Export(Core.Root);
				void Add(string name, ICollection<string> paths)
				{
					result.Counts.Add(new Count { Name = name, Value = paths.Count });
					// The file stores each path's UTF-16 length plus its terminator.
					if (paths.Count > 0)
						result.Counts.Add(new Count { Name = "longest " + name.TrimEnd('s') + " path (characters + 1)", Value = paths.Max(p => p.Length) + 1 });
				}
				Add("images", exporter.UsedTextures);
				Add("normal images", exporter.UsedNormalTextures);
				Add("distortion images", exporter.UsedDistortionTextures);
				Add("sounds", exporter.Sounds);
				Add("models", exporter.Models);
				Add("materials", exporter.Materials);
				Add("curves", exporter.Curves);
				result.Counts.Add(new Count { Name = "procedural models", Value = exporter.ProceduralModels.Count });
			}
			catch (Exception e)
			{
				while (e.InnerException != null) e = e.InnerException;
				result.Error = "exporter: " + e.GetType().Name + ": " + e.Message;
			}
			result.Counts.Add(new Count { Name = "dynamic inputs", Value = Core.Dynamic.Inputs.Values.Count });
			result.Counts.Add(new Count { Name = "dynamic equations", Value = Core.Dynamic.Equations.Values.Count });
			return result;
		}

		/// <summary>Exporter.cs get_nodes: every Data.Node reached through children that are
		/// IsRenderedNodeGroup (the node or a descendant draws, plays a sound or, in 1.8, has GPU
		/// particles).</summary>
		public static int RenderingNodes(NodeBase parent)
		{
			int n = 0;
			foreach (var child in parent.Children.Internal)
				if (child is Node node && IsRenderedNodeGroup(node)) n += 1 + RenderingNodes(node);
			return n;
		}

		static bool IsRenderedNode(Node node)
		{
			bool rendered = node.IsRendered.Value && node.DrawingValues.Type.Value != RendererValues.ParamaterType.None;
			bool sound = node.SoundValues.Type.GetValue() == SoundValues.ParamaterType.Use;
#if EFK18
			bool gpu = node.GpuParticles.Enabled.Value;
#else
			bool gpu = false;
#endif
			return rendered || sound || gpu;
		}

		static bool IsRenderedNodeGroup(Node node)
		{
			if (IsRenderedNode(node)) return true;
			foreach (var child in node.Children.Internal)
				if (child is Node n && IsRenderedNodeGroup(n)) return true;
			return false;
		}

		/// <summary>RPGReactor's battle camera (js/reactor_sprites.js Sprite_Animation: view z -10, projection
		/// w = 1 - 1.2 z) clips everything nearer than 10.4545 units from the effect along the view axis.
		/// DepthOffset moves a particle that far toward the camera (EffekseerRendererCommon
		/// ApplyDepthParameters, no clamp), so from this offset on a particle at the effect is never drawn:
		/// Geodesic Shield's 11.25 glow (measured: draws at 10.3, gone at 10.6,
		/// out/edits/depthoffset-audit-20261019).</summary>
		public const float NearPlane = 10.4545f;

		public class DepthNode
		{
			public string Path { get; set; }
			public string Name { get; set; }
			public float DepthOffset { get; set; }
		}

		/// <summary>Every drawn node (dump paths) whose DepthOffset reaches RPGReactor's near plane.
		/// A node placed past the camera can still draw because of it (OT0 Fire's Flash_Camera at Z 60),
		/// so this is a warning, not a cap.</summary>
		public static List<DepthNode> FarDepthOffsets(NodeBase parent, string path = "")
		{
			var found = new List<DepthNode>();
			for (int i = 0; i < parent.Children.Count; i++)
			{
				string p = path.Length == 0 ? i.ToString(System.Globalization.CultureInfo.InvariantCulture) : path + "/" + i;
				var child = parent.Children[i];
				if (child is Node node && node.IsRendered.Value && node.DrawingValues.Type.Value != RendererValues.ParamaterType.None
					&& node.DepthValues.DepthOffset.Value >= NearPlane)
					found.Add(new DepthNode { Path = p, Name = node.Name.Value, DepthOffset = node.DepthValues.DepthOffset.Value });
				found.AddRange(FarDepthOffsets(child, p));
			}
			return found;
		}

		/// <summary>One line per count over the cap.</summary>
		public static IEnumerable<string> Problems(IEnumerable<Count> counts) =>
			counts.Where(c => c.Over).Select(c => string.Format("{0} {1} > {2}", c.Name, c.Value, c.Limit));
	}
}
