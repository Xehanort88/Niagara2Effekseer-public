using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Effekseer;
using Effekseer.Data;
using Effekseer.Data.Value;
using Path = System.IO.Path;

namespace Niagara2Efk
{
	/// <summary>Read-only curve checks. Each file is loaded in a fresh process:
	/// Core.New does not clear every piece of Effekseer's static project state.</summary>
	static class Lint
	{
		static readonly JsonSerializerOptions Json = new JsonSerializerOptions { WriteIndented = true };
		const int CacheSchema = 1;
		// How the runtime plays a curve whose key span is not a multiple of Sampling (a short tail).
		// 1.80.3 (and 1.70e) spread the short last segment over a full Sampling, then snap to the
		// last key; 1.80.7 (upstream #1164) plays it at its real length. 1.80.7 is the default:
		// every game project and the RPGReactor source runtime play it since 2026-10-17. 1.80.3
		// finds a superset (it adds short tails), for a 1.80.3 runtime or the 1.80.3 editor preview.
		const string DefaultRuntime = "1.80.7";
		static readonly string[] Runtimes = { "1.80.3", "1.80.7" };
		static bool PlaysShortTail(string runtime) => runtime == "1.80.7";
		public class Key
		{
			public int Frame { get; set; }
			public float Value { get; set; }
			public float LeftX { get; set; }
			public float LeftY { get; set; }
			public float RightX { get; set; }
			public float RightY { get; set; }
			public string Interpolation { get; set; }
		}
		public class Difference
		{
			public int Frame { get; set; }
			public double Intended { get; set; }
			public double Played { get; set; }
			public double AbsoluteDifference { get; set; }
		}
		public class Curve
		{
			public string NodePath { get; set; }
			public string NodeName { get; set; }
			public string CurvePath { get; set; }
			public string Timeline { get; set; }
			public int Sampling { get; set; }
			public float OffsetMin { get; set; }
			public float OffsetMax { get; set; }
			public List<Key> Keys { get; set; } = new List<Key>();
			public string Signature { get; set; }
			public List<int> OffGridKeys { get; set; } = new List<int>();
			public List<int> PercentOutsideKeys { get; set; } = new List<int>();
			/// <summary>Length of the last segment when the key span is not a multiple of
			/// Sampling, else 0. Its last key is in OffGridKeys only under a runtime that plays
			/// that segment wrongly (1.80.3/1.70e), never under 1.80.7.</summary>
			public int ShortTail { get; set; }
			public Difference LargestDifference { get; set; }
			public List<Difference> OffGridValues { get; set; } = new List<Difference>();
			public string EvaluationError { get; set; }
			public string Origin { get; set; }
			public string PackSource { get; set; }
		}
		public class Effect
		{
			public string File { get; set; }
			public string Reader { get; set; }
			public string Error { get; set; }
			public List<Curve> Curves { get; set; } = new List<Curve>();
		}
		public class CacheEntry
		{
			public long Size { get; set; }
			public long MtimeUtcTicks { get; set; }
			public string Reader { get; set; }
			public string Error { get; set; }
			public List<string> Signatures { get; set; } = new List<string>();
		}
		public class Cache
		{
			public int Schema { get; set; }
			public string Readers { get; set; }
			public Dictionary<string, CacheEntry> Files { get; set; } = new Dictionary<string, CacheEntry>();
		}
		public class Report
		{
			public string Build { get; set; } = EfkCompat.CoreVersion;
			/// <summary>The runtime whose curve playback the findings model (--runtime).</summary>
			public string Runtime { get; set; }
			public string Catalogue { get; set; }
			public string CacheFile { get; set; }
			public int CacheHits { get; set; }
			public int CacheMisses { get; set; }
			public List<Effect> Effects { get; set; } = new List<Effect>();
			public List<Effect> UnreadableCorpusFiles { get; set; } = new List<Effect>();
			/// <summary>Files in the comparison corpus (readable or not).</summary>
			public int CorpusFiles { get; set; }
			public int OursFindings { get; set; }
			public int PackFindings { get; set; }
			// Complete is false when any target effect could not be read (or none
			// was found): the findings then cover only the readable ones.
			public int TargetEffects { get; set; }
			public int UnreadableEffects { get; set; }
			public bool Complete { get; set; }
			public bool Strict { get; set; }
		}

