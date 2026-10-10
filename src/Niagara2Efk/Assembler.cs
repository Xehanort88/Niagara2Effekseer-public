using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Effekseer;
using Effekseer.Data;
#if EFK18
using EfkUtl = Effekseer.Utils;   // 1.8 renamed Effekseer.Utl
#else
using EfkUtl = Effekseer.Utl;
#endif

namespace Niagara2Efk
{
	/// <summary>
	/// Effect assembler: build a new .efkefc by grafting node subtrees out of
	/// existing effects (see ASSEMBLER-BRIEF.md).
	///
	/// The whole design turns on one fact about EffekseerCore's clipboard:
	/// Core.Copy/Core.Paste serialize with isClip:true, and Data/IO.cs writes
	/// Value.Path as an ABSOLUTE path in that mode (IO.cs SaveToElement/Path)
	/// and reads it back absolute (IO.cs LoadFromElement/Path). So a pasted
	/// subtree still points at the source effect's own texture files on disk.
	/// That is what makes an exact rebuild possible: after pasting we walk the
	/// destination subtree, copy each referenced file next to the output, and
	/// repoint the path. Nothing is inferred or reconstructed.
	///
	/// Two things do NOT survive a paste and must be repaired explicitly:
	///
	///  1. Procedural models and dynamic equations are stored as a bare integer
	///     index into a project-level collection (IO.cs SaveToElement/
	///     ObjectReference writes de.Index; LoadFromElement calls
	///     SetValueWithIndex). Pasting into a fresh project whose collections
	///     are empty makes SetValueWithIndex return null and the reference
	///     silently dangles. We therefore record each reference's index on the
	///     SOURCE side, carry the referenced definition across, and re-point the
	///     reference at its new index. 42 effects in the corpus reference a
	///     procedural model and 11 use dynamic equations, so this is not
	///     hypothetical.
	///
	///  2. A File-material node's texture slots are rebuilt from the .efkmat
	///     whenever its material path changes (MaterialFileParameter's
	///     Path_OnChanged calls ApplyMaterial). Rewriting the material path
	///     therefore resets the slot assignments, so we capture them first and
	///     restore them afterwards.
	///
	/// Source effects are only ever read.
	/// </summary>
	static partial class Assembler
	{
		// ---------------------------------------------------------------- spec

		class Spec
		{
			public string name { get; set; }
			public string catalogue { get; set; }
			public bool allowMissingResources { get; set; }
			public bool keepDefaultProjectSettings { get; set; }
			public List<PartSpec> parts { get; set; }
		}

		class PartSpec
		{
			public string source { get; set; }   // .efkefc path, or catalogue effect id
			public string node { get; set; }     // tree path, e.g. "1/0"; omit for whole effect
			public string name { get; set; }     // rename the grafted root

			/// <summary>
			/// What to do with the part's ancestor chain, which in the source
			/// supplies its position, rotation, scale and spawn timing.
			///   "controllers" (default) rebuild the chain with the ancestors'
			///                 own visuals switched off, so the part keeps the
			///                 motion it had,
			///   "full"        rebuild the chain and keep the ancestors visible,
			///   "none"        lift the node alone, losing whatever its ancestors
			///                 contributed.
			/// </summary>
			public string ancestors { get; set; }

			public float[] offset { get; set; }  // x,y,z in effect units
			public float? scale { get; set; }    // uniform multiplier
			public int? delay { get; set; }      // frames before this part starts
			public float? lifeScale { get; set; } // multiply the grafted root's life
		}

		// ------------------------------------------------------------- harvest

		/// <summary>One reference found in a part, recorded on the source side.</summary>
		class RefRecord
		{
			public string KeyPath;      // stable address of the reference within the part
			public string Kind;         // "ProceduralModel" | "DynamicEquation"
			public int SourceIndex;
			public string DefinitionXml;
		}

		class Harvest
		{
			public PartSpec Part;
			public string SourceFile;
			public string NodePath;
			public string Xml;
			public int NodeCount;
			public string OriginalName;
			public string InheritLocation, InheritRotation, InheritScale;
			public List<RefRecord> Refs = new List<RefRecord>();
			public string DynamicInputsXml;   // only when a dynamic equation is used
			public ProjectSettings Settings;
			public List<AncestorRecord> Ancestors = new List<AncestorRecord>();
			public string AncestorMode = "controllers";
		}

		/// <summary>
		/// One link of the part's ancestor chain, serialized without its own
		/// children so it can be rebuilt as a bare carrier for the part.
		///
		/// This exists because a part's motion is a property of its whole
		/// ancestor chain, not of the node itself, and not only of ancestors
		/// whose renderer happens to be None. A node four levels down under a
		/// "movement controller" and a "rotation controller" that both draw
		/// sprites still loses all of its movement when lifted alone.
		/// </summary>
		class AncestorRecord
		{
			public string Xml;
			public string Name;
			public string Renderer;
			public List<RefRecord> Refs = new List<RefRecord>();
		}

		/// <summary>
		/// Project-level state that lives outside the node tree. Binary/Exporter.cs
		/// writes Global.RandomSeed, Culling and LodValues straight into the
		/// runtime chunk, so a part taken from an effect with sphere culling
		/// behaves differently under Core.New()'s defaults. Behavior and the
		/// preview frame range are editor-only but cost nothing to carry.
		/// </summary>
		class ProjectSettings
		{
			public string CullingXml, LodXml, GlobalXml, BehaviorXml;
			public int StartFrame, EndFrame;
			public bool IsLoop;

			public static ProjectSettings Capture()
			{
				return new ProjectSettings
				{
					CullingXml = Core.Copy("Culling", Core.Culling),
					LodXml = Core.Copy("LOD", Core.LodValues),
					GlobalXml = Core.Copy("Global", Core.Global),
					BehaviorXml = Core.Copy("Behavior", Core.EffectBehavior),
					StartFrame = Core.StartFrame,
					EndFrame = Core.EndFrame,
					IsLoop = Core.IsLoop,
				};
			}

			public void Apply()
			{
				if (CullingXml != null) Core.Paste("Culling", Core.Culling, CullingXml);
				if (LodXml != null) Core.Paste("LOD", Core.LodValues, LodXml);
				if (GlobalXml != null) Core.Paste("Global", Core.Global, GlobalXml);
				if (BehaviorXml != null) Core.Paste("Behavior", Core.EffectBehavior, BehaviorXml);
				Core.StartFrame = StartFrame;
				Core.EndFrame = EndFrame;
				Core.IsLoop = IsLoop;
			}

			/// <summary>Only the parts the runtime actually reads.</summary>
			public bool RuntimeEquals(ProjectSettings o)
			{
				return o != null
					&& CullingXml == o.CullingXml
					&& LodXml == o.LodXml
					&& GlobalXml == o.GlobalXml;
			}
		}

		// ----------------------------------------------------------------- run

