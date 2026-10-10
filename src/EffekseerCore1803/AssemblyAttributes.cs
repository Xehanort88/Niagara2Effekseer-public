using System.Runtime.CompilerServices;

// Effekseer.IO.EfkEfc is internal to EffekseerCore. The audit needs it to read a
// .efkefc's EDIT chunk straight off disk without building a node tree — the only
// way to compare files rather than in-process trees.
//
// This lives in our wrapper project rather than in external/, so the
// checkout stays byte-identical to the tag.
[assembly: InternalsVisibleTo("Niagara2Efk")]
