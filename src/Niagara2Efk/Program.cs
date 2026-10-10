using System;
using System.Globalization;
using System.IO;
using System.Linq;
using Effekseer;
using Effekseer.Data;

namespace Niagara2Efk
{
	/// <summary>
	/// Scaffolding for the Niagara → Effekseer converter. Not the converter yet.
	///
	///   roundtrip &lt;out.efkefc&gt; [lifeFrames] [genTimeFrames]
	///       Minimal node, no renderer resources. Proves the write path and is
	///       diffable between runs (see README "Verification performed").
	///
	///   sprite &lt;out.efkefc&gt; &lt;texture.png&gt;
	///       A visible additive sprite burst modelled on the stock MZ effects.
	///       This is the one that has to actually render on screen.
	/// </summary>
	static class Program
	{
		/// <summary>
		/// An exception nobody catches ends a .NET process with a Windows
		/// "Application Error" dialog (0xe0434352), one per run, which piles up
		/// when scripts or other agents call the tool in a loop. Report it on
		/// stderr and exit with 3 instead. The common case is a file saved by a
		/// newer Effekseer than this build reads, which gets a direct answer.
		/// </summary>
		static int Main(string[] args)
		{
			try
			{
				return Run(args);
			}
			catch (Exception e)
			{
				var inner = e;
				while (inner.InnerException != null && !inner.Message.StartsWith("Version Error")) inner = inner.InnerException;
				if (inner.Message.StartsWith("Version Error"))
				{
#if EFK18
					Console.Error.WriteLine("Niagara2Efk: an effect it loaded was saved by an Effekseer newer than 1.80.3, which this build reads.");
#else
					Console.Error.WriteLine("Niagara2Efk: an effect it loaded was saved by an Effekseer newer than 1.70 (a 1.8x editor or tool save).");
					Console.Error.WriteLine("  Use the 1.80.3 build: dotnet src/Niagara2Efk1803/bin/Release/net9.0/Niagara2Efk.dll " + string.Join(" ", args));
#endif
					Console.Error.WriteLine("  Command: " + string.Join(" ", args));
					return 3;
				}
				Console.Error.WriteLine("Niagara2Efk: unexpected error: " + e);
				Console.Error.WriteLine("  Command: " + string.Join(" ", args));
				return 3;
			}
		}

