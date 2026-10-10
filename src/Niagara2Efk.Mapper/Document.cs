using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Niagara2Efk.Mapper
{
	/// <summary>
	/// The intermediate document, as defined in schema/intermediate-v1.md.
	///
	/// Deliberately dumb: this is a transcription of the JSON and nothing more.
	/// Every decision about what a value means lives in Mapper.cs, so that the
	/// schema can be read straight off this file.
	/// </summary>
	class Document
	{
		public int schemaVersion { get; set; }
		public Provenance provenance { get; set; }
		public EffectInfo effect { get; set; }
		public List<Unsupported> unsupported { get; set; } = new List<Unsupported>();
		public List<NodeSpec> nodes { get; set; } = new List<NodeSpec>();
	}

	class Provenance
	{
		public string sourceSystem { get; set; }
		public string sourceFile { get; set; }
		public string sourceEngine { get; set; }
		public string extractor { get; set; }
		public string extractedAt { get; set; }
		public string licenceNote { get; set; }
		public string note { get; set; }
	}

	class EffectInfo
	{
		public string name { get; set; }
		public float scale { get; set; } = 1.0f;
	}

	class Unsupported
	{
		public string item { get; set; }
		public string kind { get; set; }
		public string reason { get; set; }
	}

	class NodeSpec
	{
		public string name { get; set; }
		public List<NodeSpec> children { get; set; } = new List<NodeSpec>();
		public SourceInfo source { get; set; }

		public CommonSpec common { get; set; }
		public GenerationLocationSpec generationLocation { get; set; }
		public LocationSpec location { get; set; }
		public ScalingSpec scaling { get; set; }
		public RotationSpec rotation { get; set; }
		public float? depthOffset { get; set; }
		public List<ForceFieldSpec> forceFields { get; set; } = new List<ForceFieldSpec>();
		public RendererCommonSpec rendererCommon { get; set; }
		public RendererSpec renderer { get; set; }
	}

	class SourceInfo
	{
		public string emitter { get; set; }
		public List<string> modules { get; set; } = new List<string>();
	}

	/// <summary>
	/// A number that may be written either as a bare value or as a
	/// {center,min,max} triple, because Effekseer stores randomised values that
	/// way and a front end that only knows a constant should not have to fake a
	/// range. <see cref="RangeConverter"/> accepts both forms.
	/// </summary>
	[JsonConverter(typeof(RangeConverter))]
	class Range
	{
		public float Center;
		public float Min;
		public float Max;

		public static Range Of(float v) => new Range { Center = v, Min = v, Max = v };
	}

	class RangeConverter : JsonConverter<Range>
	{
		public override Range Read(ref Utf8JsonReader reader, System.Type t, JsonSerializerOptions o)
		{
			if (reader.TokenType == JsonTokenType.Number)
			{
				return Range.Of(reader.GetSingle());
			}

			if (reader.TokenType != JsonTokenType.StartObject)
			{
				throw new JsonException("a range must be a number or an object");
			}

			float? center = null, min = null, max = null;
			while (reader.Read() && reader.TokenType != JsonTokenType.EndObject)
			{
				string key = reader.GetString();
				reader.Read();
				float v = reader.GetSingle();
				if (key == "center") center = v;
				else if (key == "min") min = v;
				else if (key == "max") max = v;
			}

			// Only one of the three is strictly required. A document that gives
			// min and max but no center gets the midpoint, which is what an
			// author writing a range actually means.
			if (center == null && min != null && max != null) center = (min.Value + max.Value) * 0.5f;
			if (center == null) throw new JsonException("a range needs center, or min and max");

			return new Range
			{
				Center = center.Value,
				Min = min ?? center.Value,
				Max = max ?? center.Value,
			};
		}

		public override void Write(Utf8JsonWriter w, Range v, JsonSerializerOptions o)
		{
			w.WriteStartObject();
			w.WriteNumber("center", v.Center);
			w.WriteNumber("min", v.Min);
			w.WriteNumber("max", v.Max);
			w.WriteEndObject();
		}
	}

	class CommonSpec
	{
		public Range life { get; set; }

		// Either a number or the string "infinite", so it is read as a raw
		// element and interpreted in the mapper.
		public JsonElement maxGeneration { get; set; }

		public Range generationTime { get; set; }
		public Range generationTimeOffset { get; set; }
		public bool removeWhenLifeIsExtinct { get; set; } = true;
	}

	class GenerationLocationSpec
	{
		public string type { get; set; }
		public SphereSpec sphere { get; set; }
		public CircleSpec circle { get; set; }
		public PointSpec point { get; set; }
	}

	/// <summary>
	/// A sphere spawn. The rotations are not decoration: Effekseer places each
	/// particle at (radius, rotationX, rotationY), so leaving the angles at a
	/// fixed 0 stacks every particle on one point and the emitter appears to
	/// produce a single mote. The stock corpus uses -360..360 on both.
	/// </summary>
	class SphereSpec
	{
		public Range radius { get; set; }
		public Range rotationX { get; set; }
		public Range rotationY { get; set; }
	}
	/// <summary>
	/// A ring spawn. This is Effekseer's nearest shape to a source cylinder, so a
	/// document that names one has already accepted losing the cylinder's height.
	/// <see cref="axisDirection"/> is the ring's normal: a Z-up source becomes
	/// YAxis here.
	/// </summary>
	class CircleSpec
	{
		public string axisDirection { get; set; }
		public Range radius { get; set; }
		public Range angleStart { get; set; }
		public Range angleEnd { get; set; }
		public int? division { get; set; }
		public string type { get; set; }
	}

	class PointSpec { public Vec3Spec location { get; set; } }

	class Vec3Spec
	{
		public Range x { get; set; }
		public Range y { get; set; }
		public Range z { get; set; }
	}

	class LocationSpec
	{
		public string type { get; set; }

		// Named fixedOffset rather than fixed, which is a C# keyword. This is the
		// node's own position, which is where an emitter offset goes once a spawn
		// shape has claimed the generationLocation slot.
		public Vec3Spec fixedOffset { get; set; }
		public PvaSpec pva { get; set; }
	}

	class PvaSpec
	{
		// Effekseer's PVA carries the start position as well as the two
		// derivatives, so a node can hold its own offset and still move. Reaching
		// for the Fixed location type instead would force a choice between them.
		public Vec3Spec location { get; set; }
		public Vec3Spec velocity { get; set; }
		public Vec3Spec acceleration { get; set; }
	}

	class ScalingSpec
	{
		public string type { get; set; }
		public SinglePvaSpec singlePVA { get; set; }

		// A size that changes across the particle's life is two ends and a curve
		// between them, which is an easing rather than a constant rate of growth.
		public SingleEasingSpec singleEasing { get; set; }
		public Vec3Spec fixedScale { get; set; }
	}

	class SingleEasingSpec
	{
		public Range start { get; set; }
		public Range end { get; set; }
	}

	/// <summary>
	/// Mirrors <see cref="LocationSpec"/>. "fixed" is a C# keyword, so the fixed
	/// rotation is spelled out the same way the fixed offset is.
	/// </summary>
	class RotationSpec
	{
		public string type { get; set; }
		public Vec3Spec fixedRotation { get; set; }
		public RotationPvaSpec pva { get; set; }
	}

	class RotationPvaSpec
	{
		public Vec3Spec rotation { get; set; }
		public Vec3Spec velocity { get; set; }
		public Vec3Spec acceleration { get; set; }
	}

	class SinglePvaSpec
	{
		public Range scale { get; set; }
		public Range velocity { get; set; }
	}

	class ForceFieldSpec
	{
		public string type { get; set; }
		public float? power { get; set; }

		// Effekseer can turn a force field before it acts. Degrees, per axis.
		public Vec3Spec rotation { get; set; }
		public Vec3Spec gravity { get; set; }
		public TurbulenceSpec turbulence { get; set; }
	}

	class TurbulenceSpec
	{
		public float? fieldScale { get; set; }
		public int? octave { get; set; }
		public int? seed { get; set; }
	}

	class RendererCommonSpec
	{
		public string materialType { get; set; }
		public float? distortionIntensity { get; set; }
		public ResourceSpec colorTexture { get; set; }
		public ResourceSpec alphaTexture { get; set; }
		public string alphaBlend { get; set; }
		public FadeOutSpec fadeOut { get; set; }
		public UvSpec uv { get; set; }
	}

	/// <summary>
	/// How the node reads its texture.
	///
	/// Effekseer states UV rectangles in texels and its exporter divides them by
	/// the real texture size, so writing a rectangle here would tie the document
	/// to one resolution: reauthor the sheet at twice the size and every number
	/// would be wrong. This block carries the grid and the timing instead, and
	/// the mapper works the rectangle out from the texture it actually copied.
	/// </summary>
	class UvSpec
	{
		public string type { get; set; }             // Default | Fixed | Animation | Scroll | FCurve
		public UvAnimationSpec animation { get; set; }
		public UvScrollSpec scroll { get; set; }
	}

	/// <summary>
	/// A flipbook. The sheet is <see cref="frameCountX"/> by
	/// <see cref="frameCountY"/> cells filling the whole texture, and each cell is
	/// held for <see cref="frameLength"/> frames.
	/// </summary>
	class UvAnimationSpec
	{
		public int frameCountX { get; set; } = 1;
		public int frameCountY { get; set; } = 1;
		public int? frameLength { get; set; }
		public string loopType { get; set; }         // Once | Loop | ReverceLoop, Effekseer's own spelling
		public string interpolation { get; set; }    // None | Lerp
		public Range startSheet { get; set; }
	}

	/// <summary>Speeds are in textures per frame, so they survive a resize too.</summary>
	class UvScrollSpec
	{
		public float? speedU { get; set; }
		public float? speedV { get; set; }
	}

	/// <summary>
	/// A texture or model, with the provenance the schema requires. A resource
	/// without a <see cref="source"/> block is rejected rather than converted,
	/// because the whole point of recording provenance is that it cannot be
	/// reconstructed afterwards.
	/// </summary>
	class ResourceSpec
	{
		public string file { get; set; }
		public JsonElement source { get; set; }
	}

	class FadeOutSpec
	{
		public string type { get; set; }
		public float frame { get; set; }
	}

	class RendererSpec
	{
		public string type { get; set; }
		public SpriteSpec sprite { get; set; }
	}

	class SpriteSpec
	{
		public string billboard { get; set; }
		public string colorAll { get; set; }
		public int[] colorAll_Fixed { get; set; }
		public ColorEasingSpec colorAll_Easing { get; set; }
	}

	/// <summary>
	/// A colour that eases from one end to the other across the particle's life.
	/// Each end is [r, g, b, a] in 0..255. This is what a source colour ramp
	/// becomes: the two ends survive and the shape between them does not.
	/// </summary>
	class ColorEasingSpec
	{
		public int[] start { get; set; }
		public int[] end { get; set; }
	}
}
