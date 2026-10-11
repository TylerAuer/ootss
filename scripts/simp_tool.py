import argparse
import json
import os
import struct
import sys

# ---------------------------------------------------------------- constants

# Members whose value is a 4-byte prefix + float32.
FLOAT_MEMBERS = {0, 1, 2, 6, 7, 8, 9, 0x16, 0x17, 0x18, 0x21, 0x22, 0x23}

# String members: 8-byte prefix + u64 length + UTF-8 bytes.
# 0x1F is the base Entity member (mesh / character name).
STRING_MEMBERS_BASE = {0x1F}
STRING_MEMBERS_BY_TYPE = {
    "Level_Entry": {0x1000},
    "Guy": {0x100E, 0x1011},
    "Settings": {0x1054},
}

LEVEL_NAME_MEMBER = 0x1000
POSITION_MEMBERS = {0: "x", 1: "y", 2: "z"}

# Mixed level sets: used for "area" only if no regular set lists the level.
GENERIC_LEVEL_SETS = {"overworld", "intro", "intro_hard_puzzles"}

EPILOG = """\
examples:
  List everything inside the package:
    python simp_tool.py list levels.package

  Unpack all entries (keeps the folders from the entry names):
    python simp_tool.py extract levels.package --output-dir unpacked

  Unpack only some entries, and also write a decoded .json next to each .entities:
    python simp_tool.py extract levels.package --name mirror_1.entities heroes1.level_set --decode-entities

  List the entities of one level:
    python simp_tool.py entities levels.package --entry mirror_1.entities --list

  Dump all Mirror entities of one level (.bin + .json per entity):
    python simp_tool.py entities levels.package --entry mirror_1.entities --type Mirror

  Dump the overworld Level_Entry that points at heroes1_1:
    python simp_tool.py entities levels.package --entry overworld.entities --contains heroes1_1

  Export all level entries of the overworld as GeoJSON markers:
    python simp_tool.py markers levels.package --output level_entries.geojson

Run 'python simp_tool.py COMMAND -h' for the options of one command.
"""


# ------------------------------------------------------------ binary helpers

def read_u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def read_u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def read_u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def read_f32(data, offset):
    return struct.unpack_from("<f", data, offset)[0]


# ------------------------------------------------------------------ package

def read_package(data):
    """Return {entry_name: bytes} for a Simple_Package v1 file."""
    if data[:4] != b"simp":
        sys.exit("Not a Simple_Package file (missing 'simp' header).")

    toc_offset = read_u64(data, 16)
    entry_count = read_u64(data, toc_offset + 8)
    cursor = toc_offset + 64

    entries = {}
    for _ in range(entry_count):
        name_length = read_u32(data, cursor)
        cursor += 4
        name = data[cursor:cursor + name_length].decode("utf-8")
        cursor += name_length + 1  # skip the NUL terminator

        size = read_u64(data, cursor)
        offset = read_u64(data, cursor + 8)
        cursor += 16

        entries[name] = data[offset:offset + size]
    return entries


def normalize(name):
    return name.replace("\\", "/")


def file_name_of(entry_name):
    return normalize(entry_name).rsplit("/", 1)[-1]


def entry_matches(entry_name, wanted):
    """True if 'wanted' is the entry's full path or just its file name (case-insensitive)."""
    entry = normalize(entry_name).lower()
    wanted = normalize(wanted).lower()
    return entry == wanted or file_name_of(entry) == file_name_of(wanted)


def find_entry(entries, wanted):
    for name in entries:
        if entry_matches(name, wanted):
            return name
    return None


def load_source(path):
    """Return {entry_name: bytes} for a .package, or a single-entry dict for a plain file."""
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path}")
    data = open(path, "rb").read()
    if data[:4] == b"simp":
        return read_package(data)
    return {os.path.basename(path): data}


# ----------------------------------------------------------------- entities

