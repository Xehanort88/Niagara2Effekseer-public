using System;
using System.Globalization;
using System.IO;

namespace Niagara2Efk.Mapper
{
	/// <summary>
	///   map &lt;intermediate.json&gt; &lt;out.efkefc&gt; [placeholder.png]
	///
	/// The optional placeholder stands in for textures the document names but
	/// cannot supply, which is the normal state until a real extractor exists.
	/// Every substitution is reported.
	///
	/// The back half of the Niagara converter. It reads the intermediate schema
	/// and writes an Effekseer effect; it never touches a .uasset.
	/// </summary>
	static class Program
	{
		static int Main(string[] args)
		{
			// The machine locale here is it-IT, whose decimal separator is ','.
			// Core's static ctor patches CurrentCulture, but only for whichever
			// thread first touches Core. Pin it process-wide so no float ever
			// serializes as "1,5" into the EDIT chunk's XML.
			CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
			CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;

			if (args.Length < 2)
			{
				Console.Error.WriteLine("usage: Niagara2EfkMapper <intermediate.json> <out.efkefc> [placeholder.png]");
				return 2;
			}

			string docPath = Path.GetFullPath(args[0]);
			string outPath = Path.GetFullPath(args[1]);

			if (!File.Exists(docPath))
			{
				Console.Error.WriteLine("no such document: " + docPath);
				return 2;
			}

			Directory.CreateDirectory(Path.GetDirectoryName(outPath));

			// Effekseer.Utils.Logger.LogPath is a bare static that only the GUI
			// ever sets. Left null, every internal Logger.Write() throws
			// ArgumentNullException, gets swallowed, and prints a stack trace to
			// stdout. Point it somewhere real.
			Effekseer.Utils.Logger.LogPath = Path.Combine(Path.GetDirectoryName(outPath), "niagara2efk-map.log");

			string placeholder = args.Length > 2 ? Path.GetFullPath(args[2]) : null;
			if (placeholder != null && !File.Exists(placeholder))
			{
				Console.Error.WriteLine("no such placeholder texture: " + placeholder);
				return 2;
			}

			return Mapper.Run(docPath, outPath, placeholder);
		}
	}
}
