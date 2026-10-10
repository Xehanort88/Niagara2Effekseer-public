using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using Effekseer;
using Effekseer.Data;

namespace Niagara2Efk.Mapper
{
	/// <summary>
	/// Reads an intermediate document (schema/intermediate-v1.md) and writes an
	/// .efkefc, copying every referenced resource next to the output.
	///
	/// This is the back half of the converter. It never reads a .uasset, and it
	/// does not care whether the document was produced by Unreal Editor Python,
	/// an offline parser or a person, which is exactly why the mapper can be
	/// built and verified before any extractor exists.
	/// </summary>
	static class Mapper
	{
		/// <summary>
		/// Everything the mapper could not honour, kept alongside whatever the
		/// document already declared unsupported. Written out as a report next
		/// to the effect, because an explicit list of what did not convert is
		/// part of the deliverable rather than a log line.
		/// </summary>
		class Losses
		{
			public List<string> Items = new List<string>();
			public void Add(string where, string what) => Items.Add(where + ": " + what);
		}

		/// <summary>
		/// Stands in for a texture the document names but cannot supply, which
		/// is the normal state before a real extractor exists. Substituting one
		/// lets the effect be played and judged; leaving the slot empty would
		/// make every node draw nothing and hide every other mapping error
		/// behind that. The substitution is always reported.
		/// </summary>
		static string placeholderTexture;

		public static int Run(string docPath, string outPath, string placeholder = null)
		{
			placeholderTexture = placeholder;

			var opts = new JsonSerializerOptions
			{
				ReadCommentHandling = JsonCommentHandling.Skip,
				AllowTrailingCommas = true,
			};

			Document doc;
			try
			{
				doc = JsonSerializer.Deserialize<Document>(File.ReadAllText(docPath), opts);
			}
			catch (JsonException e)
			{
				Console.Error.WriteLine("could not read " + docPath + ": " + e.Message);
				return 1;
			}

			if (doc.schemaVersion != 1)
			{
				Console.Error.WriteLine("unsupported schemaVersion " + doc.schemaVersion + "; this mapper reads version 1");
				return 2;
			}

			// Provenance is a build requirement, not a nicety: converted output
			// embeds the source vendor's art, and the licence question has to
			// stay answerable later. Refuse rather than produce an untraceable
			// effect.
			if (doc.provenance == null || string.IsNullOrWhiteSpace(doc.provenance.sourceSystem))
			{
				Console.Error.WriteLine("document has no provenance.sourceSystem; refusing to convert");
				return 2;
			}

			if (doc.nodes == null || doc.nodes.Count == 0)
			{
				Console.Error.WriteLine("document has no nodes");
				return 2;
			}

			string outDir = Path.GetDirectoryName(outPath);
			Directory.CreateDirectory(outDir);

			var losses = new Losses();

			Core.New();

			// Core.New() already appends one child under Root. Reuse it for the
			// first node rather than adding a second, otherwise the effect ships
			// with an empty sibling.
			while (Core.Root.Children.Count > 0) Core.Root.RemoveChild(Core.Root.Children[0] as Node);
			foreach (var spec in doc.nodes)
			{
				Core.Root.AddChild();
				var node = Core.Root.Children[Core.Root.Children.Count - 1] as Node;
				Apply(spec, node, doc, outDir, losses, Path.GetDirectoryName(Path.GetFullPath(docPath)));
			}

			Core.SaveTo(outPath);

			WriteReport(doc, losses, outPath);

			Console.WriteLine("Mapped {0} -> {1}", doc.effect?.name ?? "(unnamed)", outPath);
			Console.WriteLine("  source        {0} ({1})", doc.provenance.sourceSystem, doc.provenance.sourceEngine);
			Console.WriteLine("  extractor     {0}", doc.provenance.extractor);
			Console.WriteLine("  nodes         {0}", CountNodes(doc.nodes));
			Console.WriteLine("  declared gaps {0}", doc.unsupported?.Count ?? 0);
			Console.WriteLine("  mapper gaps   {0}", losses.Items.Count);
			foreach (var l in losses.Items) Console.WriteLine("    - " + l);

			return Verify(outPath, doc);
		}

		static int CountNodes(List<NodeSpec> nodes)
			=> nodes.Sum(n => 1 + CountNodes(n.children ?? new List<NodeSpec>()));

