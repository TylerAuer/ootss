import struct, sys

def read_status(path):
    d = open(path, "rb").read()
    off = 0

    def u32():
        nonlocal off
        v, = struct.unpack_from("<I", d, off); off += 4
        return v

    def string():
        nonlocal off
        n, = struct.unpack_from("<H", d, off)
        s = d[off+2:off+2+n].decode("utf-8", "replace"); off += 2 + n
        return s

    # ---- header: opaque (u64 33, two 8-byte hashes, some counters) ----
    print("header words:", [f"{w:#x}" for w in struct.unpack_from("<28I", d, 0)])
    off = 0x71
    print("u32 =", u32())                         # 1
    print("flags =", d[off], d[off+1]); off += 2  # 01 01
    print("str1 =", string())                     # flame_wand
    print("str2 =", string())                     # menu_combo_heroes_light_a

    # ---- level table: u32 count, then [u16 len][name][u8 flags] ----
    n = u32()
    print(f"\nLevels: {n}")
    levels = []
    for i in range(n):
        name = string()
        flags = d[off]; off += 1
        levels.append((name, flags))
        print(f"  [{i:3}] flags={flags:#04x} ({flags:04b})  {name}")

    # ---- trailing tables: u32 count + count * u32 ----
    for t in range(3):
        if off + 4 > len(d): break
        c = u32()
        vals = struct.unpack_from(f"<{c}I", d, off); off += 4 * c
        print(f"\nTable {t}: {c} values")
        for v in vals:
            print(f"  {v:#010x}")
    print(f"\nconsumed {off} of {len(d)} bytes")
    return levels

if __name__ == "__main__":
    read_status(sys.argv[1] if len(sys.argv) > 1 else
        r"C:\Users\user\Saved Games\Order of the Sinking Star\default\Status.sokoban_status")
