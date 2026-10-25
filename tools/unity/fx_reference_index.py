#!/usr/bin/env python3
"""Read Unity game B's shipped FX prefab reference cache without unpacking its template.

Examples:
    py fx_reference_index.py --game "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Unity Game B"
    py fx_reference_index.py --archive "D:\\...\\WhRtModificationTemplate.tar" --bundle-map "AssetToBundle_RT.txt" --query plasma
    py fx_reference_index.py --json BlueprintUnityObjectReferences.json --field FxPrefab --field PrefabLink

Optional --bundle-map format: 32-character asset GUID:bundle-name (historical community map; a tab, comma or
space instead of the colon is accepted too). The map may not match the installed game build; matched bundle
names are candidates only.
"""

import argparse
import csv
import json
import re
import tarfile
from collections import Counter
from pathlib import Path

CACHE_SUFFIX = "/Assets/ModTemplateCaches/BlueprintUnityObjectReferences.json"
FIELDS = (
    "BlueprintName", "BlueprintGuid", "FieldName", "ObjectPath", "Guid", "FileId",
    "ScriptGuid", "ScriptFileId", "CandidateBundle", "MappingStatus",
)
MAP_LINE = re.compile(r"^([0-9a-fA-F]{32})\s*[:\t,= ]\s*(\S.*)$")


def rows_of(data, source):
    rows = data.get("dataList") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Expected a JSON object with a dataList array in {source}")
    return rows


def find_cache(archive_path: Path):
    """(member name, rows, raw bytes) of the cache inside the template tar."""
    with tarfile.open(archive_path, mode="r:*") as tar:
        # Iterator stops as soon as the wanted cache appears; no full unpacking.
        for member in tar:
            name = "/" + member.name.replace("\\", "/").lstrip("./")
            if member.isfile() and name.endswith(CACHE_SUFFIX):
                source = tar.extractfile(member)
                if source is None:
                    break
                raw = source.read()
                return member.name, rows_of(json.loads(raw.decode("utf-8-sig")), member.name), raw
    raise FileNotFoundError(f"Did not find {CACHE_SUFFIX} inside {archive_path}")


def load_mapping(path):
    if not path:
        return {}
    mapping = {}
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            m = MAP_LINE.match(line.strip())
            if m:
                mapping[m.group(1).lower()] = m.group(2).strip()
    if not mapping:
        raise ValueError(f"No GUID:bundle entries recognized in {path}")
    return mapping


def build_rows(records, mapping, query):
    output = []
    for record in records:
        row = {field: record.get(field, "") for field in FIELDS}
        guid = str(row["Guid"]).lower()
        candidate_bundle = mapping.get(guid)
        row["CandidateBundle"] = candidate_bundle or ""
        row["MappingStatus"] = ("historical-map-match" if candidate_bundle else
                                "not-in-provided-map" if mapping else "no-map-provided")
        if not query or query.lower() in (str(row["BlueprintName"]) + " " + str(row["ObjectPath"])).lower():
            output.append(row)
    output.sort(key=lambda r: (str(r["BlueprintName"]).casefold(), str(r["ObjectPath"]).casefold()))
    return output


PREFAB_FIELDS = ("Category", "Prefab", "Blueprints", "ExampleBlueprints", "Container", "ObjectPath", "Guid",
                 "CandidateBundle")