		// ------------------------------------------------------------- node

		static void Apply(NodeSpec spec, Node node, Document doc, string outDir, Losses losses, string docDir)
		{
			string where = spec.name ?? "(unnamed)";
			node.Name.SetValue(where);

			ApplyCommon(spec.common, node, where, losses);
			ApplyGenerationLocation(spec.generationLocation, node, where, losses);
			ApplyLocation(spec.location, node, where, losses);
			ApplyScaling(spec.scaling, node, where, losses);
			ApplyRotation(spec.rotation, node, where, losses);
			ApplyForceFields(spec.forceFields, node, where, losses);

			// Cascade's camera offset and Effekseer's depth offset both slide the
			// particle along the view direction, so the number carries over. Which
			// way positive points is not verified here; the front end says so.
			if (spec.depthOffset.HasValue) node.DepthValues.DepthOffset.SetValue(spec.depthOffset.Value);
			ApplyRendererCommon(spec.rendererCommon, node, where, losses, outDir, docDir);
			ApplyRenderer(spec.renderer, node, where, losses);

			foreach (var childSpec in spec.children ?? new List<NodeSpec>())
			{
				node.AddChild();
				var child = node.Children[node.Children.Count - 1] as Node;
				Apply(childSpec, child, doc, outDir, losses, docDir);
			}
		}

		static void ApplyCommon(CommonSpec c, Node node, string where, Losses losses)
		{
			if (c == null) return;

			if (c.life != null) SetRange(node.CommonValues.Life, c.life);

			if (c.maxGeneration.ValueKind == JsonValueKind.String)
			{
				if (c.maxGeneration.GetString() == "infinite")
				{
					node.CommonValues.MaxGeneration.Infinite.SetValue(true);
				}
				else
				{
					losses.Add(where, "maxGeneration '" + c.maxGeneration.GetString() + "' is not a number or \"infinite\"");
				}
			}
			else if (c.maxGeneration.ValueKind == JsonValueKind.Number)
			{
				node.CommonValues.MaxGeneration.Value.SetValue(c.maxGeneration.GetInt32());
				node.CommonValues.MaxGeneration.Infinite.SetValue(false);
			}

			// These three are flat on CommonValues at 1.70e. Do NOT carry this
			// forward to 1.8x unchanged: there they are [IO(Import = true,
			// Export = false)] and the live fields move under
			// CommonValues.Generation.* / CommonValues.Removal.*, so writing the
			// flat ones succeeds in memory and is silently dropped on save.
			if (c.generationTime != null) SetRange(node.CommonValues.GenerationTime, c.generationTime);
			if (c.generationTimeOffset != null) SetRange(node.CommonValues.GenerationTimeOffset, c.generationTimeOffset);
			node.CommonValues.RemoveWhenLifeIsExtinct.SetValue(c.removeWhenLifeIsExtinct);
		}

