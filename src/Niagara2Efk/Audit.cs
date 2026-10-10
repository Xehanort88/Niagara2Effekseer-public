using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Effekseer;
using Effekseer.Data;

namespace Niagara2Efk
{
	/// <summary>
	/// Corpus validation: load every .efkefc under a directory through
	/// EffekseerCore 170e, re-save it, load it again, and check the node tree
	/// survived the round trip unchanged.
	///
	/// The point is coverage. The generator only exercises the handful of fields
	/// it sets; the stock effects exercise every renderer type, every curve kind
	/// and every parameter group the mapper will eventually have to emit. If a
	/// field is dropped or mangled by a load/save cycle, it shows up here rather
	/// than as a silently wrong effect months later.
	///
	/// Nothing is left in the corpus. Each effect is re-saved to a temporary file
	/// beside itself, which is then either deleted or moved out to the
	/// intermediates directory. The save location is not negotiable: resource
	/// paths are stored absolute in memory and serialized relative to the effect
	/// file, so writing anywhere else would shift every texture path. Moving the
	/// finished file afterwards is safe, because by then the paths are already
	/// baked into it as text.
	/// </summary>
	static class Audit
	{
		/// <summary>Where kept intermediates go when the caller names nothing.</summary>
		public const string DefaultIntermediatesDir = "out/audit-intermediates";

		class Row
		{
			public string File;
			public string Status = "OK";
			public int OrigBinVersion;
			public int NewBinVersion;
			public int NodeCount;
			public string Renderers = "";
			public string Note = "";
			public string Intermediate = "";
		}

		public static int Run(string corpusDir, string reportPath, string intermediatesDir = null)
		{
			if (!Directory.Exists(corpusDir))
			{
				Console.Error.WriteLine("corpus directory not found: " + corpusDir);
				return 2;
			}

			bool keepIntermediates = !string.Equals(intermediatesDir, "none", StringComparison.OrdinalIgnoreCase);
			string keepDir = null;
			if (keepIntermediates)
			{
				keepDir = Path.GetFullPath(string.IsNullOrEmpty(intermediatesDir)
					? DefaultIntermediatesDir
					: intermediatesDir);
			}

			var files = Directory.GetFiles(corpusDir, "*.efkefc", SearchOption.AllDirectories)
								 .OrderBy(f => f, StringComparer.OrdinalIgnoreCase)
								 .ToArray();

			Console.WriteLine("Auditing {0} effects under {1}", files.Length, corpusDir);
			Console.WriteLine(keepIntermediates
				? "Intermediates for CHANGED effects will be moved to " + keepDir
				: "Intermediates will not be kept");

			var rows = new List<Row>();
			foreach (var file in files)
			{
				rows.Add(AuditOne(file, corpusDir, keepDir));
			}

			WriteReport(rows, reportPath);
			Summarize(rows);

			// Only hard failures count. A file that merely comes back textually
			// different is expected: the ToolVersion stamp is rewritten, elements are
			// reordered, older schemas are migrated, and floats reformat. None of that
			// is data loss — see the README. Use the two-process idempotence check for
			// the property that actually matters.
			var hardFailures = new[] { "LOAD_FAIL", "LOAD_THREW", "SERIALIZE_THREW",
				"READ_EDIT_THREW", "SAVE_THREW", "READ_BACK_THREW", "RELOAD_FAIL", "RELOAD_THREW" };
			int bad = rows.Count(r => hardFailures.Contains(r.Status));
			return bad == 0 ? 0 : 1;
		}

