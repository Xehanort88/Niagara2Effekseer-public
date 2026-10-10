using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using Effekseer;

namespace Niagara2Efk
{
	/// <summary>
	/// Accuracy check for the assembler, run against the real corpus.
	///
	/// The claim being tested is the strongest one available: grafting a part
	/// changes nothing the runtime can see. For every effect whose root has a
	/// single child, grafting that one child into a fresh project must reproduce
	/// the original effect exactly.
	///
	/// "Exactly" means the BIN_ chunk — the compiled data the player actually
	/// consumes — and the INFO chunk that lists its resources, compared byte for
	/// byte. That is a far sharper test than counting nodes or eyeballing a
	/// render, and it catches the whole class of silent drift: a lost texture, a
	/// dangling procedural-model index, a culling volume reset to default.
	///
	/// The control is a plain `resave` of the same file rather than the file on
	/// disk, because most of the corpus predates binary version 1710 and is
	/// legitimately migrated on save (see README). Comparing against the resave
	/// isolates what the assembler did from what the version upgrade did.
	///
	/// Only effects whose resources all resolve and sit in a flat Texture/,
	/// Model/ or Materials/ folder are eligible, because the assembler writes
	/// that layout; an effect keeping textures in nested folders would differ
	/// only in its path strings, which is a layout change and not drift.
	/// </summary>
	static class SelfTest
	{
		class Candidate
		{
			public string Id;
			public string Path;
		}

		public static int Run(string cataloguePath, string workDir)
		{
			if (!File.Exists(cataloguePath))
			{
				Console.Error.WriteLine("catalogue not found: " + cataloguePath);
				Console.Error.WriteLine("run the 'catalogue' command first");
				return 2;
			}

			var candidates = SelectCandidates(cataloguePath);
			Console.WriteLine("Self-test: {0} eligible effect(s)", candidates.Count);
			if (candidates.Count == 0) return 1;

			Directory.CreateDirectory(workDir);

			int identical = 0, differing = 0, errors = 0;
			var failures = new List<string>();

			foreach (var c in candidates)
			{
				string safe = c.Id.Replace('/', '_').Replace('\\', '_');
				string caseDir = Path.Combine(workDir, safe);
				string assembled = Path.Combine(caseDir, safe + ".efkefc");

				try
				{
					if (Directory.Exists(caseDir)) Directory.Delete(caseDir, true);
					Directory.CreateDirectory(caseDir);

					if (Assembler.AssembleSingleNode(c.Path, "0", assembled) != 0)
					{
						errors++;
						failures.Add(c.Id + "  assemble failed");
						continue;
					}

					// The control must be written beside the original, because
					// resource paths serialize relative to the effect file. It is
					// deleted immediately; nothing else in the corpus is touched.
					string control = c.Path + ".selftest-tmp.efkefc";
					byte[] controlBin, controlInfo;
					try
					{
						Core.New();
						if (!Core.LoadFrom(c.Path)) { errors++; failures.Add(c.Id + "  control load failed"); continue; }
						Core.SaveTo(control);
						var chunks = ReadChunks(control);
						controlBin = chunks.TryGetValue("BIN_", out var b) ? b : null;
						controlInfo = chunks.TryGetValue("INFO", out var i) ? i : null;
					}
					finally
					{
						try { if (File.Exists(control)) File.Delete(control); } catch { }
					}

					var made = ReadChunks(assembled);
					byte[] madeBin = made.TryGetValue("BIN_", out var mb) ? mb : null;
					byte[] madeInfo = made.TryGetValue("INFO", out var mi) ? mi : null;

					bool binSame = Same(controlBin, madeBin);
					bool infoSame = Same(controlInfo, madeInfo);

					if (binSame && infoSame) identical++;
					else
					{
						differing++;
						failures.Add(string.Format("{0}  BIN_ {1} ({2} vs {3} bytes), INFO {4}",
							c.Id,
							binSame ? "same" : "DIFFERS",
							controlBin == null ? -1 : controlBin.Length,
							madeBin == null ? -1 : madeBin.Length,
							infoSame ? "same" : "DIFFERS"));
					}
				}
				catch (Exception e)
				{
					errors++;
					failures.Add(c.Id + "  " + e.GetType().Name + ": " + e.Message);
				}
				finally
				{
					try { if (Directory.Exists(caseDir)) Directory.Delete(caseDir, true); } catch { }
				}
			}

			Console.WriteLine();
			Console.WriteLine("  byte-identical   {0}", identical);
			Console.WriteLine("  differing        {0}", differing);
			Console.WriteLine("  errors           {0}", errors);

			if (failures.Count > 0)
			{
				Console.WriteLine();
				Console.WriteLine("--- failures ---");
				foreach (var f in failures.Take(40)) Console.WriteLine("  " + f);
				if (failures.Count > 40) Console.WriteLine("  ... and {0} more", failures.Count - 40);
			}

			Console.WriteLine();
			Console.WriteLine(differing == 0 && errors == 0
				? "SELF-TEST OK: every grafted part reproduced its source exactly."
				: "SELF-TEST FAILED");

			return differing == 0 && errors == 0 ? 0 : 1;
		}

		static List<Candidate> SelectCandidates(string cataloguePath)
		{
			var list = new List<Candidate>();

			using (var doc = JsonDocument.Parse(File.ReadAllText(cataloguePath)))
			{
				foreach (var e in doc.RootElement.GetProperty("effects").EnumerateArray())
				{
					JsonElement nodes, resources, id, path;
					if (!e.TryGetProperty("nodes", out nodes)) continue;
					if (!e.TryGetProperty("resources", out resources)) continue;
					if (!e.TryGetProperty("id", out id) || !e.TryGetProperty("path", out path)) continue;

					// exactly one root child, so node "0" is the whole effect
					int roots = nodes.EnumerateArray().Count(n => n.GetProperty("depth").GetInt32() == 1);
					if (roots != 1) continue;

					var res = resources.EnumerateArray().ToList();
					if (res.Count == 0) continue;
					if (res.Any(r => !r.GetProperty("exists").GetBoolean())) continue;

					bool flat = res.All(r =>
					{
						string rel = r.GetProperty("relative").GetString();
						if (rel == null) return false;
						var bits = rel.Split('/');
						return bits.Length == 2 && (bits[0] == "Texture" || bits[0] == "Model" || bits[0] == "Materials");
					});
					if (!flat) continue;

					list.Add(new Candidate { Id = id.GetString(), Path = path.GetString() });
				}
			}

			return list;
		}

		/// <summary>Container is: "EFKE", int32 version, then (tag, int32 size, payload)*.</summary>
		static Dictionary<string, byte[]> ReadChunks(string path)
		{
			var result = new Dictionary<string, byte[]>(StringComparer.Ordinal);
			byte[] data = File.ReadAllBytes(path);
			if (data.Length < 8 || Encoding.ASCII.GetString(data, 0, 4) != "EFKE") return result;

			int pos = 8;
			while (pos + 8 <= data.Length)
			{
				string tag = Encoding.ASCII.GetString(data, pos, 4);
				int size = BitConverter.ToInt32(data, pos + 4);
				pos += 8;
				if (size < 0 || pos + size > data.Length) break;

				var payload = new byte[size];
				Array.Copy(data, pos, payload, 0, size);
				result[tag] = payload;
				pos += size;
			}
			return result;
		}

		static bool Same(byte[] a, byte[] b)
		{
			if (a == null || b == null) return a == b;
			if (a.Length != b.Length) return false;
			for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
			return true;
		}
	}
}