		static void ApplyGenerationLocation(GenerationLocationSpec g, Node node, string where, Losses losses)
		{
			if (g == null || string.IsNullOrEmpty(g.type)) return;

			if (!Enum.TryParse<GenerationLocationValues.ParameterType>(g.type, out var type))
			{
				losses.Add(where, "generationLocation.type '" + g.type + "' is not an Effekseer spawn method; left at Point");
				return;
			}

			node.GenerationLocationValues.Type.SetValue(type);

			switch (type)
			{
				case GenerationLocationValues.ParameterType.Sphere:
					if (g.sphere?.radius != null) SetRange(node.GenerationLocationValues.Sphere.Radius, g.sphere.radius);

					// Without spread on the angles every particle lands on the
					// same point of the sphere, and 62 particles look like one.
					if (g.sphere?.rotationX != null) SetRange(node.GenerationLocationValues.Sphere.RotationX, g.sphere.rotationX);
					if (g.sphere?.rotationY != null) SetRange(node.GenerationLocationValues.Sphere.RotationY, g.sphere.rotationY);

					if (g.sphere != null && g.sphere.rotationX == null && g.sphere.rotationY == null)
						losses.Add(where, "sphere spawn gave no rotationX/rotationY, so every particle spawns at the same point on the sphere");
					break;

				// A ring. Its radius may be a range, which is how a filled disc is
				// told apart from a source that spawns only on the rim.
				case GenerationLocationValues.ParameterType.Circle:
					if (g.circle == null)
					{
						losses.Add(where, "circle spawn carries no parameters, so it kept Effekseer's defaults");
						break;
					}

					if (!string.IsNullOrEmpty(g.circle.axisDirection)
						&& Enum.TryParse<AxisType>(g.circle.axisDirection, out var axis))
						node.GenerationLocationValues.Circle.AxisDirection.SetValue(axis);

					if (g.circle.radius != null) SetRange(node.GenerationLocationValues.Circle.Radius, g.circle.radius);
					if (g.circle.angleStart != null) SetRange(node.GenerationLocationValues.Circle.AngleStart, g.circle.angleStart);
					if (g.circle.angleEnd != null) SetRange(node.GenerationLocationValues.Circle.AngleEnd, g.circle.angleEnd);
					if (g.circle.division.HasValue) node.GenerationLocationValues.Circle.Division.SetValue(g.circle.division.Value);

					if (!string.IsNullOrEmpty(g.circle.type)
						&& Enum.TryParse<GenerationLocationValues.CircleType>(g.circle.type, out var circleOrder))
						node.GenerationLocationValues.Circle.Type.SetValue(circleOrder);
					break;

				// Point randomises each axis independently, so a point with a range
				// on every axis is a box and needs no approximation.
				case GenerationLocationValues.ParameterType.Point:
					if (g.point?.location != null) SetVector(node.GenerationLocationValues.Point.Location, g.point.location);
					break;

				default:
					losses.Add(where, "generationLocation type " + type + " is accepted but its parameters are not mapped yet");
					break;
			}
		}

		static void ApplyLocation(LocationSpec l, Node node, string where, Losses losses)
		{
			if (l == null || string.IsNullOrEmpty(l.type)) return;

			if (!Enum.TryParse<LocationValues.ParamaterType>(l.type, out var type))
			{
				losses.Add(where, "location.type '" + l.type + "' is not an Effekseer location type; left at Fixed");
				return;
			}

			node.LocationValues.Type.SetValue(type);

			if (type == LocationValues.ParamaterType.PVA && l.pva != null)
			{
				if (l.pva.location != null) SetVector(node.LocationValues.PVA.Location, l.pva.location);
				if (l.pva.velocity != null) SetVector(node.LocationValues.PVA.Velocity, l.pva.velocity);
				if (l.pva.acceleration != null) SetVector(node.LocationValues.PVA.Acceleration, l.pva.acceleration);
			}
			else if (type == LocationValues.ParamaterType.Fixed)
			{
				if (l.fixedOffset != null) SetVector(node.LocationValues.Fixed.Location, l.fixedOffset);
			}
			else
			{
				losses.Add(where, "location type " + type + " is accepted but its parameters are not mapped yet");
			}
		}

		static void ApplyScaling(ScalingSpec s, Node node, string where, Losses losses)
		{
			if (s == null || string.IsNullOrEmpty(s.type)) return;

			if (!Enum.TryParse<ScaleValues.ParamaterType>(s.type, out var type))
			{
				losses.Add(where, "scaling.type '" + s.type + "' is not an Effekseer scale type; left at Fixed");
				return;
			}

			node.ScalingValues.Type.SetValue(type);

			if (type == ScaleValues.ParamaterType.SinglePVA && s.singlePVA != null)
			{
				if (s.singlePVA.scale != null) SetRange(node.ScalingValues.SinglePVA.Scale, s.singlePVA.scale);
				if (s.singlePVA.velocity != null) SetRange(node.ScalingValues.SinglePVA.Velocity, s.singlePVA.velocity);
			}
			else if (type == ScaleValues.ParamaterType.SingleEasing && s.singleEasing != null)
			{
				if (s.singleEasing.start != null) SetRange(node.ScalingValues.SingleEasing.Start, s.singleEasing.start);
				if (s.singleEasing.end != null) SetRange(node.ScalingValues.SingleEasing.End, s.singleEasing.end);
			}
			else if (type != ScaleValues.ParamaterType.SinglePVA && type != ScaleValues.ParamaterType.SingleEasing)
			{
				losses.Add(where, "scaling type " + type + " is accepted but its parameters are not mapped yet");
			}
		}

