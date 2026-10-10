#!/usr/bin/env python3
"""
efkefc_dump.py — decode an Effekseer .efkefc project file's editable
node tree (the "EDIT" chunk) into readable XML.

Verified against effekseer/Effekseer's own EffekseerCore/IO/EfkEfc.cs
and Utils/BinaryReader.cs / Zlib.cs (source of truth for the format).

File layout:
    4 bytes  "EFKE" magic
    4 bytes  int32 little-endian exporter version
    N chunks, each:
        4 bytes  chunk tag (ASCII, e.g. "INFO" / "EDIT" / "BIN_")
        4 bytes  int32 little-endian chunk size
        <size> bytes  chunk payload

The EDIT chunk payload is a standard zlib stream (RFC1950 — header +
deflate + big-endian adler32, which is exactly what Python's zlib
module expects) wrapping a custom key/value-deduplicated binary
encoding of an XML document:
    int16  key count, then that many (utf8 string, int16 id) pairs
    int16  value count, then that many (utf8 string, int16 id) pairs
    a recursive element tree: int16 child count, then per child:
        int16 name-key-id
        int32 has-value flag, and if set: int16 value-id
        int32 has-children flag, and if set: nested element tree

Usage:
    python3 efkefc_dump.py path/to/effect.efkefc [output.xml]
"""
import struct
import sys
import zlib


class _Reader:
    def __init__(self, buf: bytes):
        self.buf = buf
        self.off = 0

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.buf, self.off)[0]
        self.off += 2
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.buf, self.off)[0]
        self.off += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.buf, self.off)[0]
        self.off += 4
        return v

    def bool32(self) -> bool:
        return self.i32() > 0

    def str_u16len(self) -> str:
        # Matches BinaryReader.Get(ref string, Encoding.UTF8, zeroEnd=false, bufLenSize=2)
        length = self.u16()
        s = self.buf[self.off:self.off + length].decode("utf-8", errors="replace")
        self.off += length
        return s


def parse_chunks(data: bytes) -> dict:
    if data[0:4] != b"EFKE":
        raise ValueError("Not an .efkefc file (missing 'EFKE' magic)")
    version = struct.unpack_from("<i", data, 4)[0]

    pos = 8
    blocks = {}
    while pos < len(data):
        chunk = data[pos:pos + 4].decode("ascii", errors="replace")
        pos += 4
        size = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        blocks[chunk] = data[pos:pos + size]
        pos += size

    if pos != len(data):
        raise ValueError("Chunk parsing did not consume the whole file — format mismatch")

    return {"version": version, "blocks": blocks}


def decode_edit_chunk(edit_raw: bytes) -> list:
    decompressed = zlib.decompress(edit_raw)
    r = _Reader(decompressed)

    key_count = r.i16()
    keys = {}
    for _ in range(key_count):
        name = r.str_u16len()
        key = r.i16()
        keys[key] = name

    value_count = r.i16()
    values = {}
    for _ in range(value_count):
        val = r.str_u16len()
        key = r.i16()
        values[key] = val

    def decomp():
        elems = []
        n = r.i16()
        for _ in range(n):
            name = keys[r.i16()]
            value = values[r.i16()] if r.bool32() else None
            children = decomp() if r.bool32() else None
            elems.append((name, value, children))
        return elems

    tree = decomp()

    if r.off != len(decompressed):
        raise ValueError(
            f"Decoder left {len(decompressed) - r.off} unread bytes — format mismatch"
        )

    return tree


def tree_to_xml_lines(elems: list, indent: int = 0) -> list:
    out = []
    pad = "  " * indent
    for name, value, children in elems:
        if value is not None:
            out.append(f"{pad}<{name}>{value}</{name}>")
        elif children:
            out.append(f"{pad}<{name}>")
            out.extend(tree_to_xml_lines(children, indent + 1))
            out.append(f"{pad}</{name}>")
        else:
            out.append(f"{pad}<{name} />")
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    data = open(in_path, "rb").read()
    parsed = parse_chunks(data)

    print(f"exporter version: {parsed['version']}")
    for tag, buf in parsed["blocks"].items():
        print(f"  chunk {tag}: {len(buf)} bytes")

    edit_raw = parsed["blocks"].get("EDIT")
    if edit_raw is None:
        print("No EDIT chunk found — this file has no editable node tree.")
        sys.exit(1)

    tree = decode_edit_chunk(edit_raw)
    xml_lines = tree_to_xml_lines(tree)
    xml_text = "\n".join(xml_lines)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(xml_text)
        print(f"Wrote decoded tree to {out_path} ({len(xml_lines)} lines)")
    else:
        print(xml_text)


if __name__ == "__main__":
    main()