		static string F(double value) => value.ToString("G9", CultureInfo.InvariantCulture);
		static bool HasFinding(Curve c) => c.OffGridKeys.Count > 0 || c.PercentOutsideKeys.Count > 0;
		static string Signature(Curve c)
		{
			// The brief's identity is keys + Sampling + Timeline, independent of
			// node name/path. Keys include interpolation and both Bezier handles.
			byte[] bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { c.Timeline, c.Sampling, c.Keys }));
			return Convert.ToHexString(SHA256.HashData(bytes));
		}

		static Curve Describe(IFCurve source, string nodePath, string name, string path, string timeline, bool analyse, string runtime)
		{
			var keys = source.Keys.OrderBy(k => k.Frame).ToArray();
			var c = new Curve { NodePath = nodePath, NodeName = name, CurvePath = path,
				Timeline = timeline, Sampling = source.Sampling.Value,
				OffsetMin = source.OffsetMin.Value, OffsetMax = source.OffsetMax.Value,
				Keys = keys.Select(k => new Key { Frame = k.Frame, Value = k.ValueAsFloat,
					LeftX = k.LeftX, LeftY = k.LeftY, RightX = k.RightX, RightY = k.RightY,
					Interpolation = k.InterpolationType.ToString() }).ToList() };
			c.Signature = Signature(c);
			if (!analyse) return c;
			int first = keys[0].Frame, last = keys[keys.Length - 1].Frame;
			bool playsTail = PlaysShortTail(runtime);
			if (c.Sampling > 1)
			{
				c.ShortTail = (int)(((long)last - first) % c.Sampling);
				// The last key is always a stored sample; only a runtime that stretches the
				// short segment before it plays that segment wrongly.
				c.OffGridKeys = keys.Where(k => ((long)k.Frame - first) % c.Sampling != 0 &&
					!(playsTail && k.Frame == last)).Select(k => k.Frame).ToList();
			}
			if (timeline == "Percent")
				c.PercentOutsideKeys = keys.Where(k => k.Frame < 0 || k.Frame > 100).Select(k => k.Frame).ToList();
			if (c.OffGridKeys.Count == 0) return c;
			try
			{
				// What the runtime plays: GetBytes samples GetValue on its grid and
				// appends the last key; FCurve::GetValue (Effekseer.FCurves.cpp)
				// draws lines between samples. In 1.80.3/1.70e its shortened-last-
				// interval branch (ind == keys_.size() - 1) is unreachable, so the final
				// segment is spread over a full Sampling and snaps to the last key at its
				// frame; 1.80.7 tests ind == keys_.size() - 2 and divides by the segment's
				// real length (len_ - ind * freq_).
				// Compare every integer curve unit, the domain GetValue accepts.
				var frames = new List<int>();
				var samples = new List<float>();
				for (long frame = first; frame < last; frame += c.Sampling)
				{ frames.Add((int)frame); samples.Add(source.GetValue((int)frame)); }
				frames.Add(last); samples.Add(source.GetValue(last));
				int segment = 0;
				var off = new HashSet<int>(c.OffGridKeys);
				for (long frame = first; frame <= last; frame++)
				{
					while (segment + 1 < frames.Count - 1 && frame > frames[segment + 1]) segment++;
					double played = samples[segment];
					if (frame == last)
						played = samples[samples.Count - 1];
					else if (segment + 1 < frames.Count)
					{
						int length = playsTail && segment + 1 == frames.Count - 1 ? frames[segment + 1] - frames[segment] : c.Sampling;
						played += ((double)samples[segment + 1] - samples[segment]) *
							(frame - frames[segment]) / length;
					}
					double intended = source.GetValue((int)frame);
					if (!double.IsFinite(intended) || !double.IsFinite(played))
						throw new InvalidDataException("curve evaluator returned a non-finite value");
					var difference = new Difference { Frame = (int)frame, Intended = intended,
						Played = played, AbsoluteDifference = Math.Abs(intended - played) };
					if (c.LargestDifference == null || difference.AbsoluteDifference > c.LargestDifference.AbsoluteDifference)
						c.LargestDifference = difference;
					if (off.Contains((int)frame)) c.OffGridValues.Add(difference);
				}
			}
			catch (Exception e) { c.EvaluationError = e.Message; }
			return c;
		}

		static void Curves(object value, string prefix, int depth, HashSet<object> seen,
			string nodePath, string name, List<Curve> into, bool analyse, string runtime)
		{
			if (value == null || depth > 8 || !seen.Add(value)) return;
			var field = value.GetType().GetField("Timeline", BindingFlags.Public | BindingFlags.Instance);
			object timelineValue = field?.GetValue(value);
			string timeline = timelineValue?.GetType().GetProperty("Value")?.GetValue(timelineValue)?.ToString();
			foreach (var p in value.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
			{
				if (p.GetIndexParameters().Length > 0 || p.Name == "Children" || p.Name == "Parent") continue;
				var io = p.GetCustomAttributes(typeof(IOAttribute), true).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export) continue;
				object child;
				try { child = p.GetValue(value); } catch { continue; }
				if (child == null) continue;
				string path = prefix.Length == 0 ? p.Name : prefix + "/" + p.Name;
				if (child is IFCurve curve)
				{
					if (curve.Keys.Any()) into.Add(Describe(curve, nodePath, name, path, timeline, analyse, runtime));
					continue;
				}
				var type = child.GetType();
				if (type.IsPrimitive || child is string || type.IsEnum) continue;
				if (type.Namespace != null && type.Namespace.StartsWith("Effekseer", StringComparison.Ordinal))
					Curves(child, path, depth + 1, seen, nodePath, name, into, analyse, runtime);
			}
		}
		static void Nodes(NodeBase parent, string prefix, List<Curve> into, bool analyse, string runtime)
		{
			for (int i = 0; i < parent.Children.Count; i++)
			{
				var node = parent.Children[i];
				string path = prefix.Length == 0 ? i.ToString(CultureInfo.InvariantCulture) : prefix + "/" + i;
				Curves(node, "", 0, new HashSet<object>(ReferenceEqualityComparer.Instance), path, node.Name.Value, into, analyse, runtime);
				Nodes(node, path, into, analyse, runtime);
			}
		}

		// Internal protocol for isolated loading, also used by the other build.
		// It emits JSON on stdout, never calls the public command or spawns again.
		public static int RunWorker(string[] args)
		{
			// file, mode, runtime (the runtime only changes what "analyse" reports).
			if (args.Length != 3 || (args[1] != "analyse" && args[1] != "signatures") || !Runtimes.Contains(args[2])) return 2;
			var result = new Effect { File = Path.GetFullPath(args[0]), Reader = EfkCompat.CoreVersion };
			string log = Path.Combine(Path.GetTempPath(), "niagara2efk-lint-" + Guid.NewGuid().ToString("N") + ".log");
			// Upstream diagnostics must not corrupt the stdout JSON protocol.
			TextWriter stdout = Console.Out;
			Console.SetOut(new StringWriter(CultureInfo.InvariantCulture));
			try
			{
				Effekseer.Utils.Logger.LogPath = log;
				Core.New();
				if (!Core.LoadFrom(result.File)) result.Error = "Core.LoadFrom rejected the file";
				else Nodes(Core.Root, "", result.Curves, args[1] == "analyse", args[2]);
			}
			catch (Exception e)
			{
				while (e.InnerException != null) e = e.InnerException;
				result.Error = e.GetType().Name + ": " + e.Message;
				result.Curves.Clear();
			}
			finally
			{
				Console.SetOut(stdout);
				try { if (File.Exists(log)) File.Delete(log); } catch { }
			}
			Console.WriteLine(JsonSerializer.Serialize(result));
			return 0;
		}

		static string OwnDll => Assembly.GetExecutingAssembly().Location;
		static string OtherDll()
		{
			string project = EfkCompat.CoreVersion == "1.70e" ? "Niagara2Efk1803" : "Niagara2Efk";
			// Both regular project builds and dotnet --artifacts-path layouts.
			for (var directory = new DirectoryInfo(Path.GetDirectoryName(OwnDll)); directory != null; directory = directory.Parent)
			{
				foreach (string relative in new[] { Path.Combine(project, "release", "Niagara2Efk.dll"),
					Path.Combine("src", project, "bin", "x64", "Release", "net9.0", "Niagara2Efk.dll"),
					Path.Combine("src", project, "bin", "Release", "net9.0", "Niagara2Efk.dll") })
				{
					string file = Path.Combine(directory.FullName, relative);
					if (File.Exists(file) && file != OwnDll) return file;
				}
			}
			return null;
		}
		static Effect Invoke(string dll, string file, bool analyse, string runtime)
		{
			try
			{
				var start = new ProcessStartInfo("dotnet") { UseShellExecute = false,
					RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
				start.ArgumentList.Add(dll); start.ArgumentList.Add("lint-internal");
				start.ArgumentList.Add(file); start.ArgumentList.Add(analyse ? "analyse" : "signatures");
				start.ArgumentList.Add(runtime);
				using var process = Process.Start(start);
				Task<string> stdout = process.StandardOutput.ReadToEndAsync();
				Task<string> stderr = process.StandardError.ReadToEndAsync();
				if (!process.WaitForExit(60000))
				{
					// Only this exact worker was started by this command.
					process.Kill(entireProcessTree: true); process.WaitForExit();
					return new Effect { File = file, Error = "lint reader timed out after 60 seconds" };
				}
				Task.WaitAll(stdout, stderr);
				if (process.ExitCode != 0) return new Effect { File = file, Error = "reader exit " + process.ExitCode + ": " + stderr.Result.Trim() };
				return JsonSerializer.Deserialize<Effect>(stdout.Result) ?? new Effect { File = file, Error = "empty reader result" };
			}
			catch (Exception e) { return new Effect { File = file, Error = "reader: " + e.Message }; }
		}
		static Effect Read(string file, bool analyse, string other, string runtime)
		{
			var result = Invoke(OwnDll, file, analyse, runtime);
			if (result.Error == null || other == null) return result;
			var fallback = Invoke(other, file, analyse, runtime);
			if (fallback.Error == null) return fallback;
			result.Error += "; other build: " + fallback.Error;
			return result;
		}

		static void SaveJson<T>(string file, T data)
		{
			Directory.CreateDirectory(Path.GetDirectoryName(file));
			// Unique temporary file permits concurrent lint commands without truncation.
			string temporary = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
			try { File.WriteAllText(temporary, JsonSerializer.Serialize(data, Json)); File.Move(temporary, file, true); }
			finally { if (File.Exists(temporary)) File.Delete(temporary); }
		}
		static Dictionary<string, string> Corpus(string catalogue, string cacheFile, string outputJson, string other, Report report)
		{
			using var document = JsonDocument.Parse(File.ReadAllText(catalogue));
			var paths = document.RootElement.GetProperty("effects").EnumerateArray()
				.Where(e => e.TryGetProperty("path", out _))
				.Select(e => Path.GetFullPath(e.GetProperty("path").GetString(), Path.GetDirectoryName(catalogue)))
				.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(p => p, StringComparer.Ordinal).ToArray();
			if (paths.Contains(cacheFile, StringComparer.OrdinalIgnoreCase) ||
				(outputJson != null && paths.Contains(outputJson, StringComparer.OrdinalIgnoreCase)))
				throw new ArgumentException("JSON/cache output must not overwrite a corpus effect");
			string readers = "schema" + CacheSchema + ":" + EfkCompat.CoreVersion;
			foreach (string dll in new[] { OwnDll, other }.Where(p => p != null))
			{ var info = new FileInfo(dll); readers += ":" + dll + ":" + info.Length + ":" + info.LastWriteTimeUtc.Ticks; }
			Cache previous = null;
			if (File.Exists(cacheFile))
			{
				try { previous = JsonSerializer.Deserialize<Cache>(File.ReadAllText(cacheFile)); }
				catch (Exception e) { Console.Error.WriteLine("lint: ignoring unreadable cache: " + e.Message); }
			}
			if (previous?.Schema != CacheSchema || previous.Readers != readers || previous.Files == null) previous = new Cache();
			var entries = new CacheEntry[paths.Length];
			int hits = 0, misses = 0;
			Parallel.For(0, paths.Length, new ParallelOptions { MaxDegreeOfParallelism = 4 }, i =>
			{
				string path = paths[i]; var info = new FileInfo(path);
				try
				{
					if (info.Exists && previous.Files.TryGetValue(path, out var old) && old.Size == info.Length && old.MtimeUtcTicks == info.LastWriteTimeUtc.Ticks)
					{ entries[i] = old; System.Threading.Interlocked.Increment(ref hits); return; }
					System.Threading.Interlocked.Increment(ref misses);
					// Signatures do not depend on the runtime.
					var effect = info.Exists ? Read(path, false, other, DefaultRuntime) : new Effect { Error = "file not found" };
					entries[i] = new CacheEntry { Size = info.Exists ? info.Length : -1,
						MtimeUtcTicks = info.Exists ? info.LastWriteTimeUtc.Ticks : 0,
						Reader = effect.Reader, Error = effect.Error,
						Signatures = effect.Curves.Select(c => c.Signature).Distinct().ToList() };
				}
				catch (Exception e) { entries[i] = new CacheEntry { Size = -1, Error = e.Message }; }
			});
			var cache = new Cache { Schema = CacheSchema, Readers = readers };
			var signatures = new Dictionary<string, string>(StringComparer.Ordinal);
			for (int i = 0; i < paths.Length; i++)
			{
				var entry = entries[i]; cache.Files[paths[i]] = entry;
				if (entry.Error != null) report.UnreadableCorpusFiles.Add(new Effect { File = paths[i], Reader = entry.Reader, Error = entry.Error });
				foreach (string signature in entry.Signatures) signatures.TryAdd(signature, paths[i]);
			}
			SaveJson(cacheFile, cache);
			report.CacheHits = hits; report.CacheMisses = misses;
			report.CorpusFiles = paths.Length;
			Console.WriteLine("Corpus: {0} files, {1} signatures, cache {2} hit / {3} miss, {4} unreadable", paths.Length, signatures.Count, hits, misses, report.UnreadableCorpusFiles.Count);
			foreach (var e in report.UnreadableCorpusFiles) Console.WriteLine("UNREADABLE corpus: " + e.File + " -- " + e.Error);
			return signatures;
		}

		public static int Run(string[] args)
		{
			string target = null, catalogue = null, json = null, runtime = null;
			bool strict = false;
			for (int i = 0; i < args.Length; i++)
			{
				if (i == 0 && !args[i].StartsWith("--", StringComparison.Ordinal)) { target = args[i]; continue; }
				if (args[i] == "--strict" && !strict) { strict = true; continue; }
				if ((args[i] == "--catalogue" && catalogue == null) || (args[i] == "--json" && json == null) || (args[i] == "--runtime" && runtime == null))
				{
					if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal)) return Usage("missing value for " + args[i]);
					if (args[i] == "--catalogue") catalogue = args[++i]; else if (args[i] == "--json") json = args[++i]; else runtime = args[++i];
				}
				else return Usage("unknown or duplicate argument: " + args[i]);
			}
			if (target == null) return Usage("an effect or folder is required");
			runtime ??= DefaultRuntime;
			if (!Runtimes.Contains(runtime)) return Usage("unknown runtime " + runtime + " (expected " + string.Join(" or ", Runtimes) + ")");
			try
			{
				target = Path.GetFullPath(target);
				if (!File.Exists(target) && !Directory.Exists(target)) return Usage("not found: " + target);
				if (catalogue != null) { catalogue = Path.GetFullPath(catalogue); if (!File.Exists(catalogue)) return Usage("catalogue not found: " + catalogue); }
				if (json != null) json = Path.GetFullPath(json);
				if (json != null && (string.Equals(json, target, StringComparison.OrdinalIgnoreCase) || string.Equals(json, catalogue, StringComparison.OrdinalIgnoreCase))) return Usage("JSON output must not overwrite the input or catalogue");
				string[] files = Directory.Exists(target) ? Directory.EnumerateFiles(target, "*", SearchOption.AllDirectories)
					.Where(p => Path.GetExtension(p).Equals(".efkefc", StringComparison.OrdinalIgnoreCase)).OrderBy(p => p, StringComparer.Ordinal).ToArray() : new[] { target };
				if (json != null && files.Contains(json, StringComparer.OrdinalIgnoreCase)) return Usage("JSON output must not overwrite an effect");
				string cacheFile = catalogue == null ? null : Path.Combine(json == null ? Path.GetDirectoryName(catalogue) : Path.GetDirectoryName(json), "lint-cache.json");
				if (cacheFile != null && (string.Equals(cacheFile, catalogue, StringComparison.OrdinalIgnoreCase) || files.Contains(cacheFile, StringComparer.OrdinalIgnoreCase) || string.Equals(cacheFile, json, StringComparison.OrdinalIgnoreCase))) return Usage("cache path collides with an input or report");
				var report = new Report { Runtime = runtime, Catalogue = catalogue, CacheFile = cacheFile, Strict = strict };
				Console.WriteLine("Runtime: {0} ({1})", runtime, PlaysShortTail(runtime)
					? "a short last curve segment plays at its real length; a short tail alone is not a finding"
					: "a short last curve segment is spread over a full Sampling, then snaps; a short tail is a finding");
				string other = OtherDll();
				var signatures = catalogue == null ? new Dictionary<string, string>() : Corpus(catalogue, cacheFile, json, other, report);
				foreach (string file in files)
				{
					var effect = Read(file, true, other, runtime); report.Effects.Add(effect);
					if (effect.Error != null) { Console.WriteLine("UNREADABLE effect: " + file + " -- " + effect.Error); continue; }
					foreach (var curve in effect.Curves)
					{
						curve.Origin = signatures.TryGetValue(curve.Signature, out string pack) ? "pack" : "ours";
						curve.PackSource = pack;
					}
					var findings = effect.Curves.Where(HasFinding).ToList();
					int ours = findings.Count(c => c.Origin == "ours"), packs = findings.Count - ours;
					report.OursFindings += ours; report.PackFindings += packs;
					Console.WriteLine("{0}: {1} keyed curves, {2} ours / {3} pack findings", file, effect.Curves.Count, ours, packs);
					foreach (var c in findings)
					{
						string delta = c.LargestDifference == null ? "" : "; max difference " + F(c.LargestDifference.AbsoluteDifference) +
							" at " + c.LargestDifference.Frame + " (intended " + F(c.LargestDifference.Intended) + ", played " + F(c.LargestDifference.Played) + ")";
						Console.WriteLine("  {0} node {1} ({2}) {3}: Timeline {4}, Sampling {5}, off-grid [{6}], Percent outside [{7}]{8}{9}",
							c.Origin, c.NodePath, c.NodeName, c.CurvePath, c.Timeline, c.Sampling,
							string.Join(",", c.OffGridKeys), string.Join(",", c.PercentOutsideKeys), delta,
							c.EvaluationError == null ? "" : "; evaluation error: " + c.EvaluationError);
					}
				}
				report.TargetEffects = report.Effects.Count;
				report.UnreadableEffects = report.Effects.Count(e => e.Error != null);
				report.Complete = report.TargetEffects > 0 && report.UnreadableEffects == 0;
				if (json != null) SaveJson(json, report);
				Console.WriteLine("LINT: {0} effects, {1} ours / {2} pack findings, {3} unreadable effects (runtime {4})", report.Effects.Count, report.OursFindings, report.PackFindings, report.UnreadableEffects, runtime);
				// An unreadable comparison corpus (e.g. a sandbox that cannot see the corpus drive) was only on the
				// Corpus line, far above. Fewer pack signatures can only move findings from pack to ours, so a
				// clean result still stands and the exit status is unchanged; but say it at the end.
				if (report.UnreadableCorpusFiles.Count > 0)
					Console.WriteLine("LINT NOTE: comparison corpus incomplete ({0} of {1} files unreadable): pack findings may be counted as ours; a result with 0 ours findings still stands",
						report.UnreadableCorpusFiles.Count, report.CorpusFiles);
				if (!report.Complete)
				{
					// Unreadable effects stay non-fatal by default (exit 0 means no ours
					// findings among the effects that were read); say so plainly.
					Console.WriteLine(report.TargetEffects == 0
						? "LINT INCOMPLETE: no .efkefc effect found under " + target + "; nothing was checked"
						: "LINT INCOMPLETE: " + report.UnreadableEffects + " of " + report.TargetEffects + " target effects unreadable (UNREADABLE lines above); findings cover only the other " + (report.TargetEffects - report.UnreadableEffects));
					Console.WriteLine(strict ? "LINT: --strict, exit 3" : "LINT: an incomplete check does not change the exit status; use --strict to fail on it");
					if (strict) return 3;
				}
				return report.OursFindings > 0 ? 1 : 0;
			}
			catch (Exception e) when (e is IOException || e is UnauthorizedAccessException || e is JsonException || e is ArgumentException || e is InvalidOperationException || e is KeyNotFoundException)
			{ return Usage(e.Message); }
		}
		static int Usage(string error)
		{
			Console.Error.WriteLine("lint: " + error);
			Console.Error.WriteLine("usage: Niagara2Efk lint <effect.efkefc | folder> [--catalogue out/catalogue.json] [--json report.json] [--strict] [--runtime 1.80.3|1.80.7]");
			Console.Error.WriteLine("runtime: how the target project's runtime plays a short last curve segment; default " + DefaultRuntime + " ; use 1.80.3 for a 1.80.3 or 1.70e runtime");
			Console.Error.WriteLine("exit: 0 no ours findings, 1 ours findings, 2 usage error, 3 (--strict only) a target effect was unreadable or none was found");
			return 2;
		}
	}
}