		static void ApplyRotation(RotationSpec r, Node node, string where, Losses losses)
		{
			if (r == null || string.IsNullOrEmpty(r.type)) return;

			if (!Enum.TryParse<RotationValues.ParamaterType>(r.type, out var type))
			{
				losses.Add(where, "rotation.type '" + r.type + "' is not an Effekseer rotation type; left at Fixed");
				return;
			}

			node.RotationValues.Type.SetValue(type);

			if (type == RotationValues.ParamaterType.PVA && r.pva != null)
			{
				if (r.pva.rotation != null) SetVector(node.RotationValues.PVA.Rotation, r.pva.rotation);
				if (r.pva.velocity != null) SetVector(node.RotationValues.PVA.Velocity, r.pva.velocity);
				if (r.pva.acceleration != null) SetVector(node.RotationValues.PVA.Acceleration, r.pva.acceleration);
			}
			else if (type == RotationValues.ParamaterType.Fixed)
			{
				if (r.fixedRotation != null) SetVector(node.RotationValues.Fixed.Rotation, r.fixedRotation);
			}
			else
			{
				losses.Add(where, "rotation type " + type + " is accepted but its parameters are not mapped yet");
			}
		}

		static void ApplyForceFields(List<ForceFieldSpec> fields, Node node, string where, Losses losses)
		{
			if (fields == null || fields.Count == 0) return;

			// Effekseer has exactly four slots. Anything beyond that is a real
			// loss and has to be said out loud, not truncated in silence.
			var slots = new[]
			{
				node.LocationAbsValues.LocalForceField1,
				node.LocationAbsValues.LocalForceField2,
				node.LocationAbsValues.LocalForceField3,
				node.LocationAbsValues.LocalForceField4,
			};

			for (int i = 0; i < fields.Count; i++)
			{
				if (i >= slots.Length)
				{
					losses.Add(where, "force field '" + fields[i].type + "' dropped; Effekseer has only four slots");
					continue;
				}

				var f = fields[i];
				if (!Enum.TryParse<LocalForceFieldType>(f.type, out var type))
				{
					losses.Add(where, "force field type '" + f.type + "' has no Effekseer equivalent; slot left empty");
					continue;
				}

				var slot = slots[i];
				slot.Type.SetValue(type);

				if (f.power.HasValue) slot.Power.SetValue(f.power.Value);
				if (f.rotation != null) SetVector(slot.Rotation, f.rotation);

				if (type == LocalForceFieldType.Gravity && f.gravity != null)
				{
					// Gravity is a direction vector rather than a scalar power,
					// and Vector3D has no whole-vector setter: its X/Y/Z are
					// plain Float values set one at a time.
					slot.Gravity.Gravity.X.SetValue(Center(f.gravity.x));
					slot.Gravity.Gravity.Y.SetValue(Center(f.gravity.y));
					slot.Gravity.Gravity.Z.SetValue(Center(f.gravity.z));
				}

				if (type == LocalForceFieldType.Turbulence && f.turbulence != null)
				{
					if (f.turbulence.fieldScale.HasValue) slot.Turbulence.FieldScale.SetValue(f.turbulence.fieldScale.Value);
					if (f.turbulence.octave.HasValue) slot.Turbulence.Octave.SetValue(f.turbulence.octave.Value);
					if (f.turbulence.seed.HasValue) slot.Turbulence.Seed.SetValue(f.turbulence.seed.Value);
				}
			}
		}

