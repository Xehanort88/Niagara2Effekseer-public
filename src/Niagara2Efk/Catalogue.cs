using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
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
	/// Parts catalogue for the effect assembler: walk every node of every effect
	/// in one or more corpora and emit a JSON index describing each node as a
	/// candidate part — where it came from, what it renders, which parameter
	/// groups it uses, which resource files it needs, and whether it depends on
	/// a controller parent.
	///
	/// Resource gating mirrors Binary/Exporter.cs exactly (Default /
	/// BackDistortion / Lighting / File material, advanced texture toggles,
	/// model renderer, generation-location model, sound). A path that is set on
	/// the node but not reachable by the exporter's rules is NOT a dependency:
	/// the runtime never loads it, so grafting the node does not need it.
	///
	/// Nothing under a corpus directory is written or modified.
	/// </summary>
	static class Catalogue
	{
		class Corpus
		{
			public string Id;
			public string Root;
		}

		public static int Run(string outPath, string[] corpusArgs)
		{
			var corpora = new List<Corpus>();
			foreach (var arg in corpusArgs)
			{
				string id, dir;
				int eq = arg.IndexOf('=');
				if (eq > 0)
				{
					id = arg.Substring(0, eq);
					dir = arg.Substring(eq + 1);
				}
				else
				{
					dir = arg;
					id = Path.GetFileName(dir.TrimEnd('\\', '/'));
				}
				dir = Path.GetFullPath(dir);
				if (!Directory.Exists(dir))
				{
					Console.Error.WriteLine("corpus directory not found: " + dir);
					return 2;
				}
				corpora.Add(new Corpus { Id = id, Root = dir });
			}

			var effects = new List<Dictionary<string, object>>();
			var stats = new Stats();

			foreach (var corpus in corpora)
			{
				var files = Directory.GetFiles(corpus.Root, "*.efkefc", SearchOption.AllDirectories)
									 .OrderBy(f => f, StringComparer.OrdinalIgnoreCase)
									 .ToArray();
				Console.WriteLine("Cataloguing {0} effects under {1} (corpus '{2}')", files.Length, corpus.Root, corpus.Id);

				foreach (var file in files)
				{
					effects.Add(CatalogueOne(corpus, file, stats));
				}
			}

			var doc = new Dictionary<string, object>
			{
				["generated"] = DateTime.UtcNow.ToString("o"),
				["toolVersion"] = Core.Version,
				["corpora"] = corpora.Select(c => new Dictionary<string, object>
				{
					["id"] = c.Id,
					["root"] = c.Root.Replace('\\', '/'),
					["effects"] = effects.Count(e => (string)e["corpus"] == c.Id),
				}).ToList(),
				["effects"] = effects,
			};

			Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath)));
			var opts = new JsonSerializerOptions { WriteIndented = true };
			File.WriteAllText(outPath, JsonSerializer.Serialize(doc, opts), new UTF8Encoding(false));
			Console.WriteLine("Catalogue written to {0}", Path.GetFullPath(outPath));

			stats.Print();
			return stats.LoadFailures == 0 ? 0 : 1;
		}

		class Stats
		{
			public int Effects, Nodes, LoadFailures, ControllerChildren, MissingResources;
			public Dictionary<string, int> Renderers = new Dictionary<string, int>();
			public Dictionary<string, int> Materials = new Dictionary<string, int>();

			public void Print()
			{
				Console.WriteLine();
				Console.WriteLine("effects            {0}", Effects);
				Console.WriteLine("load failures      {0}", LoadFailures);
				Console.WriteLine("nodes              {0}", Nodes);
				Console.WriteLine("controller-children{0,6}  (nodes whose parent is a non-drawing node)", ControllerChildren);
				Console.WriteLine("missing resources  {0}  (referenced files not found on disk)", MissingResources);
				Console.WriteLine();
				Console.WriteLine("--- nodes by renderer ---");
				foreach (var kv in Renderers.OrderByDescending(k => k.Value))
					Console.WriteLine("  {0,-16} {1}", kv.Key, kv.Value);
				Console.WriteLine();
				Console.WriteLine("--- drawing nodes by material type ---");
				foreach (var kv in Materials.OrderByDescending(k => k.Value))
					Console.WriteLine("  {0,-16} {1}", kv.Key, kv.Value);
			}
		}

		static Dictionary<string, object> CatalogueOne(Corpus corpus, string file, Stats stats)
		{
			stats.Effects++;
			string rel = Path.GetRelativePath(corpus.Root, file).Replace('\\', '/');
			var e = new Dictionary<string, object>
			{
				["id"] = corpus.Id + "/" + rel.Substring(0, rel.Length - ".efkefc".Length),
				["corpus"] = corpus.Id,
				["file"] = rel,
				["path"] = file.Replace('\\', '/'),
			};

			try
			{
				Core.New();
				if (!Core.LoadFrom(file))
				{
					stats.LoadFailures++;
					e["error"] = "Core.LoadFrom returned false";
					return e;
				}
			}
			catch (Exception ex)
			{
				stats.LoadFailures++;
				e["error"] = ex.GetType().Name + ": " + ex.Message;
				return e;
			}

			var nodes = new List<Dictionary<string, object>>();
			var effectResources = new List<Dictionary<string, object>>();
			int index = 0;
			foreach (var child in Core.Root.Children.Internal)
			{
				WalkNode(child, "" + index, "Root", 1, nodes, effectResources, stats);
				index++;
			}

			e["nodeCount"] = nodes.Count;
			e["renderers"] = nodes.Select(n => (string)n["renderer"]).Distinct().OrderBy(s => s).ToList();
			e["resources"] = DistinctResources(effectResources);
			e["nodes"] = nodes;
			return e;
		}

		static void WalkNode(Node node, string path, string namePath, int depth,
			List<Dictionary<string, object>> nodes, List<Dictionary<string, object>> effectResources, Stats stats)
		{
			stats.Nodes++;
			string name = node.Name.Value;
			string thisNamePath = namePath + "/" + name;
			var parent = node.Parent as Node;

			var renderer = node.DrawingValues.Type.Value;
			string rendererName = renderer.ToString();
			Bump(stats.Renderers, rendererName);

			// A "controller" is any parent that does not draw. Renderer None is
			// only one way to be one: a node with IsRendered false is equally a
			// pure carrier, and the corpus is full of them (a sprite node named
			// "! Motion Controller" with IsRendered off).
			bool parentIsController = parent != null && !Draws(parent);
			if (parentIsController) stats.ControllerChildren++;

			// Motion is a property of the whole ancestor chain, not of the
			// immediate parent, and not of the parent's renderer. A node four
			// levels down under nodes that both draw sprites still loses all its
			// movement when lifted alone, so record the chain and say plainly
			// whether anything up there is moving this node.
			var ancestors = new List<Dictionary<string, object>>();
			bool dependsOnAncestors = false;
			{
				var chain = new List<Node>();
				for (var a = parent; a != null; a = a.Parent as Node) chain.Add(a);
				chain.Reverse();

				foreach (var a in chain)
				{
					bool contributes = ContributesTransform(a);
					dependsOnAncestors |= contributes;
					ancestors.Add(new Dictionary<string, object>
					{
						["name"] = a.Name.Value,
						["renderer"] = a.DrawingValues.Type.Value.ToString(),
						["draws"] = Draws(a),
						["contributesTransform"] = contributes,
					});
				}
			}

			var resources = CollectResources(node);
			foreach (var r in resources)
			{
				if (!(bool)r["exists"]) stats.MissingResources++;
			}
			effectResources.AddRange(resources);

			var n = new Dictionary<string, object>
			{
				["path"] = path,
				["namePath"] = thisNamePath,
				["name"] = name,
				["depth"] = depth,
				["childCount"] = node.Children.Count,
				["subtreeNodeCount"] = CountSubtree(node),
				["isRendered"] = node.IsRendered.Value,
				["renderer"] = rendererName,
				["drawsSomething"] = node.IsRendered.Value && renderer != RendererValues.ParamaterType.None,

				// Dependence on the parent. Children spawn per parent instance, so
				// any non-root parent matters; a parent with renderer None exists
				// only to drive its children (the "controller" case in the brief).
				["parentPath"] = parent == null ? null : ParentPath(path),
				["parentRenderer"] = parent == null ? null : parent.DrawingValues.Type.Value.ToString(),
				["parentIsController"] = parentIsController,

				// True means lifting this node on its own loses motion, rotation
				// or scale that an ancestor was supplying. The assembler's
				// "ancestors" option exists to carry that chain across.
				["dependsOnAncestors"] = dependsOnAncestors,
				["ancestors"] = ancestors,
				["inherit"] = new Dictionary<string, object>
				{
					["location"] = node.CommonValues.LocationEffectType.Value.ToString(),
					["rotation"] = node.CommonValues.RotationEffectType.Value.ToString(),
					["scale"] = node.CommonValues.ScaleEffectType.Value.ToString(),
					["color"] = node.RendererCommonValues.ColorInheritType.Value.ToString(),
				},

				["common"] = Common(node.CommonValues),
				["motion"] = Motion(node),
				["render"] = Render(node),
				["sound"] = node.SoundValues.Type.Value.ToString(),
				["resources"] = resources,
			};

			if (renderer != RendererValues.ParamaterType.None && node.IsRendered.Value)
				Bump(stats.Materials, node.RendererCommonValues.Material.Value.ToString());

			nodes.Add(n);

			int i = 0;
			foreach (var c in node.Children.Internal)
			{
				WalkNode(c, path + "/" + i, thisNamePath, depth + 1, nodes, effectResources, stats);
				i++;
			}
		}

		static bool Draws(Node n)
		{
			return n.IsRendered.Value && n.DrawingValues.Type.Value != RendererValues.ParamaterType.None;
		}

		/// <summary>
		/// Does this node move, turn, resize or displace whatever hangs beneath
		/// it? Anything other than an identity transform counts, whether or not
		/// the node draws.
		/// </summary>
		static bool ContributesTransform(Node n)
		{
			if (n.LocationValues.Type.Value != LocationValues.ParamaterType.Fixed) return true;
			if (!IsZero(n.LocationValues.Fixed.Location)) return true;

			if (n.RotationValues.Type.Value != RotationValues.ParamaterType.Fixed) return true;
			if (!IsZero(n.RotationValues.Fixed.Rotation)) return true;

			if (n.ScalingValues.Type.Value != ScaleValues.ParamaterType.Fixed) return true;
			if (!IsOne(n.ScalingValues.Fixed.Scale)) return true;

			if (n.GenerationLocationValues.Type.Value != GenerationLocationValues.ParameterType.Point) return true;
			if (!IsZero(n.GenerationLocationValues.Point.Location)) return true;

			for (int i = 1; i <= 4; i++)
			{
				var ff = i == 1 ? n.LocationAbsValues.LocalForceField1
					   : i == 2 ? n.LocationAbsValues.LocalForceField2
					   : i == 3 ? n.LocationAbsValues.LocalForceField3
								: n.LocationAbsValues.LocalForceField4;
				if (ff.Type.Value != LocalForceFieldType.None) return true;
			}

			return false;
		}

		static bool IsZero(Effekseer.Data.Value.Vector3D v)
		{
			return v.X.Value == 0 && v.Y.Value == 0 && v.Z.Value == 0;
		}

		static bool IsZero(Effekseer.Data.Value.Vector3DWithRandom v)
		{
			return v.X.Center == 0 && v.Y.Center == 0 && v.Z.Center == 0
				&& v.X.Max == 0 && v.Y.Max == 0 && v.Z.Max == 0
				&& v.X.Min == 0 && v.Y.Min == 0 && v.Z.Min == 0;
		}

		static bool IsOne(Effekseer.Data.Value.Vector3D v)
		{
			return v.X.Value == 1 && v.Y.Value == 1 && v.Z.Value == 1;
		}

		static string ParentPath(string path)
		{
			int slash = path.LastIndexOf('/');
			return slash < 0 ? null : path.Substring(0, slash);
		}

		static int CountSubtree(NodeBase node)
		{
			int n = 1;
			foreach (var c in node.Children.Internal) n += CountSubtree(c);
			return n;
		}

		static Dictionary<string, object> Common(CommonValues cv)
		{
			return new Dictionary<string, object>
			{
				["life"] = Range(cv.Life),
				["maxGeneration"] = cv.MaxGeneration.Infinite.Value ? (object)"infinite" : cv.MaxGeneration.Value.Value,
				["generationTime"] = Range(EfkCompat.GenerationTime(cv)),
				["generationTimeOffset"] = Range(EfkCompat.GenerationTimeOffset(cv)),
				["removeWhenLifeIsExtinct"] = EfkCompat.RemoveWhenLifeIsExtinct(cv).Value,
				["removeWhenParentIsRemoved"] = EfkCompat.RemoveWhenParentIsRemoved(cv).Value,
				["removeWhenAllChildrenAreRemoved"] = EfkCompat.RemoveWhenAllChildrenAreRemoved(cv).Value,
			};
		}

		static Dictionary<string, object> Motion(Node node)
		{
			var forceFields = new[]
			{
				node.LocationAbsValues.LocalForceField1, node.LocationAbsValues.LocalForceField2,
				node.LocationAbsValues.LocalForceField3, node.LocationAbsValues.LocalForceField4,
			}
			.Select(f => f.Type.Value.ToString())
			.Where(t => t != "None")
			.ToList();

			return new Dictionary<string, object>
			{
				["location"] = node.LocationValues.Type.Value.ToString(),
				["rotation"] = node.RotationValues.Type.Value.ToString(),
				["scaling"] = node.ScalingValues.Type.Value.ToString(),
				["spawn"] = node.GenerationLocationValues.Type.Value.ToString(),
				["forceFields"] = forceFields,
				["killRule"] = node.KillRulesValues.Type.Value.ToString(),
			};
		}

		static Dictionary<string, object> Render(Node node)
		{
			var rc = node.RendererCommonValues;
			var adv = node.AdvancedRendererCommonValuesValues;

			var advanced = new List<string>();
			if (adv.AlphaTextureParam.Enabled.Value) advanced.Add("AlphaTexture");
			if (adv.UVDistortionTextureParam.Enabled.Value) advanced.Add("UVDistortion");
			if (adv.BlendTextureParams.Enabled.Value) advanced.Add("BlendTexture");
			if (adv.AlphaCutoffParam.Enabled.Value) advanced.Add("AlphaCutoff");
			if (adv.FalloffParam.Enabled.Value) advanced.Add("Falloff");
			if (adv.SoftParticleParams.Enabled.Value) advanced.Add("SoftParticle");

			var r = new Dictionary<string, object>
			{
				["material"] = rc.Material.Value.ToString(),
				["alphaBlend"] = rc.AlphaBlend.Value.ToString(),
				["uv"] = rc.UV.Value.ToString(),
				["fadeIn"] = rc.FadeInType.Value.ToString(),
				["fadeOut"] = rc.FadeOutType.Value.ToString(),
				["zSort"] = node.DepthValues.ZSort.Value.ToString(),
				["advanced"] = advanced,
			};

			var dv = node.DrawingValues;
			switch (dv.Type.Value)
			{
				case RendererValues.ParamaterType.Sprite:
					r["billboard"] = dv.Sprite.Billboard.Value.ToString();
					r["color"] = dv.ColorAll.Type.Value.ToString();
					break;
				case RendererValues.ParamaterType.Model:
					r["billboard"] = dv.Model.Billboard.Value.ToString();
					r["color"] = dv.ColorAll.Type.Value.ToString();
					r["modelReference"] = dv.Model.ModelReference.Value.ToString();
					break;
				case RendererValues.ParamaterType.Ring:
					r["billboard"] = dv.Ring.Billboard.Value.ToString();
					r["viewingAngle"] = dv.Ring.ViewingAngle.Value.ToString();
					break;
				case RendererValues.ParamaterType.Track:
					r["trackColor"] = dv.TrailColorCenter.Type.Value.ToString();
					break;
			}
			return r;
		}

		static Dictionary<string, object> Range(Effekseer.Data.Value.IntWithRandom v)
		{
			return new Dictionary<string, object> { ["center"] = v.Center, ["min"] = v.Min, ["max"] = v.Max };
		}

		static Dictionary<string, object> Range(Effekseer.Data.Value.FloatWithRandom v)
		{
			return new Dictionary<string, object> { ["center"] = v.Center, ["min"] = v.Min, ["max"] = v.Max };
		}

		/// <summary>
		/// Resources the runtime will actually load for this node, following the
		/// same gating as Binary/Exporter.cs. Each entry: kind (texture /
		/// normalTexture / distortionTexture / model / material / sound / curve),
		/// role (which slot), the path relative to the effect file, the absolute
		/// path, and whether the file exists.
		/// </summary>
		static List<Dictionary<string, object>> CollectResources(Node node)
		{
			var list = new List<Dictionary<string, object>>();
			var rc = node.RendererCommonValues;
			var adv = node.AdvancedRendererCommonValuesValues;

			bool rendered = node.IsRendered.Value && node.DrawingValues.Type.Value != RendererValues.ParamaterType.None;
			bool hasSound = node.SoundValues.Type.Value == SoundValues.ParamaterType.Use;
			bool isRenderedNode = rendered || hasSound;

			if (isRenderedNode)
			{
				var mat = rc.Material.Value;
				if (mat == RendererCommonValues.MaterialType.Default || mat == RendererCommonValues.MaterialType.Lighting)
				{
					Add(list, "texture", "color", rc.ColorTexture);
					if (mat == RendererCommonValues.MaterialType.Lighting)
						Add(list, "normalTexture", "normal", rc.NormalTexture);
					AddAdvanced(list, adv, "texture");
				}
				else if (mat == RendererCommonValues.MaterialType.BackDistortion)
				{
					Add(list, "distortionTexture", "color", rc.ColorTexture);
					AddAdvanced(list, adv, "distortionTexture");
				}
				else if (mat == RendererCommonValues.MaterialType.File)
				{
					Add(list, "material", "material", rc.MaterialFile.Path);

					var info = Core.ResourceCache.LoadMaterialInformation(rc.MaterialFile.Path.AbsolutePath);
					if (info == null) info = new EfkUtl.MaterialInformation();
					foreach (var t in rc.MaterialFile.GetTextures(info))
					{
						if (t.Item1 == null) continue;
						var p = t.Item1.Value as Effekseer.Data.Value.PathForImage;
						if (p == null) continue;
						string kind = t.Item2.Type == EfkUtl.TextureType.Value ? "normalTexture" : "texture";
						Add(list, kind, "material:" + t.Item2.Name, p);
					}
				}

				if (hasSound)
					Add(list, "sound", "wave", node.SoundValues.Sound.Wave);
			}

			if (rendered && node.DrawingValues.Type.Value == RendererValues.ParamaterType.Model)
				Add(list, "model", "renderer", node.DrawingValues.Model.Model);

			if (node.GenerationLocationValues.Type.Value == GenerationLocationValues.ParameterType.Model)
				Add(list, "model", "spawn", node.GenerationLocationValues.Model.Model);

			if (node.LocationValues.Type.Value == LocationValues.ParamaterType.NurbsCurve)
				Add(list, "curve", "nurbs", node.LocationValues.NurbsCurve.FilePath);

			return list;
		}

		static void AddAdvanced(List<Dictionary<string, object>> list, AdvancedRenderCommonValues adv, string kind)
		{
			if (adv.AlphaTextureParam.Enabled.Value)
				Add(list, kind, "alpha", adv.AlphaTextureParam.Texture);
			if (adv.UVDistortionTextureParam.Enabled.Value)
				Add(list, kind, "uvDistortion", adv.UVDistortionTextureParam.Texture);
			if (adv.BlendTextureParams.Enabled.Value)
			{
				Add(list, kind, "blend", adv.BlendTextureParams.BlendTextureParam.Texture);
				if (adv.BlendTextureParams.EnableBlendAlphaTexture.Value)
					Add(list, kind, "blendAlpha", adv.BlendTextureParams.BlendAlphaTextureParam.Texture);
				if (adv.BlendTextureParams.EnableBlendUVDistortionTexture.Value)
					Add(list, kind, "blendUVDistortion", adv.BlendTextureParams.BlendUVDistortionTextureParam.Texture);
			}
		}

		static void Add(List<Dictionary<string, object>> list, string kind, string role, Effekseer.Data.Value.Path p)
		{
			string rel = p.RelativePath;
			if (string.IsNullOrEmpty(rel)) return;
			string abs = p.AbsolutePath;

			// Models are authored as .fbx/.mqo but the runtime loads the converted
			// .efkmodel beside them; the exporter rewrites the extension
			// (Binary/Define.cs GetModelPath). Record both, and check existence on
			// the file the runtime will actually open.
			if (kind == "model" && !string.IsNullOrEmpty(Path.GetExtension(rel)))
			{
				string runtimeRel = Path.ChangeExtension(rel, ".efkmodel");
				string runtimeAbs = Path.ChangeExtension(abs, ".efkmodel");
				list.Add(new Dictionary<string, object>
				{
					["kind"] = kind,
					["role"] = role,
					["relative"] = runtimeRel.Replace('\\', '/'),
					["absolute"] = runtimeAbs.Replace('\\', '/'),
					["exists"] = File.Exists(runtimeAbs),
					["authoredRelative"] = rel,
				});
				return;
			}

			list.Add(new Dictionary<string, object>
			{
				["kind"] = kind,
				["role"] = role,
				["relative"] = rel,
				["absolute"] = abs.Replace('\\', '/'),
				["exists"] = File.Exists(abs),
			});
		}

		static List<Dictionary<string, object>> DistinctResources(List<Dictionary<string, object>> all)
		{
			return all
				.GroupBy(r => (string)r["kind"] + "|" + (string)r["relative"])
				.Select(g => new Dictionary<string, object>
				{
					["kind"] = g.First()["kind"],
					["relative"] = g.First()["relative"],
					["absolute"] = g.First()["absolute"],
					["exists"] = g.First()["exists"],
					["usedByNodes"] = g.Count(),
				})
				.OrderBy(r => (string)r["kind"]).ThenBy(r => (string)r["relative"])
				.ToList();
		}

		static void Bump(Dictionary<string, int> d, string key)
		{
			if (!d.ContainsKey(key)) d[key] = 0;
			d[key]++;
		}
	}
}
