using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using Effekseer;
using Effekseer.Data;

namespace Niagara2Efk
{
	/// <summary>
	/// Print every saved value of the chosen nodes as "path = value", in the
	/// same property-path spelling the edit spec uses, so an edit can be
	/// written against what is actually in the file, then the procedural
	/// models those nodes use. Read only.
	/// </summary>
	static partial class Assembler
	{
		public static int RunDump(string source, IList<string> nodePaths)
		{
			Core.New();
			if (!Core.LoadFrom(source))
			{
				Console.Error.WriteLine("could not load " + source);
				return 1;
			}
			var targets = new List<Tuple<string, NodeBase>>();
			if (nodePaths.Count == 0)
				Walk(Core.Root, "", targets);
			else
				foreach (var p in nodePaths)
				{
					var n = FindNode(p);
					if (n == null) { Console.Error.WriteLine("no node " + p); return 2; }
					targets.Add(Tuple.Create(p, (NodeBase)n));
				}
			var shown = new HashSet<int>();
			foreach (var t in targets)
			{
				Console.WriteLine("### " + t.Item1 + " " + t.Item2.Name.Value);
				DumpObject(t.Item2, "", 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
				if (t.Item2 is Node node)
					WalkOwnValues(node, (_, v) =>
					{
						if (v is ProceduralModelReference r && r.Index >= 0) shown.Add(r.Index);
					});
			}
			// Procedural models live in the project, not in a node: a node's
			// Model/Reference prints "#<index> "<name>"" and the model is printed
			// once here, in the paths an edit spec's `procedural` set uses. A
			// whole-effect dump lists every model, used or not.
			var models = Core.ProceduralModel.ProceduralModels.Values;
			for (int i = 0; i < models.Count; i++)
			{
				if (nodePaths.Count > 0 && !shown.Contains(i)) continue;
				Console.WriteLine("### procedural " + i.ToString(CultureInfo.InvariantCulture) + " " + models[i].Name.Value);
				DumpObject(models[i], "", 0, new HashSet<object>(ReferenceEqualityComparer.Instance));
			}
			return 0;
		}

		static NodeBase FindNode(string path)
		{
			NodeBase cur = Core.Root;
			if (IsRootPath(path)) return cur;
			foreach (var s in path.Split('/'))
			{
				int i = int.Parse(s, CultureInfo.InvariantCulture);
				if (i < 0 || i >= cur.Children.Count) return null;
				cur = cur.Children[i];
			}
			return cur;
		}

		static void Walk(NodeBase n, string path, List<Tuple<string, NodeBase>> into)
		{
			for (int i = 0; i < n.Children.Count; i++)
			{
				string p = path.Length == 0 ? i.ToString(CultureInfo.InvariantCulture) : path + "/" + i;
				into.Add(Tuple.Create(p, (NodeBase)n.Children[i]));
				Walk(n.Children[i], p, into);
			}
		}

		static void DumpObject(object o, string prefix, int depth, HashSet<object> seen)
		{
			if (o == null || depth > 8 || !seen.Add(o)) return;
			if (o is MaterialFileParameter material)
				foreach (var slot in material.GetValueStatus().Where(s => s.IsShown))
					if (TryDescribe(slot.Value, out var value))
						Console.WriteLine(prefix + (slot.Value is Effekseer.Data.Value.PathForImage ? "/Textures/" : "/Uniforms/") + slot.Key.UniformName + " = " + value);
			bool keyed = false;
			foreach (var prop in o.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
			{
				if (prop.GetIndexParameters().Length > 0) continue;
				if (prop.Name == "Children" || prop.Name == "Parent") continue;
				var io = prop.GetCustomAttributes(typeof(IOAttribute), true).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export) continue;
				object v;
				try { v = prop.GetValue(o, null); } catch { continue; }
				if (v == null) continue;
				string path = prefix.Length == 0 ? prop.Name : prefix + "/" + prop.Name;
				if (v is Effekseer.Data.Value.IFCurve c)
				{
					var keys = c.Keys.Select(k => k.Frame + ":" + F(k.ValueAsFloat)).ToList();
					if (keys.Count > 0)
					{
						keyed = true;
						Console.WriteLine(path + " = fcurve[" + string.Join(" ", keys) + "]");
						// How often the runtime samples the curve: 1 follows it
						// exactly, the default 10 steps through it.
						Console.WriteLine(path + "/Sampling = " + c.Sampling.Value.ToString(CultureInfo.InvariantCulture));
						// A random offset added to the curve's value, one pick per particle,
						// in the runtime's units (a rotation's in radians while its keys are
						// degrees). Printed only when set.
						foreach (string offset in new[] { "OffsetMin", "OffsetMax" })
							if (c.GetType().GetProperty(offset)?.GetValue(c) is Effekseer.Data.Value.Float off && off.Value != 0)
								Console.WriteLine(path + "/" + offset + " = " + F(off.Value));
					}
					continue;
				}
				if (TryDescribe(v, out var s)) { Console.WriteLine(path + " = " + s); continue; }
				var t = v.GetType();
				if (t.IsPrimitive || v is string || t.IsEnum) continue;
				if (t.Namespace != null && t.Namespace.StartsWith("Effekseer"))
					DumpObject(v, path, depth + 1, seen);
			}
			// A few saved values are public fields, not properties: upstream, only
			// each FCurve's Timeline. Percent (the default) reads the key frames
			// as % of the node's life, Time reads them as frames. Every node has
			// some 28 unused curve slots, so it is printed only beside keys.
			if (!keyed) return;
			foreach (var field in o.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
			{
				var io = field.GetCustomAttributes(typeof(IOAttribute), true).FirstOrDefault() as IOAttribute;
				if (io != null && !io.Export) continue;
				object v = field.GetValue(o);
				if (v == null) continue;
				string path = prefix.Length == 0 ? field.Name : prefix + "/" + field.Name;
				if (TryDescribe(v, out var s)) Console.WriteLine(path + " = " + s);
			}
		}
	}
}