		static void ApplyRendererCommon(RendererCommonSpec r, Node node, string where, Losses losses, string outDir, string docDir)
		{
			if (r == null) return;

			if (!string.IsNullOrEmpty(r.materialType))
			{
				if (Enum.TryParse<RendererCommonValues.MaterialType>(r.materialType, out var mt))
				{
					node.RendererCommonValues.Material.SetValue(mt);
					if (mt == RendererCommonValues.MaterialType.BackDistortion && r.distortionIntensity.HasValue)
					{
						node.RendererCommonValues.DistortionIntensity.SetValue(r.distortionIntensity.Value);
					}
				}
				else
				{
					losses.Add(where, "materialType '" + r.materialType + "' is not an Effekseer material type; left at Default");
				}
			}

			if (!string.IsNullOrEmpty(r.alphaBlend))
			{
				if (Enum.TryParse<AlphaBlendType>(r.alphaBlend, out var blend))
				{
					node.RendererCommonValues.AlphaBlend.SetValue(blend);
				}
				else
				{
					losses.Add(where, "alphaBlend '" + r.alphaBlend + "' is not one of Opacity/Blend/Add/Sub/Mul");
				}
			}

			if (r.fadeOut != null && !string.IsNullOrEmpty(r.fadeOut.type))
			{
				if (Enum.TryParse<RendererCommonValues.FadeOutMethod>(r.fadeOut.type, out var fom))
				{
					node.RendererCommonValues.FadeOutType.SetValue(fom);
					node.RendererCommonValues.FadeOut.Frame.SetValue(r.fadeOut.frame);
				}
				else
				{
					losses.Add(where, "fadeOut.type '" + r.fadeOut.type + "' is not an Effekseer fade-out method");
				}
			}

			string color = Resolve(r.colorTexture, where, "colorTexture", losses, outDir, docDir);
			if (color != null) node.RendererCommonValues.ColorTexture.SetAbsolutePath(color);

			string alpha = Resolve(r.alphaTexture, where, "alphaTexture", losses, outDir, docDir);
			if (alpha != null) node.AdvancedRendererCommonValuesValues.AlphaTextureParam.Texture.SetAbsolutePath(alpha);

			// After the texture, because the UV rectangle is measured against it.
			ApplyUv(r.uv, node, where, losses, color);
		}

		/// <summary>
		/// Effekseer measures UV rectangles in texels and its exporter divides them
		/// by the real texture size, so the rectangle cannot be written without
		/// knowing that size. The document carries a grid instead and the cell is
		/// worked out here, against the texture that was actually copied.
		/// </summary>
		static void ApplyUv(UvSpec uv, Node node, string where, Losses losses, string texturePath)
		{
			if (uv == null || string.IsNullOrEmpty(uv.type)) return;

			if (!Enum.TryParse<RendererCommonValues.UVType>(uv.type, out var type))
			{
				losses.Add(where, "uv.type '" + uv.type + "' is not an Effekseer UV mode; left at Default");
				return;
			}

			if (type == RendererCommonValues.UVType.Default)
			{
				node.RendererCommonValues.UV.SetValue(type);
				return;
			}

			int texW, texH;
			if (!TextureSize(texturePath, out texW, out texH))
			{
				losses.Add(where, "uv." + uv.type + " needs the texture's pixel size and '"
					+ (texturePath == null ? "<none>" : Path.GetFileName(texturePath))
					+ "' could not be measured, so the UV mode was left at Default and the sheet will not play");
				return;
			}

			node.RendererCommonValues.UV.SetValue(type);

			switch (type)
			{
				case RendererCommonValues.UVType.Animation:
				{
					var a = uv.animation ?? new UvAnimationSpec();
					int cellsX = Math.Max(1, a.frameCountX);
					int cellsY = Math.Max(1, a.frameCountY);

					var p = node.RendererCommonValues.UVAnimation.AnimationParams;
					p.FrameCountX.SetValue(cellsX);
					p.FrameCountY.SetValue(cellsY);

					// The sheet fills the texture, so one cell is the texture divided
					// by the grid. These are texels; the exporter normalises them.
					p.Start.X.SetValue(0);
					p.Start.Y.SetValue(0);
					p.Size.X.SetValue((float)texW / cellsX);
					p.Size.Y.SetValue((float)texH / cellsY);

					p.FrameLength.Value.SetValue(Math.Max(1, a.frameLength ?? 1));

					if (!string.IsNullOrEmpty(a.loopType)
						&& Enum.TryParse<RendererCommonValues.LoopType>(a.loopType, out var loop))
						p.LoopType.SetValue(loop);

					if (a.startSheet != null)
					{
						p.StartSheet.SetMin((int)Math.Round(a.startSheet.Min));
						p.StartSheet.SetMax((int)Math.Round(a.startSheet.Max));
					}

					if (!string.IsNullOrEmpty(a.interpolation)
						&& Enum.TryParse<RendererCommonValues.FlipbookInterpolationType>(a.interpolation, out var blend))
						node.RendererCommonValues.UVAnimation.FlipbookInterpolationType.SetValue(blend);

					if (texW % cellsX != 0 || texH % cellsY != 0)
						losses.Add(where, "a " + cellsX + "x" + cellsY + " sheet does not divide a "
							+ texW + "x" + texH + " texture evenly, so the cells will not land on pixel boundaries");
					break;
				}

				case RendererCommonValues.UVType.Scroll:
				{
					var s = uv.scroll ?? new UvScrollSpec();
					var p = node.RendererCommonValues.UVScroll;

					// The window is the whole texture. Leaving Size at its default of
					// zero would scroll an empty rectangle and draw nothing at all.
					SetVector2(p.Start, 0, 0);
					SetVector2(p.Size, texW, texH);
					SetVector2(p.Speed, (s.speedU ?? 0) * texW, (s.speedV ?? 0) * texH);
					break;
				}

				default:
					losses.Add(where, "uv type " + type + " is accepted but its parameters are not mapped yet");
					break;
			}
		}