		static int Run(string[] args)
		{
			// The machine locale here is it-IT, whose decimal separator is ','.
			// Core's static ctor patches CurrentCulture, but only for whichever thread
			// first touches Core. Pin it process-wide so no float ever serializes as
			// "1,5" into the EDIT chunk's XML.
			CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
			CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;

			if (args.Length < 2)
			{
				Console.Error.WriteLine("usage: Niagara2Efk roundtrip <out.efkefc> [life] [genTime]");
				Console.Error.WriteLine("       Niagara2Efk sprite    <out.efkefc> <texture.png>");
				Console.Error.WriteLine("       Niagara2Efk blank     <out.efkefc> [life]   (neutral base to author from with edit)");
				Console.Error.WriteLine("       Niagara2Efk audit     <corpusDir> [report.csv] [intermediatesDir|none]");
				Console.Error.WriteLine("       Niagara2Efk catalogue <out.json> <id=corpusDir> [<id=corpusDir> ...]");
				Console.Error.WriteLine("       Niagara2Efk assemble  <spec.json> <out.efkefc>");
				Console.Error.WriteLine("       Niagara2Efk parts     <selection.json> <outDir>");
				Console.Error.WriteLine("       Niagara2Efk selftest  <catalogue.json> [workDir]");
				Console.Error.WriteLine("       Niagara2Efk lint      <effect.efkefc | folder> [--catalogue out/catalogue.json] [--json report.json] [--strict]");
				Console.Error.WriteLine("       Niagara2Efk cascade   <dump.json> <out-intermediate.json> [worldScale] [spriteScale]");
				return 2;
			}

			string command = args[0];

			if (command == "lint") return Lint.Run(args.Skip(1).ToArray());
			if (command == "lint-internal") return Lint.RunWorker(args.Skip(1).ToArray());

			if (command == "audit")
			{
				// The audit walks a whole corpus and manages its own paths, so it runs
				// before the single-output-file setup below.
				string reportPath = args.Length > 2 ? args[2] : null;

				// Where to move the intermediates the audit decides are worth
				// keeping. Never the corpus: it is left exactly as found.
				// Pass "none" to keep nothing.
				string intermediatesDir = args.Length > 3 ? args[3] : null;

				Effekseer.Utils.Logger.LogPath = Path.Combine(Path.GetTempPath(), "niagara2efk-audit.log");
				return Audit.Run(Path.GetFullPath(args[1]), reportPath, intermediatesDir);
			}

			if (command == "catalogue")
			{
				// catalogue <out.json> <id=corpusDir> [<id=corpusDir> ...]
				// Reads every effect under each corpus and writes the parts index.
				// Like audit, it manages its own paths and writes nothing to the corpus.
				if (args.Length < 3)
				{
					Console.Error.WriteLine("catalogue: <out.json> <id=corpusDir> [<id=corpusDir> ...]");
					return 2;
				}
				string catOut = Path.GetFullPath(args[1]);
				Directory.CreateDirectory(Path.GetDirectoryName(catOut));
				Effekseer.Utils.Logger.LogPath = Path.Combine(Path.GetDirectoryName(catOut), "niagara2efk-catalogue.log");
				return Catalogue.Run(catOut, args.Skip(2).ToArray());
			}

			if (command == "assemble")
			{
				// assemble <spec.json> <out.efkefc>
				if (args.Length < 3)
				{
					Console.Error.WriteLine("assemble: <spec.json> <out.efkefc>");
					return 2;
				}
				string asmOut = Path.GetFullPath(args[2]);
				Directory.CreateDirectory(Path.GetDirectoryName(asmOut));
				Effekseer.Utils.Logger.LogPath = Path.Combine(Path.GetDirectoryName(asmOut), "niagara2efk-assemble.log");
				return Assembler.Run(Path.GetFullPath(args[1]), asmOut);
			}

			if (command == "edit")
			{
				// edit <edit-spec.json> <out.efkefc>
				if (args.Length < 3)
				{
					Console.Error.WriteLine("edit: <edit-spec.json> <out.efkefc>");
					return 2;
				}
				string editOut = Path.GetFullPath(args[2]);
				Directory.CreateDirectory(Path.GetDirectoryName(editOut));
				// The log goes beside the SPEC, not the output: an edit built straight into a
				// game project's effects folder used to leave this log there every time.
				Effekseer.Utils.Logger.LogPath = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(args[1])), "niagara2efk-edit.log");
				return Assembler.RunEdit(Path.GetFullPath(args[1]), editOut);
			}

			if (command == "dump")
			{
				// dump <in.efkefc> [nodePath ...]   every saved value, edit-spec spelling
				if (args.Length < 2)
				{
					Console.Error.WriteLine("dump: <in.efkefc> [nodePath ...]");
					return 2;
				}
				return Assembler.RunDump(Path.GetFullPath(args[1]), args.Skip(2).ToList());
			}

			if (command == "cascade")
			{
				// cascade <dump.json> <out-intermediate.json> [worldScale] [spriteScale]
				//
				// A front end only: it writes the intermediate document defined in
				// schema/intermediate-v1.md. Run Niagara2EfkMapper on the result to
				// get an .efkefc. Nothing here touches Effekseer.
				if (args.Length < 3)
				{
					Console.Error.WriteLine("cascade: <dump.json> <out-intermediate.json> [worldScale] [spriteScale]");
					return 2;
				}
				float ws = args.Length > 3 ? float.Parse(args[3], CultureInfo.InvariantCulture) : 0.25f;
				float ss = args.Length > 4 ? float.Parse(args[4], CultureInfo.InvariantCulture) : 1.0f;

				float tg = args.Length > 5 ? float.Parse(args[5], CultureInfo.InvariantCulture) : 6.0f;
				float lg = args.Length > 6 ? float.Parse(args[6], CultureInfo.InvariantCulture) : 1.5f;

				return Cascade.Run(Path.GetFullPath(args[1]), Path.GetFullPath(args[2]), ws, ss, tg, lg);
			}