		static Row AuditOne(string path, string corpusDir, string keepDir)
		{
			var row = new Row { File = path };

			try { row.OrigBinVersion = ReadBinVersion(path); }
			catch (Exception e) { row.Note = "binver: " + e.Message; }

			// ---- load the original ------------------------------------------------
			try
			{
				Core.New();
				if (!Core.LoadFrom(path))
				{
					row.Status = "LOAD_FAIL";
					row.Note = "Core.LoadFrom returned false";
					return row;
				}
			}
			catch (Exception e)
			{
				row.Status = "LOAD_THREW";
				row.Note = Flatten(e);
				return row;
			}

			try
			{
				row.NodeCount = CountNodes(Core.Root);
				row.Renderers = string.Join("|", CollectRenderers(Core.Root).OrderBy(s => s));
			}
			catch (Exception e)
			{
				row.Status = "SERIALIZE_THREW";
				row.Note = Flatten(e);
				return row;
			}

			// Read what the ORIGINAL file says, straight off disk. EfkEfc.Load returns
			// the decoded EDIT chunk without building a node tree, which keeps this
			// comparison free of any in-process state.
			//
			// Comparing in-memory trees across a load/save/load cycle in one process
			// does NOT work: EffekseerCore carries static state that survives
			// Core.New(), and a second load in the same process can yield a subtly
			// different tree even though the file on disk is correct. Compare files.
			string xmlBefore;
			try
			{
				xmlBefore = EditXml(path);
			}
			catch (Exception e)
			{
				row.Status = "READ_EDIT_THREW";
				row.Note = Flatten(e);
				return row;
			}

			// ---- re-save beside the original --------------------------------------
			// This location is forced: resource paths serialize relative to the
			// effect file. The finally below removes it on every path, so the
			// corpus is left exactly as it was found.
			string temp = path + ".audit-tmp.efkefc";
			bool worthKeeping = false;

			try
			{
				try
				{
					Core.SaveTo(temp);
				}
				catch (Exception e)
				{
					row.Status = "SAVE_THREW";
					row.Note = Flatten(e);
					return row;
				}

				try { row.NewBinVersion = ReadBinVersion(temp); }
				catch { /* reported via status below if the reload also fails */ }

				// ---- what did we actually write? ----------------------------------
				string xmlAfter;
				try
				{
					xmlAfter = EditXml(temp);
				}
				catch (Exception e)
				{
					row.Status = "READ_BACK_THREW";
					row.Note = Flatten(e);
					return row;
				}

				// ---- and does Effekseer accept it? --------------------------------
				try
				{
					Core.New();
					if (!Core.LoadFrom(temp))
					{
						row.Status = "RELOAD_FAIL";
						row.Note = "Core.LoadFrom returned false on our own output";
						return row;
					}
				}
				catch (Exception e)
				{
					row.Status = "RELOAD_THREW";
					row.Note = Flatten(e);
					return row;
				}

				// ---- compare ------------------------------------------------------
				// Only files already at the current binary version are expected to be
				// byte-identical after a resave. Older ones legitimately get migrated by
				// ProjectVersionUpdator, so a difference there is an upgrade, not a defect.
				if (xmlBefore != xmlAfter)
				{
					if (row.OrigBinVersion == 1710)
					{
						row.Status = "CHANGED";
						row.Note = FirstDifference(xmlBefore, xmlAfter);
						worthKeeping = true;
						return row;
					}

					row.Status = "UPGRADED";
					row.Note = FirstDifference(xmlBefore, xmlAfter);
				}

				return row;
			}
			finally
			{
				if (worthKeeping && keepDir != null)
					row.Intermediate = MoveOut(temp, path, corpusDir, keepDir);

				TryDelete(temp);
			}
		}

		/// <summary>
		/// Move a kept intermediate out of the corpus, mirroring the effect's
		/// path under the corpus root so two effects with the same name in
		/// different folders cannot collide.
		///
		/// The moved file is for DIFFING, not for playing: its resource paths are
		/// relative to where the original effect lives, so they no longer resolve
		/// from the intermediates folder. Decode it, do not load it.
		/// </summary>
		static string MoveOut(string temp, string originalPath, string corpusDir, string keepDir)
		{
			try
			{
				if (!File.Exists(temp)) return "";

				string relative = Path.GetRelativePath(corpusDir, originalPath);
				string destination = Path.Combine(keepDir, relative);

				Directory.CreateDirectory(Path.GetDirectoryName(destination));
				File.Move(temp, destination, true);

				Console.WriteLine("  kept intermediate: {0}", destination);
				return destination;
			}
			catch (Exception e)
			{
				Console.WriteLine("  could not keep intermediate for {0}: {1}",
					Path.GetFileName(originalPath), e.Message);
				return "";
			}
		}