		/// <summary>
		/// Width and height from a PNG header: an eight-byte signature, then a
		/// length and the IHDR tag, then the two dimensions as big-endian 32-bit
		/// integers. Effekseer measures UV rectangles in texels, so a flipbook
		/// cannot be written without this.
		/// </summary>
		static bool TextureSize(string path, out int width, out int height)
		{
			width = height = 0;
			if (string.IsNullOrEmpty(path) || !File.Exists(path)) return false;

			try
			{
				var header = new byte[24];
				using (var file = File.OpenRead(path))
					if (file.Read(header, 0, 24) < 24) return false;

				if (header[0] != 0x89 || header[1] != 'P' || header[2] != 'N' || header[3] != 'G') return false;
				if (header[12] != 'I' || header[13] != 'H' || header[14] != 'D' || header[15] != 'R') return false;

				width = (header[16] << 24) | (header[17] << 16) | (header[18] << 8) | header[19];
				height = (header[20] << 24) | (header[21] << 16) | (header[22] << 8) | header[23];
				return width > 0 && height > 0;
			}
			catch
			{
				return false;
			}
		}

		/// <summary>
		/// Copy a referenced resource next to the output and return its absolute
		/// path, or null if there is nothing usable.
		///
		/// Effekseer stores resource paths absolute in memory and serializes them
		/// relative to the effect file, so the file has to exist beside the
		/// output for the path to resolve at runtime.
		/// </summary>
		static string Resolve(ResourceSpec res, string where, string slot, Losses losses, string outDir, string docDir)
		{
			if (res == null || string.IsNullOrWhiteSpace(res.file)) return null;

			if (res.source.ValueKind != JsonValueKind.Object)
			{
				losses.Add(where, slot + " '" + res.file + "' has no source block; provenance is required, so it was not converted");
				return null;
			}

			// The document's paths are relative to the document, which keeps a
			// hand-authored file portable.
			string src = Path.GetFullPath(Path.Combine(docDir, res.file));
			string dest = Path.Combine(outDir, res.file.Replace('/', Path.DirectorySeparatorChar));

			if (!File.Exists(src))
			{
				if (placeholderTexture == null || !File.Exists(placeholderTexture))
				{
					losses.Add(where, slot + " file '" + res.file + "' does not exist; the node has no texture and will draw nothing");
					return null;
				}

				// Keep the placeholder under its own obvious name rather than
				// the missing file's, so nobody later mistakes it for the real
				// art on the strength of the filename.
				string stand = Path.Combine(outDir, "Texture", "_PLACEHOLDER.png");
				Directory.CreateDirectory(Path.GetDirectoryName(stand));
				File.Copy(placeholderTexture, stand, true);
				losses.Add(where, slot + " file '" + res.file + "' does not exist; SUBSTITUTED Texture/_PLACEHOLDER.png, so what renders is not the source art");
				return Path.GetFullPath(stand);
			}

			Directory.CreateDirectory(Path.GetDirectoryName(dest));
			File.Copy(src, dest, true);
			return Path.GetFullPath(dest);
		}