def parse_entities(data):
    """Return a list of (entity_id, type_name, diff_bytes)."""
    if data[:4] != b"enty":
        sys.exit("Not an .entities file (missing 'enty' header).")

    cursor = 4 + 2          # magic + u16 version
    cursor += 8             # header_value
    type_count = read_u64(data, cursor)
    cursor += 8

    type_names = []
    for _ in range(type_count):
        name_length = read_u64(data, cursor)
        type_names.append(data[cursor + 8:cursor + 8 + name_length].decode("utf-8"))
        cursor += 8 + name_length + 8  # length + name + aux

    record_count = read_u32(data, cursor)
    cursor += 4

    records = []
    for _ in range(record_count):
        entity_id, type_index, diff_size = struct.unpack_from("<IHI", data, cursor)
        cursor += 10
        records.append((entity_id, type_names[type_index], data[cursor:cursor + diff_size]))
        cursor += diff_size
    return records


def is_string_member(type_name, member):
    if member in STRING_MEMBERS_BASE:
        return True
    return member in STRING_MEMBERS_BY_TYPE.get(type_name, ())


def skip_unknown_value(diff, start):
    """Unknown member: its size isn't known, so guess that it ends where the
    next 0xFD <member> marker begins. This can be wrong inside float data."""
    position = start
    while True:
        position = diff.find(b"\xfd", position + 1)
        if position < 0:
            return len(diff)
        looks_like_marker = position + 3 <= len(diff) and read_u16(diff, position + 1) < 0x1100
        if looks_like_marker:
            return position


def decode_diff(type_name, diff):
    """Decode a record's diff into a list of member dicts."""
    members = []
    cursor = 0

    while cursor < len(diff):
        # The first member has no 0xFD marker; every later one does.
        if cursor == 0:
            member = read_u16(diff, 0)
            cursor = 2
        elif diff[cursor] == 0xFD:
            member = read_u16(diff, cursor + 1)
            cursor += 3
        else:
            members.append({"raw": diff[cursor:].hex()})
            break

        if member in FLOAT_MEMBERS:
            members.append({
                "member": member,
                "prefix": diff[cursor:cursor + 4].hex(),
                "float": read_f32(diff, cursor + 4),
            })
            cursor += 8

        elif is_string_member(type_name, member):
            length = read_u64(diff, cursor + 8)
            text = diff[cursor + 16:cursor + 16 + length].decode("utf-8", "replace")
            members.append({"member": member, "string": text})
            cursor += 16 + length

        elif type_name == "Guy" and member == 0x1004:
            members.append({"member": member, "u32": read_u32(diff, cursor + 4)})
            cursor += 8

        else:
            end = skip_unknown_value(diff, cursor)
            members.append({"member": member, "raw": diff[cursor:end].hex()})
            cursor = end

    return members


def entity_to_dict(entity_id, type_name, diff):
    return {
        "id": entity_id,
        "type": type_name,
        "diff_size": len(diff),
        "members": decode_diff(type_name, diff),
    }


# ------------------------------------------------------------------ markers

def parse_level_set(text):
    """Return the level names listed in a .level_set file, in order."""
    levels = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()   # drop comments
        if not line or line.startswith("[") or line.startswith("*"):
            continue                           # version, glyphs, *choice, ...
        levels.append(line.split()[0])
    return levels


def read_level_areas(entries):
    """Return {level_name_lowercase: [level set names]}."""
    areas = {}
    for entry_name, data in sorted(entries.items()):
        filename = file_name_of(entry_name)
        if not filename.lower().endswith(".level_set"):
            continue
        set_name = filename[:-len(".level_set")]
        for level in parse_level_set(data.decode("utf-8-sig", "replace")):
            sets = areas.setdefault(level.lower(), [])
            if set_name not in sets:
                sets.append(set_name)
    return areas


def pick_area(set_names):
    """Prefer a regular level set over a generic mixed one."""
    if not set_names:
        return None
    return next((s for s in set_names if s not in GENERIC_LEVEL_SETS and 'drafts' not in s),"UNDEFINED")