		/// <summary>Container is: "EFKE", int32 version, then (tag, int32 size, payload)*.</summary>
		static int ReadBinVersion(string path)
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
				if (tag == "BIN_" && size >= 8) return BitConverter.ToInt32(data, pos + 4);
				pos += size;
			}
			return -1;
		}

		static int CountNodes(NodeBase node)
		{
			int n = 1;
			foreach (var c in node.Children.Internal) n += CountNodes(c);
			return n;
		}

		static HashSet<string> CollectRenderers(NodeBase node)
		{
			var set = new HashSet<string>();
			Walk(node, set);
			return set;
		}

		static void Walk(NodeBase node, HashSet<string> set)
		{
			var n = node as Node;
			if (n != null) set.Add(n.DrawingValues.Type.Value.ToString());
			foreach (var c in node.Children.Internal) Walk(c, set);
		}

		static string FirstDifference(string a, string b)
		{
			int i = 0;
			int min = Math.Min(a.Length, b.Length);
			while (i < min && a[i] == b[i]) i++;

			int start = Math.Max(0, i - 40);
			string before = a.Substring(start, Math.Min(90, a.Length - start));
			string after = b.Substring(start, Math.Min(90, b.Length - start));
			return string.Format("at {0} (len {1} vs {2}) before=[{3}] after=[{4}]",
				i, a.Length, b.Length, before, after);
		}

		static string Flatten(Exception e)
		{
			return (e.GetType().Name + ": " + e.Message).Replace('\r', ' ').Replace('\n', ' ');
		}

		static void TryDelete(string path)
		{
			try { if (File.Exists(path)) File.Delete(path); } catch { }
		}

		static void WriteReport(List<Row> rows, string reportPath)
		{
			if (string.IsNullOrEmpty(reportPath)) return;
			Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath)));

			using (var w = new StreamWriter(reportPath, false, new UTF8Encoding(false)))
			{
				w.WriteLine("status,origBinVersion,newBinVersion,nodeCount,renderers,file,intermediate,note");
				foreach (var r in rows)
				{
					w.WriteLine(string.Join(",",
						Csv(r.Status), r.OrigBinVersion, r.NewBinVersion, r.NodeCount,
						Csv(r.Renderers), Csv(r.File), Csv(r.Intermediate), Csv(r.Note)));
				}
			}
			Console.WriteLine("Report written to {0}", Path.GetFullPath(reportPath));
		}

		static string Csv(string s)
		{
			if (s == null) return "";
			return "\"" + s.Replace("\"", "\"\"") + "\"";
		}

		static void Summarize(List<Row> rows)
		{
			Console.WriteLine();
			Console.WriteLine("--- status ---");
			foreach (var g in rows.GroupBy(r => r.Status).OrderByDescending(g => g.Count()))
				Console.WriteLine("  {0,-16} {1}", g.Key, g.Count());

			Console.WriteLine();
			Console.WriteLine("--- source binary versions ---");
			foreach (var g in rows.GroupBy(r => r.OrigBinVersion).OrderBy(g => g.Key))
				Console.WriteLine("  {0,-16} {1}", g.Key, g.Count());

			Console.WriteLine();
			Console.WriteLine("--- output binary versions ---");
			foreach (var g in rows.Where(r => r.NewBinVersion != 0).GroupBy(r => r.NewBinVersion).OrderBy(g => g.Key))
				Console.WriteLine("  {0,-16} {1}", g.Key, g.Count());

			Console.WriteLine();
			Console.WriteLine("--- renderer types in use (effects containing each) ---");
			var counts = new Dictionary<string, int>();
			foreach (var r in rows)
			{
				if (string.IsNullOrEmpty(r.Renderers)) continue;
				foreach (var t in r.Renderers.Split('|'))
				{
					if (!counts.ContainsKey(t)) counts[t] = 0;
					counts[t]++;
				}
			}
			foreach (var kv in counts.OrderByDescending(k => k.Value))
				Console.WriteLine("  {0,-16} {1}", kv.Key, kv.Value);

			Console.WriteLine();
			var hard = new[] { "LOAD_FAIL", "LOAD_THREW", "SERIALIZE_THREW", "READ_EDIT_THREW",
				"SAVE_THREW", "READ_BACK_THREW", "RELOAD_FAIL", "RELOAD_THREW" };
			var failures = rows.Where(r => hard.Contains(r.Status)).ToList();
			if (failures.Count == 0)
			{
				Console.WriteLine("No load/save/reload failures. Textual differences are detailed in the CSV.");
			}
			else
			{
				foreach (var r in failures.Take(25))
					Console.WriteLine("  {0}: {1}  {2}", r.Status, Path.GetFileName(r.File), r.Note);
			}
		}

		/// <summary>The EDIT chunk of an .efkefc as XML text. 1.8 changed EfkEfc.Load
		/// to return success and keep the document in EditorData.</summary>
		static string EditXml(string file)
		{
#if EFK18
			var e = new Effekseer.IO.EfkEfc();
			if (!e.Load(file) || e.EditorData == null) throw new InvalidDataException("cannot read the EDIT chunk of " + file);
			return e.EditorData.OuterXml;
#else
			return new Effekseer.IO.EfkEfc().Load(file).OuterXml;
#endif
		}
	}
}