		static void ApplyRenderer(RendererSpec r, Node node, string where, Losses losses)
		{
			if (r == null || string.IsNullOrEmpty(r.type)) return;

			if (!Enum.TryParse<RendererValues.ParamaterType>(r.type, out var type))
			{
				losses.Add(where, "renderer.type '" + r.type + "' is not an Effekseer renderer");
				return;
			}

			// Sprite is RendererValues' constructor default, so IO.cs omits the
			// element when it is written back. Its absence from the output is
			// expected and not a sign the value was lost.
			node.DrawingValues.Type.SetValue(type);

			if (type != RendererValues.ParamaterType.Sprite)
			{
				losses.Add(where, "renderer " + type + " is set but its parameters are not mapped yet; only Sprite is complete");
				return;
			}

			if (r.sprite == null) return;

			if (!string.IsNullOrEmpty(r.sprite.billboard)
				&& Enum.TryParse<RendererValues.BillboardType>(r.sprite.billboard, out var bb))
			{
				node.DrawingValues.Sprite.Billboard.SetValue(bb);
			}

			// Colour lives on DrawingValues.ColorAll, NOT on
			// DrawingValues.Sprite.ColorAll. The Sprite.ColorAll* properties are
			// pre-1610 leftovers marked [IO(Export = false)] in
			// Data/RendererValues.cs: writing them succeeds in memory and is
			// silently dropped on save, so every effect comes out white. The
			// live property is the StandardColor group at RendererValues.cs:188,
			// which is what the corpus files contain (<ColorAll><Fixed>).
			if (!string.IsNullOrEmpty(r.sprite.colorAll)
				&& Enum.TryParse<StandardColorType>(r.sprite.colorAll, out var ct))
			{
				node.DrawingValues.ColorAll.Type.SetValue(ct);
			}

			if (r.sprite.colorAll_Fixed != null && r.sprite.colorAll_Fixed.Length == 4)
			{
				var c = r.sprite.colorAll_Fixed;
				node.DrawingValues.ColorAll.Fixed.SetValue(c[0], c[1], c[2], c[3]);
			}

			// Easing is how a source colour ramp arrives. Each end is a
			// ColorWithRandom with no SetValue, so an exact colour is written by
			// pinning its min and max to the same value.
			if (r.sprite.colorAll_Easing != null)
			{
				var e = r.sprite.colorAll_Easing;

				if (e.start != null && e.start.Length == 4)
				{
					node.DrawingValues.ColorAll.Easing.Start.SetMin(e.start[0], e.start[1], e.start[2], e.start[3]);
					node.DrawingValues.ColorAll.Easing.Start.SetMax(e.start[0], e.start[1], e.start[2], e.start[3]);
				}

				if (e.end != null && e.end.Length == 4)
				{
					node.DrawingValues.ColorAll.Easing.End.SetMin(e.end[0], e.end[1], e.end[2], e.end[3]);
					node.DrawingValues.ColorAll.Easing.End.SetMax(e.end[0], e.end[1], e.end[2], e.end[3]);
				}

				if (node.DrawingValues.ColorAll.Type.Value != StandardColorType.Easing)
					losses.Add(where, "colorAll_Easing was given but colorAll is '"
						+ node.DrawingValues.ColorAll.Type.Value + "', so the ramp is stored and not used");
			}
		}

		// -------------------------------------------------------------- io

		static void SetRange(Effekseer.Data.Value.FloatWithRandom v, Range r)
		{
			v.SetCenter(r.Center);
			v.SetMax(r.Max);
			v.SetMin(r.Min);
		}

		static void SetRange(Effekseer.Data.Value.IntWithRandom v, Range r)
		{
			v.SetCenter((int)r.Center);
			v.SetMax((int)r.Max);
			v.SetMin((int)r.Min);
		}

		static void SetVector(Effekseer.Data.Value.Vector3DWithRandom v, Vec3Spec s)
		{
			if (s.x != null) SetRange(v.X, s.x);
			if (s.y != null) SetRange(v.Y, s.y);
			if (s.z != null) SetRange(v.Z, s.z);
		}

		/// <summary>
		/// The plain vector carries no randomness and has no setter of its own, so
		/// each component goes through its own Float. A range collapses to its
		/// centre, which is all a fixed position can hold.
		/// </summary>
		static void SetVector(Effekseer.Data.Value.Vector3D v, Vec3Spec s)
		{
			if (s.x != null) v.X.SetValue(s.x.Center);
			if (s.y != null) v.Y.SetValue(s.y.Center);
			if (s.z != null) v.Z.SetValue(s.z.Center);
		}

		/// <summary>A two-component value pinned to one exact pair, with no spread.</summary>
		static void SetVector2(Effekseer.Data.Value.Vector2DWithRandom v, float x, float y)
		{
			v.X.SetCenter(x); v.X.SetMax(x); v.X.SetMin(x);
			v.Y.SetCenter(y); v.Y.SetMax(y); v.Y.SetMin(y);
		}