		/// <summary>
		/// Graft one node subtree from one source effect, with no overrides. This
		/// is the shape the self-test uses: with node "0" of a single-root effect
		/// it should reproduce that effect exactly.
		/// </summary>
		public static int AssembleSingleNode(string sourceFile, string nodePath, string outPath)
		{
			var spec = new Spec
			{
				parts = new List<PartSpec> { new PartSpec { source = sourceFile, node = nodePath } }
			};
			return Run(spec, Path.GetDirectoryName(Path.GetFullPath(outPath)), outPath, quiet: true);
		}


		/// <summary>
		/// Assemble every part named in a selection file, one standalone effect
		/// each, in a single process.
		///
		/// The selection comes from tools/parts/enumerate.py, which collapses the
		/// corpus's drawing nodes down to distinct parts. This exists so a
		/// contact sheet can be rendered: 3,000-odd separate `assemble` processes
		/// would spend most of their time starting the runtime, and the self-test
		/// already proves that many grafts in one process stay byte-exact.
		///
		/// Every part is lifted with its ancestor chain rebuilt as non-drawing
		/// carriers, which is the only setting that works for Track and Ribbon:
		/// not one Track node in the corpus stands on its own.
		/// </summary>
		public static int RunBatch(string selectionPath, string outDir)
		{
			JsonDocument sel;
			try { sel = JsonDocument.Parse(File.ReadAllText(selectionPath)); }
			catch (Exception e)
			{
				Console.Error.WriteLine("could not read selection: " + e.Message);
				return 2;
			}

			string cataloguePath = null;
			if (sel.RootElement.TryGetProperty("catalogue", out var cat))
				cataloguePath = Path.GetFullPath(cat.GetString());

			var parts = sel.RootElement.GetProperty("parts").EnumerateArray().ToList();
			Directory.CreateDirectory(outDir);
			Console.WriteLine("Batch-assembling {0} part(s) into {1}", parts.Count, outDir);

			var results = new List<Dictionary<string, object>>();
			int ok = 0, failed = 0;
			var sw = System.Diagnostics.Stopwatch.StartNew();

			foreach (var p in parts)
			{
				string id = p.GetProperty("id").GetString();
				string source = p.GetProperty("source").GetString();
				string node = p.GetProperty("node").GetString();

				string caseDir = Path.Combine(outDir, id);
				string efk = Path.Combine(caseDir, id + ".efkefc");

				var spec = new Spec
				{
					catalogue = cataloguePath,
					parts = new List<PartSpec>
					{
						new PartSpec { source = source, node = node, ancestors = "controllers" }
					}
				};

				var row = new Dictionary<string, object> { ["id"] = id };
				foreach (var f in new[] { "renderer", "source", "node", "name", "corpus", "copies", "material", "chain", "frames", "fingerprint" })
					if (p.TryGetProperty(f, out var v))
						row[f] = v.ValueKind == JsonValueKind.Number ? (object)v.GetInt32() : v.ToString();

				try
				{
					if (Directory.Exists(caseDir)) Directory.Delete(caseDir, true);
					int rc = Run(spec, outDir, efk, quiet: true);
					if (rc == 0 && File.Exists(efk))
					{
						ok++;
						row["efkefc"] = (id + "/" + id + ".efkefc");
						row["bytes"] = (int)new FileInfo(efk).Length;
					}
					else
					{
						failed++;
						row["error"] = "assemble returned " + rc;
					}
				}
				catch (Exception e)
				{
					failed++;
					row["error"] = e.GetType().Name + ": " + e.Message;
				}

				results.Add(row);
				if (results.Count % 250 == 0)
					Console.WriteLine("  {0}/{1}  ({2:0.0}s elapsed, {3} failed)",
						results.Count, parts.Count, sw.Elapsed.TotalSeconds, failed);
			}

			var manifest = new Dictionary<string, object>
			{
				["generated"] = DateTime.UtcNow.ToString("o"),
				["selection"] = Path.GetFullPath(selectionPath).Replace(Path.DirectorySeparatorChar, '/'),
				["assembled"] = ok,
				["failed"] = failed,
				["parts"] = results,
			};
			File.WriteAllText(Path.Combine(outDir, "manifest.json"),
				JsonSerializer.Serialize(manifest, new JsonSerializerOptions { WriteIndented = true }),
				new UTF8Encoding(false));

			Console.WriteLine();
			Console.WriteLine("  assembled  {0}", ok);
			Console.WriteLine("  failed     {0}", failed);
			Console.WriteLine("  elapsed    {0:0.0}s", sw.Elapsed.TotalSeconds);
			foreach (var r in results.Where(r => r.ContainsKey("error")).Take(20))
				Console.WriteLine("    {0}  {1}", r["id"], r["error"]);
			return failed == 0 ? 0 : 1;
		}