			if (command == "parts")
			{
				// parts <selection.json> <outDir>
				// Batch-assemble one standalone effect per distinct part, for the
				// contact sheet. The selection comes from tools/parts/enumerate.py.
				if (args.Length < 3)
				{
					Console.Error.WriteLine("parts: <selection.json> <outDir>");
					return 2;
				}
				string partsOut = Path.GetFullPath(args[2]);
				Directory.CreateDirectory(partsOut);
				Effekseer.Utils.Logger.LogPath = Path.Combine(partsOut, "niagara2efk-parts.log");
				return Assembler.RunBatch(Path.GetFullPath(args[1]), partsOut);
			}

			if (command == "selftest")
			{
				// selftest <catalogue.json> [workDir]
				if (args.Length < 2)
				{
					Console.Error.WriteLine("selftest: <catalogue.json> [workDir]");
					return 2;
				}
				string work = args.Length > 2
					? Path.GetFullPath(args[2])
					: Path.Combine(Path.GetTempPath(), "niagara2efk-selftest");
				Directory.CreateDirectory(work);
				Effekseer.Utils.Logger.LogPath = Path.Combine(work, "niagara2efk-selftest.log");
				return SelfTest.Run(Path.GetFullPath(args[1]), work);
			}
			string outPath = Path.GetFullPath(args[1]);

			// Effekseer.Utils.Logger.LogPath is a bare static that only the GUI ever sets.
			// Left null, every internal Logger.Write() throws ArgumentNullException, gets
			// swallowed, and prints a stack trace to stdout. Point it somewhere real.
			string outDir = Path.GetDirectoryName(outPath);
			Directory.CreateDirectory(outDir);
			Effekseer.Utils.Logger.LogPath = Path.Combine(outDir, "niagara2efk.log");