		static float Center(Range r) => r?.Center ?? 0.0f;

		/// <summary>
		/// The conversion report. Written next to the effect because the list of
		/// what did not come across is part of the deliverable, and because a
		/// folder of PNGs with no record of where they came from is exactly the
		/// archaeology the provenance rule exists to prevent.
		/// </summary>
		static void WriteReport(Document doc, Losses losses, string outPath)
		{
			string path = Path.ChangeExtension(outPath, ".conversion.md");
			using (var w = new StreamWriter(path))
			{
				w.WriteLine("# Conversion report: " + (doc.effect?.name ?? "(unnamed)"));
				w.WriteLine();
				w.WriteLine("| | |");
				w.WriteLine("|---|---|");
				w.WriteLine("| Source system | " + doc.provenance.sourceSystem + " |");
				w.WriteLine("| Source engine | " + doc.provenance.sourceEngine + " |");
				w.WriteLine("| Extractor | " + doc.provenance.extractor + " |");
				w.WriteLine("| Extracted | " + doc.provenance.extractedAt + " |");
				w.WriteLine("| Output | " + Path.GetFileName(outPath) + " |");
				if (!string.IsNullOrWhiteSpace(doc.provenance.licenceNote))
				{
					w.WriteLine();
					w.WriteLine("**Licence.** " + doc.provenance.licenceNote);
				}
				if (!string.IsNullOrWhiteSpace(doc.provenance.note))
				{
					w.WriteLine();
					w.WriteLine("**Note.** " + doc.provenance.note);
				}

				w.WriteLine();
				w.WriteLine("## Declared by the extractor");
				w.WriteLine();
				if (doc.unsupported == null || doc.unsupported.Count == 0)
				{
					w.WriteLine("Nothing.");
				}
				else
				{
					foreach (var u in doc.unsupported)
					{
						w.WriteLine("- **" + u.item + "** (" + u.kind + "). " + u.reason);
					}
				}

				w.WriteLine();
				w.WriteLine("## Found by the mapper");
				w.WriteLine();
				if (losses.Items.Count == 0)
				{
					w.WriteLine("Nothing. Every field in the document was honoured.");
				}
				else
				{
					foreach (var l in losses.Items) w.WriteLine("- " + l);
				}
			}
			Console.WriteLine("  report        {0}", path);
		}

		/// <summary>
		/// Read the file back through Effekseer's own loader. Decoding the
		/// container proves it is well-formed; this proves the editor accepts it
		/// and recovers what was written.
		///
		/// Compare against the document, never against the in-memory tree:
		/// EffekseerCore carries static state that survives Core.New(), so a
		/// second load in the same process can yield a subtly different tree even
		/// when the file on disk is correct.
		/// </summary>
		static int Verify(string outPath, Document doc)
		{
			Console.WriteLine();
			Console.WriteLine("--- verify ---");

			Core.New();
			if (!Core.LoadFrom(outPath))
			{
				Console.Error.WriteLine("  FAIL: Core.LoadFrom() rejected the file just written");
				return 1;
			}

			int expected = CountNodes(doc.nodes);
			int actual = CountLoaded(Core.Root);
			Console.WriteLine("  nodes         {0} (expected {1})", actual, expected);

			bool ok = actual == expected;

			var first = Core.Root.Children.Count > 0 ? Core.Root.Children[0] as Node : null;
			if (first != null)
			{
				string tex = first.RendererCommonValues.ColorTexture.RelativePath;
				Console.WriteLine("  first node    {0}", first.Name.Value);
				Console.WriteLine("  texture       {0}", string.IsNullOrEmpty(tex) ? "<none>" : tex);
			}

			Console.WriteLine(ok ? "  VERIFY OK" : "  VERIFY MISMATCH");
			return ok ? 0 : 1;
		}

		// ChildrenCollection is not IEnumerable, only Count plus an indexer, so
		// this walks it by index rather than with LINQ.
		static int CountLoaded(NodeBase n)
		{
			int total = 0;
			for (int i = 0; i < n.Children.Count; i++) total += 1 + CountLoaded(n.Children[i]);
			return total;
		}
	}
}
