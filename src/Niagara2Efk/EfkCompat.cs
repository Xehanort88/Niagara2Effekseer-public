using System;
using System.Collections.Generic;
using Effekseer.Data;

namespace Niagara2Efk
{
	/// <summary>
	/// The few places where EffekseerCore 170e and 1.80.3 disagree, so the same
	/// source builds against both (src/Niagara2Efk for 170e, src/Niagara2Efk1803
	/// with EFK18 for 1.80.3).
	///
	/// 1.8 moved six CommonValues settings into the new Generation and Removal
	/// groups and kept the flat 1.7 properties as [IO(Import = true, Export =
	/// false)] shims: writing them succeeds in memory and is silently dropped on
	/// save. Every read or write of those settings goes through here, and edit-spec
	/// paths are translated, so a spec written with either spelling works on both
	/// builds.
	/// </summary>
	static class EfkCompat
	{
#if EFK18
		public const string CoreVersion = "1.80.3";
		public static Effekseer.Data.Value.FloatWithRandom GenerationTime(CommonValues cv) { return cv.Generation.GenerationTime; }
		public static Effekseer.Data.Value.FloatWithRandom GenerationTimeOffset(CommonValues cv) { return cv.Generation.GenerationTimeOffset; }
		public static Effekseer.Data.Value.Boolean RemoveWhenLifeIsExtinct(CommonValues cv) { return cv.Removal.WhenLifeIsExtinct; }
		public static Effekseer.Data.Value.Boolean RemoveWhenParentIsRemoved(CommonValues cv) { return cv.Removal.WhenParentIsRemoved; }
		public static Effekseer.Data.Value.Boolean RemoveWhenAllChildrenAreRemoved(CommonValues cv) { return cv.Removal.WhenAllChildrenAreRemoved; }
#else
		public const string CoreVersion = "1.70e";
		public static Effekseer.Data.Value.FloatWithRandom GenerationTime(CommonValues cv) { return cv.GenerationTime; }
		public static Effekseer.Data.Value.FloatWithRandom GenerationTimeOffset(CommonValues cv) { return cv.GenerationTimeOffset; }
		public static Effekseer.Data.Value.Boolean RemoveWhenLifeIsExtinct(CommonValues cv) { return cv.RemoveWhenLifeIsExtinct; }
		public static Effekseer.Data.Value.Boolean RemoveWhenParentIsRemoved(CommonValues cv) { return cv.RemoveWhenParentIsRemoved; }
		public static Effekseer.Data.Value.Boolean RemoveWhenAllChildrenAreRemoved(CommonValues cv) { return cv.RemoveWhenAllChildrenAreRemoved; }
#endif

		// 1.7 spelling -> 1.8 spelling, as edit-spec property paths.
		static readonly string[,] Moved =
		{
			{ "CommonValues/GenerationTime", "CommonValues/Generation/GenerationTime" },
			{ "CommonValues/GenerationTimeOffset", "CommonValues/Generation/GenerationTimeOffset" },
			{ "CommonValues/RemoveWhenLifeIsExtinct", "CommonValues/Removal/WhenLifeIsExtinct" },
			{ "CommonValues/RemoveWhenParentIsRemoved", "CommonValues/Removal/WhenParentIsRemoved" },
			{ "CommonValues/RemoveWhenAllChildrenAreRemoved", "CommonValues/Removal/WhenAllChildrenAreRemoved" },
			{ "CommonValues/TriggerParam/ToStartGeneration", "CommonValues/Generation/ToStartGeneration" },
			{ "CommonValues/TriggerParam/ToStopGeneration", "CommonValues/Generation/ToStopGeneration" },
			{ "CommonValues/TriggerParam/ToRemove", "CommonValues/Removal/TriggerToRemove" },
		};

		/// <summary>
		/// Rewrite an edit-spec property path into the spelling this build's core
		/// saves. Matches whole path segments, so "CommonValues/GenerationTime/Max"
		/// becomes "CommonValues/Generation/GenerationTime/Max" on 1.8 and a 1.8
		/// spelling is turned back on 170e.
		/// </summary>
		public static string NormalizePath(string key)
		{
			if (string.IsNullOrEmpty(key)) return key;
			string k = key.Replace('.', '/');
			for (int i = 0; i < Moved.GetLength(0); i++)
			{
#if EFK18
				string from = Moved[i, 0], to = Moved[i, 1];
#else
				string from = Moved[i, 1], to = Moved[i, 0];
#endif
				if (k == from || k.StartsWith(from + "/", StringComparison.Ordinal))
					return to + k.Substring(from.Length);
			}
			return key;
		}
	}
}