def prefab_rows(rows):
    """One row per referenced prefab, most used first: its category (the folder under Prefabs/), how many
    distinct blueprints use it with a few of their names, and `Container`, the lower-case file name to give
    unity_dump --container / unityfx.ps1 -Container."""
    by_path = {}
    for r in rows:
        entry = by_path.setdefault(r["ObjectPath"], {"blueprints": [], "row": r})
        if r["BlueprintName"] not in entry["blueprints"]:
            entry["blueprints"].append(r["BlueprintName"])
    out = []
    for path, entry in by_path.items():
        parts = str(path).replace("\\", "/").split("/")
        category = parts[parts.index("Prefabs") + 1] if "Prefabs" in parts[:-2] else "/".join(parts[1:-1])
        name = parts[-1]
        out.append({"Category": category, "Prefab": name.rsplit(".", 1)[0], "Blueprints": len(entry["blueprints"]),
                    "ExampleBlueprints": "; ".join(entry["blueprints"][:5]), "Container": name.lower(),
                    "ObjectPath": path, "Guid": entry["row"]["Guid"],
                    "CandidateBundle": entry["row"]["CandidateBundle"]})
    out.sort(key=lambda r: (-r["Blueprints"], r["Category"].casefold(), r["Prefab"].casefold()))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    locator = parser.add_mutually_exclusive_group(required=True)
    locator.add_argument("--game", type=Path, help="Path to Unity game B game root")
    locator.add_argument("--archive", type=Path, help="Path to WhRtModificationTemplate.tar")
    locator.add_argument("--json", type=Path, help="An already extracted BlueprintUnityObjectReferences.json")
    parser.add_argument("--bundle-map", type=Path, help="Optional community AssetToBundle_RT.txt file")
    parser.add_argument("--out", type=Path, default=Path("fx_reference_index.csv"), help="Output CSV")
    parser.add_argument("--query", default="", help="Filter rows by name or path, case-insensitive")
    parser.add_argument("--field", action="append", default=None,
                        help="FieldName to keep (repeatable; default FxPrefab)")
    parser.add_argument("--save-cache", type=Path, help="Also write the cache JSON from the archive to this file")
    args = parser.parse_args(argv)

    if args.json:
        if not args.json.is_file():
            parser.error(f"Cache file does not exist: {args.json}")
        raw = args.json.read_bytes()
        source, cache_member = args.json, "(loose file)"
        records = rows_of(json.loads(raw.decode("utf-8-sig")), args.json)
    else:
        archive = args.archive or args.game / "Modding" / "WhRtModificationTemplate.tar"
        if not archive.is_file():
            parser.error(f"Template archive does not exist: {archive}")
        cache_member, records, raw = find_cache(archive)
        source = archive
    if args.save_cache:
        args.save_cache.parent.mkdir(parents=True, exist_ok=True)
        args.save_cache.write_bytes(raw)
    mapping = load_mapping(args.bundle_map)

    wanted = set(args.field or ["FxPrefab"])
    field_counts = Counter(str(r.get("FieldName")) for r in records if isinstance(r, dict))
    records = [r for r in records if isinstance(r, dict) and r.get("FieldName") in wanted]
    output = build_rows(records, mapping, args.query)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output)
    prefabs_path = args.out.with_name(args.out.stem + "_prefabs.csv")
    prefabs = prefab_rows(output)
    with open(prefabs_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREFAB_FIELDS)
        writer.writeheader()
        writer.writerows(prefabs)

    label = "/".join(sorted(wanted))
    print(f"{'Cache' if args.json else 'Archive'}: {source}")
    print(f"Cache member: {cache_member}")
    print(f"Field names in cache (top 15): {dict(field_counts.most_common(15))}")
    print(f"All {label} references: {len(records)}")
    print(f"Exported after query filter: {len(output)}")
    print(f"Unique referenced asset GUIDs in export: {len({str(r['Guid']).lower() for r in output})}")
    if mapping:
        print(f"GUIDs in supplied historical map: {len(mapping)}")
        print(f"References with candidate bundle: {sum(bool(r['CandidateBundle']) for r in output)}")
        print("Candidate bundle counts:", dict(Counter(r["CandidateBundle"] for r in output if r["CandidateBundle"]).most_common(12)))
        print("NOTE: historical bundle maps need validation against your installed game build.")
    if args.save_cache:
        print(f"Cache saved: {args.save_cache.resolve()}")
    print(f"Exact duplicate rows in export: {len(output) - len({tuple(r.values()) for r in output})}")
    print(f"Output: {args.out.resolve()}")
    print(f"Prefabs ({len(prefabs)}, one row each, most used first): {prefabs_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