		public static int Run(string specPath, string outPath)
		{
			Spec spec;
			try
			{
				spec = JsonSerializer.Deserialize<Spec>(File.ReadAllText(specPath),
					new JsonSerializerOptions { PropertyNameCaseInsensitive = true, ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
			}
			catch (Exception e)
			{
				Console.Error.WriteLine("could not read spec: " + e.Message);
				return 2;
			}

			if (spec == null || spec.parts == null || spec.parts.Count == 0)
			{
				Console.Error.WriteLine("spec has no parts");
				return 2;
			}

			return Run(spec, Path.GetDirectoryName(Path.GetFullPath(specPath)), outPath, quiet: false);
		}

		static int Run(Spec spec, string specDir, string outPath, bool quiet)
		{
			var resolver = new SourceResolver(spec.catalogue, specDir, quiet);

			// ---- phase 1: harvest each part from its own source project -------
			var harvests = new List<Harvest>();
			foreach (var part in spec.parts)
			{
				var h = HarvestPart(part, resolver);
				if (h == null) return 1;
				harvests.Add(h);
			}

			// ---- phase 2: build the destination project -----------------------
			string outDir = Path.GetDirectoryName(Path.GetFullPath(outPath));
			Directory.CreateDirectory(outDir);
			var copier = new ResourceCopier(outDir);

			Core.New();
			Core.Root.ClearChildren();
			Core.Root.Name.SetValue(string.IsNullOrEmpty(spec.name) ? "Root" : spec.name);

			var defs = new DefinitionTable();
			var report = new List<string>();
			int grafted = 0;

			// Project settings come from the first part's source, because they are
			// the ones its nodes were authored against. Parts from a source with
			// different runtime-visible settings are called out rather than
			// silently adopting someone else's culling volume.
			if (!spec.keepDefaultProjectSettings)
			{
				harvests[0].Settings.Apply();
				for (int i = 1; i < harvests.Count; i++)
				{
					if (!harvests[0].Settings.RuntimeEquals(harvests[i].Settings))
						report.Add(string.Format(
							"NOTE: '{0}' came from an effect with different culling/LOD/seed settings; the first part's were used",
							harvests[i].OriginalName));
				}
			}

			foreach (var h in harvests)
			{
				Node target = Core.Root.AddChild();
				Node wrapper = null;

				bool needsWrapper = (h.Part.offset != null && h.Part.offset.Length == 3
										&& (h.Part.offset[0] != 0 || h.Part.offset[1] != 0 || h.Part.offset[2] != 0))
									|| (h.Part.scale.HasValue && Math.Abs(h.Part.scale.Value - 1.0f) > 1e-6f);

				if (needsWrapper)
				{
					wrapper = target;
					ConfigureWrapper(wrapper, h);
					target = wrapper.AddChild();
				}

				ApplyDynamicInputs(h, defs, report);

				// Rebuild the ancestor chain first, outermost link into the slot.
				// Each link is fixed up immediately, while it still has no
				// children, so its own values are the only thing in scope.
				Node cursor = target;
				bool firstLink = true;
				foreach (var anc in h.Ancestors)
				{
					Node link = firstLink ? cursor : cursor.AddChild();
					firstLink = false;

					Core.Paste(link, anc.Xml);
					if (h.AncestorMode != "full")
						link.DrawingValues.Type.SetValue(RendererValues.ParamaterType.None);

					RewriteResources(link, copier, spec.allowMissingResources, report);
					ApplyRefs(link, anc.Refs, defs, report, ownValuesOnly: true);
					cursor = link;
				}

				Node partNode = h.Ancestors.Count == 0 ? cursor : cursor.AddChild();
				Core.Paste(partNode, h.Xml);

				// Everything below operates on the pasted subtree only.
				RewriteResources(partNode, copier, spec.allowMissingResources, report);
				ApplyRefs(partNode, h.Refs, defs, report, ownValuesOnly: false);
				ApplyOverrides(partNode, h, wrapper != null && h.Ancestors.Count == 0, report);
				ApplyDelay(target, h, report);

				if (h.Ancestors.Count > 0)
					report.Add(string.Format("'{0}' carried {1} ancestor link(s) [{2}] so it keeps its motion",
						h.Part.name ?? h.OriginalName, h.Ancestors.Count,
						string.Join(" > ", h.Ancestors.Select(a => a.Name))));

				grafted++;
			}

			if (copier.Errors.Count > 0)
			{
				foreach (var e in copier.Errors) Console.Error.WriteLine("ERROR: " + e);
				Console.Error.WriteLine("Refusing to write an effect with unresolved resources. "
					+ "Set \"allowMissingResources\": true in the spec to drop them instead.");
				return 1;
			}

			Core.SaveTo(outPath);

			if (!quiet)
			{
				Console.WriteLine("Assembled {0} part(s) -> {1}", grafted, Path.GetFullPath(outPath));
				foreach (var line in copier.Summary()) Console.WriteLine("  " + line);
				foreach (var line in report) Console.WriteLine("  " + line);
			}

			// ---- phase 3: verify ----------------------------------------------
			return Verify(outPath, harvests, report, copier.LiveCopied, quiet) ? 0 : 1;
		}

		// ------------------------------------------------------------- harvest

		static Harvest HarvestPart(PartSpec part, SourceResolver resolver)
		{
			string file = resolver.Resolve(part.source);
			if (file == null)
			{
				Console.Error.WriteLine("part source not found: " + part.source);
				return null;
			}

			Core.New();
			if (!Core.LoadFrom(file))
			{
				Console.Error.WriteLine("could not load part source: " + file);
				return null;
			}

			Node node = Navigate(part.node);
			if (node == null)
			{
				Console.Error.WriteLine("node path '{0}' not found in {1}", part.node, file);
				return null;
			}

			var h = new Harvest
			{
				Part = part,
				SourceFile = file,
				NodePath = part.node ?? "0",
				Xml = Core.Copy(node),
				NodeCount = CountSubtree(node),
				OriginalName = node.Name.Value,
				InheritLocation = node.CommonValues.LocationEffectType.Value.ToString(),
				InheritRotation = node.CommonValues.RotationEffectType.Value.ToString(),
				InheritScale = node.CommonValues.ScaleEffectType.Value.ToString(),
			};

			if (string.IsNullOrEmpty(h.Xml))
			{
				Console.Error.WriteLine("Core.Copy produced nothing for " + file);
				return null;
			}

			// Record every project-level reference the part makes, while the
			// source project is still the loaded one.
			bool usesDynamic = false;
			h.Refs.AddRange(HarvestRefs(node, ref usesDynamic));

			// The ancestor chain, outermost first. Each link is stored without
			// its children so rebuilding it cannot drag in the part's siblings.
			h.AncestorMode = (part.ancestors ?? "controllers").ToLowerInvariant();
			if (h.AncestorMode != "none")
			{
				var chain = new List<Node>();
				for (var p = node.Parent as Node; p != null; p = p.Parent as Node) chain.Add(p);
				chain.Reverse();

				foreach (var anc in chain)
				{
					var rec = new AncestorRecord
					{
						Xml = StripChildren(Core.Copy(anc)),
						Name = anc.Name.Value,
						Renderer = anc.DrawingValues.Type.Value.ToString(),
					};
					rec.Refs.AddRange(HarvestRefs(anc, ref usesDynamic, ownValuesOnly: true));
					h.Ancestors.Add(rec);
				}
			}

			// Dynamic equations read @In0..@In3 from the project-level inputs.
			if (usesDynamic) h.DynamicInputsXml = Core.Copy("Inputs", Core.Dynamic.Inputs);

			h.Settings = ProjectSettings.Capture();
			return h;
		}

		/// <summary>True if the subtree references a procedural model or dynamic equation (project indices a plain paste cannot carry).</summary>
		internal static bool UsesProjectRefs(Node node, ref bool usesDynamic) => HarvestRefs(node, ref usesDynamic).Count > 0;

		static List<RefRecord> HarvestRefs(Node node, ref bool usesDynamic, bool ownValuesOnly = false)
		{
			var found = new List<RefRecord>();
			bool dyn = false;

			Action<string, object> collect = (keyPath, value) =>
			{
				var kind = ReferenceKind(value);
				if (kind == null) return;

				int idx = ReferenceIndex(value);
				if (idx < 0) return;

				string defXml = null;
				if (kind == "ProceduralModel")
				{
					var list = Core.ProceduralModel.ProceduralModels.Values;
					if (idx < list.Count) defXml = Core.Copy("ProceduralModel", list[idx]);
				}
				else
				{
					var list = Core.Dynamic.Equations.Values;
					if (idx < list.Count) defXml = Core.Copy("DynamicEquation", list[idx]);
					dyn = true;
				}

				if (defXml == null) return;
				found.Add(new RefRecord { KeyPath = keyPath, Kind = kind, SourceIndex = idx, DefinitionXml = defXml });
			};

			if (ownValuesOnly) WalkOwnValues(node, collect);
			else WalkSubtree(node, collect);

			usesDynamic = usesDynamic || dyn;
			return found;
		}

		/// <summary>
		/// Remove the &lt;Children&gt; element from a clipboard payload so pasting
		/// it reproduces one node and nothing beneath it.
		/// </summary>
		static string StripChildren(string xml)
		{
			var doc = new System.Xml.XmlDocument();
			doc.LoadXml(xml);
			var root = doc.DocumentElement;
			if (root == null) return xml;

			var children = root["Children"];
			if (children != null) root.RemoveChild(children);
			return doc.InnerXml;
		}

		static Node Navigate(string path)
		{
			NodeBase cur = Core.Root;
			if (string.IsNullOrWhiteSpace(path)) path = "0";

			foreach (var seg in path.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries))
			{
				int i;
				if (!int.TryParse(seg, NumberStyles.Integer, CultureInfo.InvariantCulture, out i)) return null;
				if (i < 0 || i >= cur.Children.Count) return null;
				cur = cur.Children[i];
			}
			return cur as Node;
		}

		static int CountSubtree(NodeBase n)
		{
			int c = 1;
			foreach (var ch in n.Children.Internal) c += CountSubtree(ch);
			return c;
		}

		// --------------------------------------------------------- object walk

		/// <summary>
		/// Visit every value object inside a node subtree, keyed by a stable
		/// address (node tree path + property path). Value.Path,
		/// MaterialFileParameter and ObjectReference are reported as leaves;
		/// everything else in the Effekseer.Data graph is descended into.
		///
		/// This deliberately descends further than Data/IO.cs does. IO stops at
		/// any type with its own SaveToElement overload, which would hide the
		/// DynamicEquationReference nested inside Value.Float, FloatWithRandom,
		/// IntWithRandom, IntWithInifinite, Vector3D, Vector3DWithRandom and
		/// Vector4D.
		/// </summary>
		static void WalkSubtree(Node root, Action<string, object> visit)
		{
			WalkSubtree(root, "", visit);
		}

		/// <summary>The node's own parameters, ignoring anything beneath it.</summary>
		static void WalkOwnValues(Node node, Action<string, object> visit)
		{
			var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
			foreach (var prop in typeof(Node).GetProperties(BindingFlags.Public | BindingFlags.Instance))
			{
				if (prop.Name == "Children" || prop.Name == "Parent") continue;
				var io = prop.GetCustomAttributes(typeof(IOAttribute), false).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export) continue;

				object v;
				try { v = prop.GetValue(node, null); } catch { continue; }
				WalkValue(v, ":" + prop.Name, visit, seen, 0);
			}
		}