def read_level_entries(entries):
    """Return a list of dicts: level name, x, y, z."""
    name = find_entry(entries, "overworld.entities")
    if name is None:
        sys.exit("overworld.entities not found in the package.")

    result = []
    for _, type_name, diff in parse_entities(entries[name]):
        if type_name != "Level_Entry":
            continue

        # Diffs only store non-default values, so a missing coordinate is 0.
        position = {"x": 0.0, "y": 0.0, "z": 0.0}
        level_name = None
        for member in decode_diff(type_name, diff):
            number = member.get("member")
            if number in POSITION_MEMBERS:
                position[POSITION_MEMBERS[number]] = member["float"]
            elif number == LEVEL_NAME_MEMBER:
                level_name = member["string"]

        result.append({"level": level_name, **position})
    return result


def build_geojson(level_entries, areas):
    features = []
    for entry in sorted(level_entries, key=lambda e: e["level"] or ""):
        set_names = areas.get((entry["level"] or "").lower(), [])
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [entry["x"], entry["y"], entry["z"]],
            },
            "properties": {
                "type": "level",
                "name": entry["level"],
                "area": pick_area(set_names),
            },
        })
    return {"type": "FeatureCollection", "features": features}


# ----------------------------------------------------------------- commands

def command_list(args):
    entries = load_source(args.source)
    for name in sorted(entries):
        print(f"{len(entries[name]):>10}  {name}")
    print(f"{len(entries)} entries")


def safe_output_path(root, entry_name):
    """Join an entry name onto root, refusing names that escape it."""
    target = os.path.abspath(os.path.join(root, normalize(entry_name).lstrip("/")))
    if os.path.commonpath([root, target]) != root:
        return None
    return target


def write_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def command_extract(args):
    entries = load_source(args.source)
    root = os.path.abspath(args.output_dir)

    if args.name:
        selected = [n for n in entries if any(entry_matches(n, w) for w in args.name)]
        for wanted in args.name:
            if not any(entry_matches(n, wanted) for n in entries):
                print(f"not found in package: {wanted}")
    else:
        selected = list(entries)

    if not selected:
        sys.exit("Nothing to extract.")

    for name in sorted(selected):
        target = safe_output_path(root, name)
        if target is None:
            print(f"skipped unsafe entry name: {name}")
            continue

        write_file(target, entries[name])
        print(f"{name} ({len(entries[name])} bytes)")

        if args.decode_entities and name.lower().endswith(".entities"):
            records = parse_entities(entries[name])
            decoded = [entity_to_dict(*record) for record in records]
            write_file(target + ".json",
                       json.dumps(decoded, indent=1, ensure_ascii=False).encode("utf-8"))
            print(f"  decoded {len(decoded)} entities -> {target}.json")


def record_matches(args, entity_id, type_name, diff):
    if args.id is not None and entity_id != args.id:
        return False
    if args.type and type_name.lower() != args.type.lower():
        return False
    if args.contains and args.contains.encode() not in diff:
        return False
    return True


def command_entities(args):
    entries = load_source(args.source)

    if len(entries) == 1:
        data = next(iter(entries.values()))
    else:
        if not args.entry:
            print("Entities files in this package (pass one with --entry):")
            for name in sorted(entries):
                if name.lower().endswith(".entities"):
                    print("  " + file_name_of(name))
            return
        name = find_entry(entries, args.entry)
        if name is None:
            sys.exit(f"Entry not found in package: {args.entry}")
        data = entries[name]

    records = parse_entities(data)
    has_filter = args.contains or args.type or args.id is not None
    selected = [r for r in records if record_matches(args, *r)]

    if args.list:
        for entity_id, type_name, diff in selected:
            print(entity_id, type_name, len(diff))
        return

    if not (has_filter or args.all):
        args.parser.error("choose what to dump: --contains, --type, --id or --all "
                          "(or use --list to just see the entities)")
    if not selected:
        sys.exit("No entities matched.")

    os.makedirs(args.output_dir, exist_ok=True)
    for entity_id, type_name, diff in selected:
        base = os.path.join(args.output_dir, f"{type_name}_{entity_id}")
        with open(base + ".bin", "wb") as f:
            f.write(diff)
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(entity_to_dict(entity_id, type_name, diff), f, indent=1, ensure_ascii=False)
        print(f"{type_name} id={entity_id} ({len(diff)} bytes) -> {base}.json / .bin")