			switch (command)
			{
				case "roundtrip":
					return Roundtrip(
						outPath,
						args.Length > 2 ? int.Parse(args[2], CultureInfo.InvariantCulture) : 60,
						args.Length > 3 ? float.Parse(args[3], CultureInfo.InvariantCulture) : 2.0f);

				case "resave":
					// Load an existing effect and write it back out. Doubles as the
					// "upgrade an old effect to binary version 1710" utility and as the
					// way to inspect what a save/load cycle does to a specific file.
					if (args.Length < 3)
					{
						Console.Error.WriteLine("resave: <in.efkefc> <out.efkefc>");
						return 2;
					}
					{
						// Note the argument shift: for this command args[1] is the INPUT,
						// so the output path is args[2], not the shared outPath above.
						string inFile = Path.GetFullPath(args[1]);
						string reOut = Path.GetFullPath(args[2]);
						Directory.CreateDirectory(Path.GetDirectoryName(reOut));

						Core.New();
						if (!Core.LoadFrom(inFile))
						{
							Console.Error.WriteLine("could not load " + inFile);
							return 1;
						}
						Core.SaveTo(reOut);
						Console.WriteLine("Resaved {0} -> {1}", inFile, reOut);
						return 0;
					}

				case "sprite":
					if (args.Length < 3)
					{
						Console.Error.WriteLine("sprite: a texture path is required");
						return 2;
					}
					return SpriteBurst(outPath, Path.GetFullPath(args[2]));

				case "blank":
					return Blank(outPath, args.Length > 2 ? int.Parse(args[2], CultureInfo.InvariantCulture) : 60);

				default:
					Console.Error.WriteLine("unknown command: " + command);
					return 2;
			}
		}

		/// <summary>Fresh project with the single node Core.New() already made.</summary>
		static Node NewSingleNode(string name)
		{
			// Core.New() (not Initialize()) — Initialize() additionally loads the editor
			// option file and creates scripts/ directories next to the executable, which
			// a headless generator has no use for. New() is what populates every static
			// that SaveAsXmlDocument() reads: EffectBehavior, Culling, LodValues, Global,
			// Dynamic, ProceduralModel.
			Core.New();

			// New() already appends one child under Root — reuse it rather than adding a
			// second, otherwise the effect ships with an empty sibling node.
			if (Core.Root.Children.Count == 0)
			{
				Core.Root.AddChild();
			}

			var node = Core.Root.Children[0];
			node.Name.SetValue(name);
			return node;
		}

		static int Roundtrip(string outPath, int life, float genTime)
		{
			var node = NewSingleNode("RoundtripSprite");

			// Life is frames, stored as a randomized triple (Center/Max/Min).
			node.CommonValues.Life.SetCenter(life);
			node.CommonValues.Life.SetMax(life);
			node.CommonValues.Life.SetMin(life);

			// MaxGeneration is Int + an Infinite flag, not a plain int.
			node.CommonValues.MaxGeneration.Value.SetValue(20);
			node.CommonValues.MaxGeneration.Infinite.SetValue(false);

			// Frames between spawns.
			//
			// These are flat on CommonValues at 1.70e. Do NOT carry this code forward to
			// 1.8x unchanged: there the same properties are annotated
			// [IO(Import = true, Export = false)] — legacy, load-only — and the live
			// fields move under CommonValues.Generation.* / CommonValues.Removal.*.
			// Writing the flat ones on 1.8x succeeds in memory and is silently dropped
			// on save.
			EfkCompat.GenerationTime(node.CommonValues).SetCenter(genTime);
			EfkCompat.GenerationTime(node.CommonValues).SetMax(genTime);
			EfkCompat.GenerationTime(node.CommonValues).SetMin(genTime);

			EfkCompat.RemoveWhenLifeIsExtinct(node.CommonValues).SetValue(true);

			// Sprite is RendererValues' constructor default, so IO.cs omits the element
			// entirely when it is written back (Data/IO.cs:734 skips enums equal to their
			// default). The absence of <DrawingValues><Type> in the output is expected.
			node.DrawingValues.Type.SetValue(RendererValues.ParamaterType.Sprite);

			Core.SaveTo(outPath);
			Report(outPath);

			// Read the file back through Effekseer's own loader. Decoding the EDIT chunk
			// with our Python tool only proves the container is well-formed; this proves
			// the editor itself accepts the file and recovers the values we set.
			Core.New();
			if (!Core.LoadFrom(outPath))
			{
				Console.Error.WriteLine("FAIL: Core.LoadFrom() rejected the file we just wrote.");
				return 1;
			}

			var reloaded = Core.Root.Children[0];
			Console.WriteLine("Reloaded: Name={0} Life={1} GenTime={2} MaxGen={3} Renderer={4}",
				reloaded.Name.Value,
				reloaded.CommonValues.Life.Center,
				EfkCompat.GenerationTime(reloaded.CommonValues).Center.ToString(CultureInfo.InvariantCulture),
				reloaded.CommonValues.MaxGeneration.Value.Value,
				reloaded.DrawingValues.Type.Value);

			bool ok = reloaded.Name.Value == "RoundtripSprite"
				&& reloaded.CommonValues.Life.Center == life
				&& Math.Abs(EfkCompat.GenerationTime(reloaded.CommonValues).Center - genTime) < 1e-6f
				&& reloaded.CommonValues.MaxGeneration.Value.Value == 20
				&& reloaded.DrawingValues.Type.Value == RendererValues.ParamaterType.Sprite;

			Console.WriteLine(ok ? "ROUND-TRIP OK" : "ROUND-TRIP MISMATCH");
			return ok ? 0 : 1;
		}

		/// <summary>
		/// An outward burst of additive sprites. Field choices mirror the stock MZ
		/// effect HitPhysical.efkefc rather than Core.New()'s editor defaults — the
		/// editor defaults produce a technically valid effect that draws nothing.
		/// </summary>
		static int SpriteBurst(string outPath, string texturePath)
		{
			if (!File.Exists(texturePath))
			{
				Console.Error.WriteLine("texture not found: " + texturePath);
				return 2;
			}

			var node = NewSingleNode("SpriteBurst");

			node.CommonValues.Life.SetCenter(30);
			node.CommonValues.Life.SetMax(36);
			node.CommonValues.Life.SetMin(24);

			node.CommonValues.MaxGeneration.Value.SetValue(40);
			node.CommonValues.MaxGeneration.Infinite.SetValue(false);

			// A near-zero generation time makes all MaxGeneration particles spawn on
			// effectively the same frame, i.e. a burst. This is the idiom the stock
			// effects use; there is no separate "burst" mode.
			EfkCompat.GenerationTime(node.CommonValues).SetCenter(0.00001f);
			EfkCompat.GenerationTime(node.CommonValues).SetMax(0.00001f);
			EfkCompat.GenerationTime(node.CommonValues).SetMin(0.00001f);

			EfkCompat.RemoveWhenLifeIsExtinct(node.CommonValues).SetValue(true);

			// Outward motion: random velocity per axis, slight gravity on Y.
			node.LocationValues.Type.SetValue(LocationValues.ParamaterType.PVA);
			SetRange(node.LocationValues.PVA.Velocity.X, -2.5f, 2.5f);
			SetRange(node.LocationValues.PVA.Velocity.Y, -2.5f, 2.5f);
			SetRange(node.LocationValues.PVA.Velocity.Z, -2.5f, 2.5f);
			SetRange(node.LocationValues.PVA.Acceleration.Y, -0.08f, -0.08f);

			// SinglePVA drives one uniform scale instead of three axes.
			node.ScalingValues.Type.SetValue(ScaleValues.ParamaterType.SinglePVA);
			SetRange(node.ScalingValues.SinglePVA.Scale, 0.9f, 1.6f);
			SetRange(node.ScalingValues.SinglePVA.Velocity, -0.02f, -0.02f);

			// Renderer resources. ColorTexture is stored absolute in memory and
			// serialized relative to the effect file, so the .efkefc must be written
			// next to a Texture/ folder for the path to resolve at runtime.
			node.RendererCommonValues.ColorTexture.SetAbsolutePath(texturePath);
			node.RendererCommonValues.AlphaBlend.SetValue(AlphaBlendType.Add);
			node.RendererCommonValues.FadeOutType.SetValue(RendererCommonValues.FadeOutMethod.WithinLifetime);
			node.RendererCommonValues.FadeOut.Frame.SetValue(10.0f);

			node.DrawingValues.Type.SetValue(RendererValues.ParamaterType.Sprite);
			node.DrawingValues.Sprite.Billboard.SetValue(RendererValues.BillboardType.Billboard);
			node.DrawingValues.Sprite.ColorAll.SetValue(StandardColorType.Fixed);
			node.DrawingValues.Sprite.ColorAll_Fixed.SetValue(255, 210, 140, 255);

			Core.SaveTo(outPath);
			Report(outPath);

			Core.New();
			if (!Core.LoadFrom(outPath))
			{
				Console.Error.WriteLine("FAIL: Core.LoadFrom() rejected the file we just wrote.");
				return 1;
			}

			var reloaded = Core.Root.Children[0];
			string tex = reloaded.RendererCommonValues.ColorTexture.RelativePath;
			Console.WriteLine("Reloaded: Name={0} Renderer={1} Blend={2} Texture={3}",
				reloaded.Name.Value,
				reloaded.DrawingValues.Type.Value,
				reloaded.RendererCommonValues.AlphaBlend.Value,
				string.IsNullOrEmpty(tex) ? "<none>" : tex);

			bool ok = reloaded.DrawingValues.Type.Value == RendererValues.ParamaterType.Sprite
				&& reloaded.RendererCommonValues.AlphaBlend.Value == AlphaBlendType.Add
				&& !string.IsNullOrEmpty(tex);

			Console.WriteLine(ok ? "SPRITE OK" : "SPRITE MISMATCH");
			return ok ? 0 : 1;
		}

		/// <summary>
		/// A neutral base to author an effect from with `edit`: one node, one
		/// particle, fixed life, standing still at the origin, upright, scale 1,
		/// no gravity, a plain white additive sprite. Every value an edit does not
		/// set stays neutral, and nodes copied from it (`add`, including
		/// `"file": "blank.efkefc"` for a fresh one) start neutral too. `sprite`
		/// is a burst (random velocity, gravity, shrinking, random life) and is the
		/// wrong base: copies of it fell, shrank and wandered.
		/// </summary>
		static int Blank(string outPath, int life)
		{
			var node = NewSingleNode("Blank");

			node.CommonValues.Life.SetCenter(life);
			node.CommonValues.Life.SetMax(life);
			node.CommonValues.Life.SetMin(life);
			node.CommonValues.MaxGeneration.Value.SetValue(1);
			node.CommonValues.MaxGeneration.Infinite.SetValue(false);
			EfkCompat.GenerationTime(node.CommonValues).SetCenter(1);
			EfkCompat.GenerationTime(node.CommonValues).SetMax(1);
			EfkCompat.GenerationTime(node.CommonValues).SetMin(1);
			EfkCompat.RemoveWhenLifeIsExtinct(node.CommonValues).SetValue(true);

			node.LocationValues.Type.SetValue(LocationValues.ParamaterType.Fixed);
			node.LocationValues.Fixed.Location.X.SetValue(0);
			node.LocationValues.Fixed.Location.Y.SetValue(0);
			node.LocationValues.Fixed.Location.Z.SetValue(0);
			// Zero the PVA slots too, so switching this node (or a copy) to PVA
			// starts from rest instead of inheriting a velocity or gravity.
			foreach (var v in new[] { node.LocationValues.PVA.Velocity, node.LocationValues.PVA.Acceleration })
			{
				SetRange(v.X, 0, 0);
				SetRange(v.Y, 0, 0);
				SetRange(v.Z, 0, 0);
			}
			node.RotationValues.Type.SetValue(RotationValues.ParamaterType.Fixed);
			node.ScalingValues.Type.SetValue(ScaleValues.ParamaterType.Fixed);
			node.ScalingValues.Fixed.Scale.X.SetValue(1);
			node.ScalingValues.Fixed.Scale.Y.SetValue(1);
			node.ScalingValues.Fixed.Scale.Z.SetValue(1);

			node.RendererCommonValues.AlphaBlend.SetValue(AlphaBlendType.Add);
			node.RendererCommonValues.FadeInType.SetValue(RendererCommonValues.FadeInMethod.None);
			node.RendererCommonValues.FadeOutType.SetValue(RendererCommonValues.FadeOutMethod.None);
			node.DrawingValues.Type.SetValue(RendererValues.ParamaterType.Sprite);
			node.DrawingValues.Sprite.Billboard.SetValue(RendererValues.BillboardType.Billboard);
			node.DrawingValues.Sprite.ColorAll.SetValue(StandardColorType.Fixed);
			node.DrawingValues.Sprite.ColorAll_Fixed.SetValue(255, 255, 255, 255);

			Core.SaveTo(outPath);
			Report(outPath);

			Core.New();
			if (!Core.LoadFrom(outPath))
			{
				Console.Error.WriteLine("FAIL: Core.LoadFrom() rejected the file we just wrote.");
				return 1;
			}
			var n = Core.Root.Children[0];
			bool ok = n.LocationValues.Type.Value == LocationValues.ParamaterType.Fixed
				&& n.ScalingValues.Type.Value == ScaleValues.ParamaterType.Fixed
				&& n.RotationValues.Type.Value == RotationValues.ParamaterType.Fixed
				&& n.LocationValues.PVA.Acceleration.Y.GetMax() == 0 && n.LocationValues.PVA.Acceleration.Y.GetMin() == 0
				&& n.CommonValues.MaxGeneration.Value.Value == 1 && !n.CommonValues.MaxGeneration.Infinite.Value
				&& n.CommonValues.Life.GetMin() == life && n.CommonValues.Life.GetMax() == life;
			Console.WriteLine(ok ? "BLANK OK" : "BLANK MISMATCH");
			return ok ? 0 : 1;
		}

		static void SetRange(Effekseer.Data.Value.FloatWithRandom v, float min, float max)
		{
			v.SetCenter((min + max) * 0.5f);
			v.SetMax(max);
			v.SetMin(min);
		}

		static void Report(string outPath)
		{
			var info = new FileInfo(outPath);
			Console.WriteLine("Wrote {0} ({1} bytes)", info.FullName, info.Length);
			Console.WriteLine("Tool version stamped: {0}", Core.Version);
		}
	}
}