		static void WalkSubtree(Node node, string nodePath, Action<string, object> visit)
		{
			var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
			foreach (var prop in typeof(Node).GetProperties(BindingFlags.Public | BindingFlags.Instance))
			{
				if (prop.Name == "Children" || prop.Name == "Parent") continue;
				var io = prop.GetCustomAttributes(typeof(IOAttribute), false).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export) continue;

				object v;
				try { v = prop.GetValue(node, null); } catch { continue; }
				WalkValue(v, nodePath + ":" + prop.Name, visit, seen, 0);
			}

			int i = 0;
			foreach (var child in node.Children.Internal)
			{
				WalkSubtree(child, nodePath + "/" + i, visit);
				i++;
			}
		}

		static void WalkValue(object o, string keyPath, Action<string, object> visit, HashSet<object> seen, int depth)
		{
			if (o == null || depth > 24) return;

			var t = o.GetType();
			if (t.Namespace == null || !t.Namespace.StartsWith("Effekseer")) return;
			if (o is NodeBase) return;
			if (o is NodeBase.ChildrenCollection) return;
			if (!seen.Add(o)) return;

			if (o is Effekseer.Data.Value.Path) { visit(keyPath, o); return; }
			if (o is MaterialFileParameter) { visit(keyPath, o); return; }
			if (ReferenceKind(o) != null) { visit(keyPath, o); return; }

			// Curve keyframes and gradients hold no paths or references.
			if (t.Name.StartsWith("FCurve") || t.Name == "Gradient" || t.Name == "GradientKey") return;

			foreach (var prop in t.GetProperties(BindingFlags.Public | BindingFlags.Instance))
			{
				if (prop.GetIndexParameters().Length > 0) continue;
				var io = prop.GetCustomAttributes(typeof(IOAttribute), false).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export) continue;

				object v;
				try { v = prop.GetValue(o, null); } catch { continue; }
				WalkValue(v, keyPath + "." + prop.Name, visit, seen, depth + 1);
			}
		}

		static string ReferenceKind(object o)
		{
			if (o is Effekseer.Data.ProceduralModelReference) return "ProceduralModel";
			if (o is Effekseer.Data.Value.DynamicEquationReference) return "DynamicEquation";
			return null;
		}

		static int ReferenceIndex(object reference)
		{
			var p = reference.GetType().GetProperty("Index");
			if (p == null) return -1;
			try { return (int)p.GetValue(reference, null); } catch { return -1; }
		}

		// ------------------------------------------------------- resource pass

		static void RewriteResources(Node target, ResourceCopier copier, bool allowMissing, List<string> report)
		{
			// Which paths will the runtime actually open? A node can carry a stale
			// path on a slot that is switched off — a disabled alpha texture, a
			// model path left behind on a sprite. Binary/Exporter.cs never writes
			// those, so a missing file there is harmless and must not fail the
			// build, while a missing LIVE file means the effect draws wrong.
			var live = new HashSet<object>(ReferenceEqualityComparer.Instance);
			CollectLive(target, live);

			// Collect first, act second: MaterialFileParameter rebuilds its own
			// texture slots when its path changes, which would invalidate objects
			// captured during the walk.
			var paths = new List<Tuple<string, Effekseer.Data.Value.Path>>();
			var materials = new List<Tuple<string, MaterialFileParameter>>();

			WalkSubtree(target, (keyPath, value) =>
			{
				var p = value as Effekseer.Data.Value.Path;
				if (p != null) { paths.Add(Tuple.Create(keyPath, p)); return; }

				var m = value as MaterialFileParameter;
				if (m != null) materials.Add(Tuple.Create(keyPath, m));
			});

			int dropped = 0;
			foreach (var pair in paths)
			{
				string abs = pair.Item2.AbsolutePath;
				if (string.IsNullOrEmpty(abs)) continue;

				bool isLive = live.Contains(pair.Item2);
				string moved = copier.Take(abs, FolderFor(pair.Item2), pair.Item1, isLive, allowMissing);
				if (moved != null) pair.Item2.SetAbsolutePath(moved);
				else { pair.Item2.SetAbsolutePath(string.Empty); if (!isLive) dropped++; }
			}

			foreach (var pair in materials)
			{
				var mfp = pair.Item2;
				string matAbs = mfp.Path.AbsolutePath;
				if (string.IsNullOrEmpty(matAbs)) continue;

				bool matLive = live.Contains(mfp.Path);

				// Capture the slot assignments, and their liveness, before the
				// material reloads and replaces the slot objects.
				var slots = new List<Tuple<string, string, bool>>();
				foreach (var vs in mfp.GetValueStatus())
				{
					var pi = vs.Value as Effekseer.Data.Value.PathForImage;
					if (pi == null || string.IsNullOrEmpty(pi.AbsolutePath)) continue;
					slots.Add(Tuple.Create(vs.Key.ToString(), pi.AbsolutePath, live.Contains(pi)));
				}

				string movedMat = copier.Take(matAbs, "Materials", pair.Item1 + ".Path", matLive, allowMissing);
				if (movedMat != null) mfp.Path.SetAbsolutePath(movedMat);
				else { mfp.Path.SetAbsolutePath(string.Empty); continue; }

				foreach (var slot in slots)
				{
					var vs = mfp.FindValue(slot.Item1, null, false);
					var pi = vs == null ? null : vs.Value as Effekseer.Data.Value.PathForImage;
					if (pi == null)
					{
						if (slot.Item3)
							report.Add(string.Format("WARNING: live material slot '{0}' vanished after reload ({1})", slot.Item1, pair.Item1));
						continue;
					}

					string moved = copier.Take(slot.Item2, "Texture", pair.Item1 + "." + slot.Item1, slot.Item3, allowMissing);
					if (moved != null) pi.SetAbsolutePath(moved);
					else { pi.SetAbsolutePath(string.Empty); if (!slot.Item3) dropped++; }
				}
			}

			if (dropped > 0)
				report.Add(string.Format("cleared {0} stale path(s) on slots the runtime does not read", dropped));
		}

		/// <summary>
		/// The Value.Path instances Binary/Exporter.cs would write for this
		/// subtree, gathered by reference so they can be told apart from paths
		/// left on switched-off slots. The gating mirrors Exporter.Export.
		/// </summary>
		static void CollectLive(Node node, HashSet<object> live)
		{
			var rc = node.RendererCommonValues;
			var adv = node.AdvancedRendererCommonValuesValues;

			bool rendered = node.IsRendered.Value && node.DrawingValues.Type.Value != RendererValues.ParamaterType.None;
			bool hasSound = node.SoundValues.Type.Value == SoundValues.ParamaterType.Use;

			if (rendered || hasSound)
			{
				var mat = rc.Material.Value;
				if (mat == RendererCommonValues.MaterialType.Default
					|| mat == RendererCommonValues.MaterialType.Lighting
					|| mat == RendererCommonValues.MaterialType.BackDistortion)
				{
					live.Add(rc.ColorTexture);
					if (mat == RendererCommonValues.MaterialType.Lighting) live.Add(rc.NormalTexture);

					if (adv.AlphaTextureParam.Enabled.Value) live.Add(adv.AlphaTextureParam.Texture);
					if (adv.UVDistortionTextureParam.Enabled.Value) live.Add(adv.UVDistortionTextureParam.Texture);
					if (adv.BlendTextureParams.Enabled.Value)
					{
						live.Add(adv.BlendTextureParams.BlendTextureParam.Texture);
						if (adv.BlendTextureParams.EnableBlendAlphaTexture.Value)
							live.Add(adv.BlendTextureParams.BlendAlphaTextureParam.Texture);
						if (adv.BlendTextureParams.EnableBlendUVDistortionTexture.Value)
							live.Add(adv.BlendTextureParams.BlendUVDistortionTextureParam.Texture);
					}
				}
				else if (mat == RendererCommonValues.MaterialType.File)
				{
					live.Add(rc.MaterialFile.Path);

					var info = Core.ResourceCache.LoadMaterialInformation(rc.MaterialFile.Path.AbsolutePath)
							   ?? new EfkUtl.MaterialInformation();
					foreach (var t in rc.MaterialFile.GetTextures(info))
					{
						if (t.Item1 == null || !t.Item1.IsShown) continue;
						var pi = t.Item1.Value as Effekseer.Data.Value.PathForImage;
						if (pi != null) live.Add(pi);
					}
				}

				if (hasSound) live.Add(node.SoundValues.Sound.Wave);
			}

			if (rendered && node.DrawingValues.Type.Value == RendererValues.ParamaterType.Model
				&& node.DrawingValues.Model.ModelReference.Value == ModelReferenceType.File)
				live.Add(node.DrawingValues.Model.Model);

			if (node.GenerationLocationValues.Type.Value == GenerationLocationValues.ParameterType.Model)
				live.Add(node.GenerationLocationValues.Model.Model);

			if (node.LocationValues.Type.Value == LocationValues.ParamaterType.NurbsCurve)
				live.Add(node.LocationValues.NurbsCurve.FilePath);

			foreach (var child in node.Children.Internal) CollectLive(child, live);
		}

		static string FolderFor(Effekseer.Data.Value.Path p)
		{
			if (p is Effekseer.Data.Value.PathForModel) return "Model";
			if (p is Effekseer.Data.Value.PathForMaterial) return "Materials";
			if (p is Effekseer.Data.Value.PathForSound) return "Sound";
			if (p is Effekseer.Data.Value.PathForCurve) return "Curve";
			return "Texture";
		}

		// ------------------------------------------------------ reference pass

		class DefinitionTable
		{
			readonly Dictionary<string, int> byXml = new Dictionary<string, int>(StringComparer.Ordinal);
			public string DynamicInputsXml;

			public int Ensure(RefRecord rec)
			{
				string key = rec.Kind + " " + rec.DefinitionXml;
				int existing;
				if (byXml.TryGetValue(key, out existing)) return existing;

				int index;
				if (rec.Kind == "ProceduralModel")
				{
					var param = new ProceduralModelParameter();
					if (!Core.Paste("ProceduralModel", param, rec.DefinitionXml)) return -1;
					if (!Core.ProceduralModel.ProceduralModels.Add(param)) return -1;
					index = Core.ProceduralModel.ProceduralModels.Values.Count - 1;
				}
				else
				{
					var eq = new DynamicEquation();
					if (!Core.Paste("DynamicEquation", eq, rec.DefinitionXml)) return -1;
					if (!Core.Dynamic.Equations.Add(eq)) return -1;
					index = Core.Dynamic.Equations.Values.Count - 1;
				}

				byXml[key] = index;
				return index;
			}
		}

		static void ApplyDynamicInputs(Harvest h, DefinitionTable defs, List<string> report)
		{
			if (h.DynamicInputsXml != null)
			{
				if (defs.DynamicInputsXml == null)
				{
					Core.Paste("Inputs", Core.Dynamic.Inputs, h.DynamicInputsXml);
					defs.DynamicInputsXml = h.DynamicInputsXml;
				}
				else if (defs.DynamicInputsXml != h.DynamicInputsXml)
				{
					report.Add(string.Format(
						"WARNING: '{0}' needs different dynamic inputs from an earlier part; the first part's inputs were kept",
						h.OriginalName));
				}
			}
		}

		/// <summary>
		/// Re-point each carried reference at the definition's new index in this
		/// project's collection. The index a paste restored is meaningless: it
		/// addressed the SOURCE project's collection.
		/// </summary>
		static void ApplyRefs(Node target, List<RefRecord> refs, DefinitionTable defs, List<string> report, bool ownValuesOnly)
		{
			if (refs.Count == 0) return;

			var live = new Dictionary<string, object>(StringComparer.Ordinal);
			Action<string, object> collect = (keyPath, value) =>
			{
				if (ReferenceKind(value) != null) live[keyPath] = value;
			};
			if (ownValuesOnly) WalkOwnValues(target, collect);
			else WalkSubtree(target, collect);

			foreach (var rec in refs)
			{
				object refObj;
				if (!live.TryGetValue(rec.KeyPath, out refObj))
				{
					report.Add(string.Format("WARNING: could not re-point {0} reference at {1}", rec.Kind, rec.KeyPath));
					continue;
				}

				int destIndex = defs.Ensure(rec);
				if (destIndex < 0)
				{
					report.Add(string.Format("WARNING: could not rebuild {0} definition for {1}", rec.Kind, rec.KeyPath));
					continue;
				}

				var m = refObj.GetType().GetMethod("SetValueWithIndex");
				m.Invoke(refObj, new object[] { destIndex });

				if (ReferenceIndex(refObj) != destIndex)
					report.Add(string.Format("WARNING: {0} reference at {1} did not take", rec.Kind, rec.KeyPath));
			}

			report.Add(string.Format("carried {0} project reference(s)", refs.Count));
		}

		// -------------------------------------------------------- override pass

		static void ConfigureWrapper(Node wrapper, Harvest h)
		{
			wrapper.Name.SetValue((h.Part.name ?? h.OriginalName) + " Anchor");
			wrapper.DrawingValues.Type.SetValue(RendererValues.ParamaterType.None);

			wrapper.CommonValues.MaxGeneration.Value.SetValue(1);
			wrapper.CommonValues.MaxGeneration.Infinite.SetValue(false);
			EfkCompat.GenerationTime(wrapper.CommonValues).SetCenter(1);
			EfkCompat.GenerationTime(wrapper.CommonValues).SetMax(1);
			EfkCompat.GenerationTime(wrapper.CommonValues).SetMin(1);

			// The anchor only has to outlive its child's spawning window. Children
			// default to RemoveWhenParentIsRemoved = false, so this is a margin,
			// not a hard cut-off.
			wrapper.CommonValues.Life.SetCenter(int.MaxValue / 4);
			wrapper.CommonValues.Life.SetMax(int.MaxValue / 4);
			wrapper.CommonValues.Life.SetMin(int.MaxValue / 4);
			EfkCompat.RemoveWhenLifeIsExtinct(wrapper.CommonValues).SetValue(true);

			// Without this the anchor outlives everything it carries and the effect
			// never ends: the game waits on handle.exists, so a battle would hang.
			// The runtime keeps a child group active until it has spawned its last
			// instance (InstanceGroup::IsActive), so a delayed part is not cut short.
			EfkCompat.RemoveWhenAllChildrenAreRemoved(wrapper.CommonValues).SetValue(true);

			if (h.Part.offset != null && h.Part.offset.Length == 3)
			{
				wrapper.LocationValues.Type.SetValue(LocationValues.ParamaterType.Fixed);
				SetVector(wrapper.LocationValues.Fixed.Location, h.Part.offset[0], h.Part.offset[1], h.Part.offset[2]);
			}

			if (h.Part.scale.HasValue)
			{
				float s = h.Part.scale.Value;
				wrapper.ScalingValues.Type.SetValue(ScaleValues.ParamaterType.Fixed);
				SetVector(wrapper.ScalingValues.Fixed.Scale, s, s, s);
			}
		}

		/// <summary>
		/// Shift when a part starts by <c>delay</c> frames, on top of when its
		/// author already had it start.
		///
		/// The first version set the grafted node's start offset to the delay
		/// outright, which was wrong twice over. It discarded the authored start, so
		/// a part built to wait 40 frames and given a delay of 30 began ten frames
		/// earlier rather than thirty later. And setting centre, max and min to one
		/// value collapsed whatever randomness the author put in the start.
		///
		/// The shift is applied to the outermost node of the part -- its first
		/// ancestor carrier when a chain was rebuilt -- not to the part itself. A
		/// child only spawns while its parent instance is alive, so pushing the
		/// part's own start past a short-lived carrier would silently stop it
		/// spawning at all. The outermost node hangs off the effect root, which never
		/// dies, and shifting it moves the whole part uniformly.
		/// </summary>
		static void ApplyDelay(Node outermost, Harvest h, List<string> report)
		{
			if (!h.Part.delay.HasValue || h.Part.delay.Value == 0) return;

			var start = EfkCompat.GenerationTimeOffset(outermost.CommonValues);
			string who = h.Part.name ?? h.OriginalName;

			if (start.IsDynamicEquationEnabled.Value)
			{
				report.Add(string.Format(
					"NOTE: delay not applied to '{0}': its start is driven by a dynamic equation", who));
				return;
			}

			float min = start.Min, max = start.Max, center = start.Center;
			float shift = h.Part.delay.Value;

			// A start before frame 0 is not a real start, so a negative delay can only
			// pull the part back as far as its earliest authored spawn.
			if (shift < 0 && min + shift < 0)
			{
				float allowed = Math.Min(0f, -min);
				report.Add(string.Format(
					"NOTE: delay {0} on '{1}' clamped to {2}: the part cannot start before frame 0",
					h.Part.delay.Value, who, allowed));
				shift = allowed;
				if (shift == 0) return;
			}

			// Set the three values directly. SetCenter re-derives max and min from the
			// larger half-width, which would quietly symmetrise a lopsided authored
			// range; SetMax and SetMin each recompute the centre as the midpoint.
			start.SetMaxDirectly(max + shift);
			start.SetMinDirectly(min + shift);
			start.SetCenterDirectly(center + shift);

			report.Add(string.Format("delay {0} added to '{1}' via '{2}': start {3}..{4} -> {5}..{6}",
				shift, who, outermost.Name.Value, min, max, min + shift, max + shift));
		}

		/// <summary>Multiply every node's lifetime in a subtree. Returns how many were changed.</summary>
		static int ScaleLives(Node node, float factor)
		{
			var life = node.CommonValues.Life;

			int min = Math.Max(1, (int)Math.Round(life.Min * factor));
			int max = Math.Max(min, (int)Math.Round(life.Max * factor));
			int center = Math.Min(max, Math.Max(min, (int)Math.Round(life.Center * factor)));

			// Order matters: the bounds clamp the centre, so widen before setting it.
			life.SetMin(min);
			life.SetMax(max);
			life.SetCenter(center);

			int n = 1;
			foreach (var child in node.Children.Internal) n += ScaleLives(child, factor);
			return n;
		}

		static void SetVector(Effekseer.Data.Value.Vector3D v, float x, float y, float z)
		{
			v.X.SetValue(x);
			v.Y.SetValue(y);
			v.Z.SetValue(z);
		}

		static void ApplyOverrides(Node target, Harvest h, bool wrapped, List<string> report)
		{
			if (wrapped)
			{
				// The part's inheritance flags described its ORIGINAL parent, which
				// no longer exists. Bind it to the anchor instead, and say so when
				// that is a change.
				if (h.InheritLocation != "Already" || h.InheritScale != "Already" || h.InheritRotation != "Already")
				{
					report.Add(string.Format(
						"NOTE: '{0}' inherited location={1} rotation={2} scale={3} from its original parent; bound to the anchor as Already",
						h.OriginalName, h.InheritLocation, h.InheritRotation, h.InheritScale));
				}

				target.CommonValues.LocationEffectType.SetValue(TranslationParentEffectType.Already);
				target.CommonValues.RotationEffectType.SetValue(ParentEffectType.Already);
				target.CommonValues.ScaleEffectType.SetValue(ParentEffectType.Already);
			}

			if (!string.IsNullOrEmpty(h.Part.name)) target.Name.SetValue(h.Part.name);

			if (h.Part.lifeScale.HasValue)
			{
				// Applied to the whole grafted subtree. Scaling only the root is
				// useless on a real part: a pillar whose root lives 50 frames can
				// easily have a child sprite living 120, so the part would outlast
				// its own root and appear not to have changed at all.
				int touched = ScaleLives(target, h.Part.lifeScale.Value);
				report.Add(string.Format("lifeScale {0} applied to {1} node(s) of '{2}'",
					h.Part.lifeScale.Value, touched, target.Name.Value));
			}
		}

		// ------------------------------------------------------------- verify

		/// <summary>
		/// Reload what we wrote and check the properties that decide whether the
		/// effect will actually draw: it parses, the node count is what we
		/// grafted, every resource path resolves to a file that exists, and the
		/// INFO chunk lists resources (8 bytes means none at all, which is the
		/// signature of an effect that loads and renders nothing).
		/// </summary>
		static bool Verify(string outPath, List<Harvest> harvests, List<string> report, int copiedFiles, bool quiet = false)
		{
			int expected = harvests.Sum(h => h.NodeCount + h.Ancestors.Count + (NeedsWrapper(h) ? 1 : 0));

			Core.New();
			if (!Core.LoadFrom(outPath))
			{
				Console.Error.WriteLine("VERIFY FAIL: our own output did not load");
				return false;
			}

			int actual = Core.Root.Children.Internal.Sum(c => CountSubtree(c));

			var missing = new List<string>();
			int resourceCount = 0;
			foreach (var child in Core.Root.Children.Internal)
			{
				WalkSubtree(child, (keyPath, value) =>
				{
					var p = value as Effekseer.Data.Value.Path;
					if (p == null || string.IsNullOrEmpty(p.AbsolutePath)) return;
					resourceCount++;

					// For a model it is the .efkmodel the runtime opens, not the
					// authored .fbx the project names (Binary/Define.cs GetModelPath).
					string onDisk = p.AbsolutePath;
					if (p is Effekseer.Data.Value.PathForModel)
					{
						string ext = System.IO.Path.GetExtension(onDisk);
						if (!string.IsNullOrEmpty(ext) && !ext.Equals(".efkmodel", StringComparison.OrdinalIgnoreCase))
							onDisk = System.IO.Path.ChangeExtension(onDisk, ".efkmodel");
					}

					if (!File.Exists(onDisk)) missing.Add(p.RelativePath + "  (" + keyPath + ")");
				});
			}

			int infoSize = InfoChunkSize(outPath);

			if (!quiet)
			{
				Console.WriteLine();
				Console.WriteLine("--- verify ---");
				Console.WriteLine("  nodes            {0} (expected {1})", actual, expected);
				Console.WriteLine("  resource refs    {0} in tree, {1} live copied, missing {2}", resourceCount, copiedFiles, missing.Count);
				Console.WriteLine("  INFO chunk       {0} bytes", infoSize);
			}

			bool ok = true;
			if (actual != expected)
			{
				Console.Error.WriteLine("VERIFY FAIL: node count changed across save/load");
				ok = false;
			}
			foreach (var m in missing.Take(10)) Console.Error.WriteLine("VERIFY FAIL: unresolved resource " + m);
			if (missing.Count > 0) ok = false;

			// An empty INFO chunk is only wrong when the exporter had something to
			// put in it. Counting every Value.Path in the tree is the wrong test:
			// paths left on switched-off slots are normal in this corpus and
			// Binary/Exporter.cs drops them, so a part that needs no texture of
			// its own legitimately produces an empty chunk. Ask what was actually
			// copied next to the output instead.
			if (copiedFiles > 0 && infoSize <= 8)
			{
				Console.Error.WriteLine("VERIFY FAIL: INFO chunk is empty although "
					+ copiedFiles + " live resource file(s) were copied for it");
				ok = false;
			}

			if (!quiet) Console.WriteLine(ok ? "  VERIFY OK" : "  VERIFY FAILED");
			return ok;
		}

		static bool NeedsWrapper(Harvest h)
		{
			return (h.Part.offset != null && h.Part.offset.Length == 3
					&& (h.Part.offset[0] != 0 || h.Part.offset[1] != 0 || h.Part.offset[2] != 0))
				|| (h.Part.scale.HasValue && Math.Abs(h.Part.scale.Value - 1.0f) > 1e-6f);
		}

		static int InfoChunkSize(string path)
		{
			byte[] data = File.ReadAllBytes(path);
			if (data.Length < 8 || Encoding.ASCII.GetString(data, 0, 4) != "EFKE") return -1;
			int pos = 8;
			while (pos + 8 <= data.Length)
			{
				string tag = Encoding.ASCII.GetString(data, pos, 4);
				int size = BitConverter.ToInt32(data, pos + 4);
				pos += 8;
				if (size < 0 || pos + size > data.Length) return -1;
				if (tag == "INFO") return size;
				pos += size;
			}
			return -1;
		}

		// ------------------------------------------------------------ plumbing

		class SourceResolver
		{
			readonly Dictionary<string, string> byId = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
			readonly string specDir;

			public SourceResolver(string cataloguePath, string specDir, bool quiet = false)
			{
				this.specDir = specDir;
				if (string.IsNullOrEmpty(cataloguePath)) return;

				string full = Path.IsPathRooted(cataloguePath) ? cataloguePath : Path.Combine(specDir, cataloguePath);
				if (!File.Exists(full))
				{
					Console.Error.WriteLine("warning: catalogue not found at " + full);
					return;
				}

				using (var doc = JsonDocument.Parse(File.ReadAllText(full)))
				{
					foreach (var e in doc.RootElement.GetProperty("effects").EnumerateArray())
					{
						JsonElement id, p;
						if (e.TryGetProperty("id", out id) && e.TryGetProperty("path", out p))
							byId[id.GetString()] = p.GetString();
					}
				}
				if (!quiet) Console.WriteLine("Catalogue: {0} effect ids available", byId.Count);
			}

			public string Resolve(string source)
			{
				if (string.IsNullOrEmpty(source)) return null;

				string viaId;
				if (byId.TryGetValue(source, out viaId) && File.Exists(viaId)) return viaId;

				string direct = Path.IsPathRooted(source) ? source : Path.Combine(specDir, source);
				if (File.Exists(direct)) return Path.GetFullPath(direct);

				return null;
			}
		}

		/// <summary>
		/// Copies each referenced file next to the output exactly once, keeping
		/// its basename where that is unambiguous. Two different files with the
		/// same name get distinguished rather than silently overwriting, which
		/// would swap one part's texture for another's.
		/// </summary>
		class ResourceCopier
		{
			readonly string outDir;
			readonly Dictionary<string, string> bySource = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
			readonly Dictionary<string, string> byDest = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
			public readonly List<string> Errors = new List<string>();
			int copied, liveCopied;

			/// <summary>Files actually placed next to the output.</summary>
			public int Copied { get { return copied; } }

			/// <summary>
			/// Of those, the ones Binary/Exporter.cs will actually write into the
			/// INFO chunk. A resolvable path on a switched-off slot is copied
			/// anyway -- an extra file beside the effect costs nothing and keeps
			/// the project editable -- but the runtime never loads it, so it must
			/// not be counted when asking whether resources went missing.
			/// </summary>
			public int LiveCopied { get { return liveCopied; } }

			public ResourceCopier(string outDir) { this.outDir = outDir; }

			/// <param name="live">
			/// Whether Binary/Exporter.cs would actually write this path. A dead
			/// slot pointing at a file that no longer exists is normal in the
			/// corpus and is dropped in silence; a live one is a defect.
			/// </param>
			public string Take(string sourceAbs, string folder, string what, bool live, bool allowMissing)
			{
				string existing;
				if (bySource.TryGetValue(sourceAbs, out existing)) return existing;

				// A model is authored as .fbx or .mqo, but the runtime opens the
				// .efkmodel beside it — Binary/Define.cs GetModelPath rewrites the
				// extension. The .efkmodel is the file that must travel; the
				// authored mesh often is not shipped at all, and its absence is
				// not a problem.
				bool isModel = folder == "Model";
				string runtimeSrc = sourceAbs;
				if (isModel)
				{
					string ext = Path.GetExtension(sourceAbs);
					if (!string.IsNullOrEmpty(ext) && !ext.Equals(".efkmodel", StringComparison.OrdinalIgnoreCase))
						runtimeSrc = Path.ChangeExtension(sourceAbs, ".efkmodel");
				}

				if (!File.Exists(runtimeSrc))
				{
					if (!live) return null;   // stale path on a switched-off slot

					string msg = isModel && runtimeSrc != sourceAbs
						? string.Format("model {0} has no .efkmodel beside it, so the runtime cannot draw it  (needed by {1})", sourceAbs, what)
						: string.Format("resource not found: {0}  (needed by {1})", sourceAbs, what);

					if (allowMissing) Console.WriteLine("  WARNING: " + msg + " - dropped");
					else Errors.Add(msg);
					return null;
				}

				string dir = Path.Combine(outDir, folder);
				Directory.CreateDirectory(dir);

				// Collisions are decided on the file the runtime reads.
				string runtimeName = Path.GetFileName(runtimeSrc);
				string runtimeDest = Path.Combine(dir, runtimeName);
				string suffix = null;

				string owner;
				// A file already there from an earlier run (several effects sharing
				// one resource folder) must not be overwritten by a different one
				// of the same name: the other effects would change silently.
				bool clash = byDest.TryGetValue(runtimeDest, out owner)
					? !SameFile(owner, runtimeSrc)
					: File.Exists(runtimeDest) && !SameFile(runtimeDest, runtimeSrc);
				if (clash)
				{
					suffix = "_" + ShortHash(runtimeSrc);
					runtimeDest = Path.Combine(dir,
						Path.GetFileNameWithoutExtension(runtimeName) + suffix + Path.GetExtension(runtimeName));
				}

				// Packaging into the source effect's own folder makes a file its
				// own destination, and File.Copy onto itself throws.
				if (!SamePath(runtimeSrc, runtimeDest))
					File.Copy(runtimeSrc, runtimeDest, true);
				copied++;
				if (live) liveCopied++;
				bySource[runtimeSrc] = runtimeDest;
				byDest[runtimeDest] = runtimeSrc;

				if (!isModel || runtimeSrc == sourceAbs)
					return runtimeDest;

				// Keep the authored mesh next to it when it exists, and return the
				// authored spelling so the saved project references exactly what
				// the source did. GetModelPath maps it back to the .efkmodel.
				string authoredName = Path.GetFileNameWithoutExtension(runtimeDest) + Path.GetExtension(sourceAbs);
				string authoredDest = Path.Combine(dir, authoredName);

				if (File.Exists(sourceAbs))
				{
					if (!SamePath(sourceAbs, authoredDest))
						File.Copy(sourceAbs, authoredDest, true);
					copied++;
					byDest[authoredDest] = sourceAbs;
				}

				bySource[sourceAbs] = authoredDest;
				return authoredDest;
			}

			static bool SamePath(string a, string b)
			{
				return string.Equals(Path.GetFullPath(a), Path.GetFullPath(b),
					OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal);
			}

			static bool SameFile(string a, string b)
			{
				try
				{
					var fa = new FileInfo(a);
					var fb = new FileInfo(b);
					if (fa.Length != fb.Length) return false;
					return Hash(a) == Hash(b);
				}
				catch { return false; }
			}

			static string Hash(string path)
			{
				using (var md5 = MD5.Create())
				using (var s = File.OpenRead(path))
					return BitConverter.ToString(md5.ComputeHash(s));
			}

			static string ShortHash(string text)
			{
				using (var md5 = MD5.Create())
					return BitConverter.ToString(md5.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").Substring(0, 6);
			}

			public IEnumerable<string> Summary()
			{
				yield return string.Format("copied {0} resource file(s) into {1}", copied, outDir);
			}
		}
	}
}