def command_markers(args):
    entries = load_source(args.source)
    level_entries = read_level_entries(entries)
    areas = read_level_areas(entries)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(build_geojson(level_entries, areas), f, indent=2, ensure_ascii=False)

    without_area = [e["level"] for e in level_entries
                    if not areas.get((e["level"] or "").lower())]
    print(f"Wrote {len(level_entries)} level entries to {args.output}")
    if without_area:
        print(f"{len(without_area)} without an area (not in any level set): "
              + ", ".join(str(n) for n in without_area))


# --------------------------------------------------------------------- main

class HelpParser(argparse.ArgumentParser):
    """Prints the full help (options + examples) on any usage error."""

    def error(self, message):
        sys.stderr.write(f"error: {message}\n\n")
        self.print_help(sys.stderr)
        sys.exit(2)


def add_command(commands, name, help_text, example, run):
    sub = commands.add_parser(
        name,
        help=help_text,
        description=help_text,
        epilog="example:\n  " + example,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub.set_defaults(run=run, parser=sub)
    return sub


def build_parser():
    parser = HelpParser(
        description="Tools for Simple_Package (.package) files and their .entities data.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=HelpParser)

    sub = add_command(commands, "list", "list the entries of a package",
                      "python simp_tool.py list levels.package", command_list)
    sub.add_argument("source", metavar="PACKAGE", help="path to a .package file")

    sub = add_command(commands, "extract", "unpack package entries to files",
                      "python simp_tool.py extract levels.package --output-dir unpacked",
                      command_extract)
    sub.add_argument("source", metavar="PACKAGE", help="path to a .package file")
    sub.add_argument("--name", metavar="NAME", nargs="+",
                     help="only these entries, by file name or full path "
                          "(e.g. mirror_1.entities heroes1.level_set); default: all")
    sub.add_argument("--output-dir", metavar="DIR", default="unpacked",
                     help="folder to write into (default: %(default)s)")
    sub.add_argument("--decode-entities", action="store_true",
                     help="also write <file>.entities.json with all decoded entities")

    sub = add_command(commands, "entities", "list or dump entity records of one level",
                      "python simp_tool.py entities levels.package "
                      "--entry overworld.entities --contains heroes1_1",
                      command_entities)
    sub.add_argument("source", metavar="PATH",
                     help="a .package file, or an extracted .entities file")
    sub.add_argument("--entry", metavar="NAME",
                     help="which .entities file inside the package, e.g. mirror_1.entities "
                          "(omit it to list the available ones)")
    sub.add_argument("--contains", metavar="TEXT",
                     help="only entities whose data contains this text")
    sub.add_argument("--type", metavar="TYPE",
                     help="only entities of this type, e.g. Mirror, Guy, Level_Entry")
    sub.add_argument("--id", type=int, metavar="NUMBER", help="only the entity with this id")
    sub.add_argument("--all", action="store_true", help="select every entity in the file")
    sub.add_argument("--list", action="store_true",
                     help="print 'id type size' per entity instead of writing files")
    sub.add_argument("--output-dir", metavar="DIR", default="entities_out",
                     help="folder for dumped files (default: %(default)s)")

    sub = add_command(commands, "markers", "export overworld level entries as GeoJSON points",
                      "python simp_tool.py markers levels.package --output level_entries.geojson",
                      command_markers)
    sub.add_argument("source", metavar="PACKAGE", help="path to levels.package")
    sub.add_argument("--output", metavar="FILE", default="level_entries.geojson",
                     help="GeoJSON file to write (default: %(default)s)")

    return parser


def main():
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return
    args = parser.parse_args()
    if not hasattr(args, "run"):
        parser.print_help()
        return
    args.run(args)


if __name__ == "__main__":
    main()
