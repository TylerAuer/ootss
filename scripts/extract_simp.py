import os
import struct
import sys

def extract(package_path, out_dir):
    data = open(package_path, "rb").read()

    magic, version, flags, toc = struct.unpack_from("<4sIIxxxxxxxxQ", data, 0)[0:4] if False else (
        data[:4], *struct.unpack_from("<II", data, 4), struct.unpack_from("<Q", data, 16)[0])
    if magic != b"simp" or version != 1 or flags != 0:
        sys.exit("Not a Simple_Package v1 file.")
    if data[toc:toc + 4] != b"toc!":
        sys.exit("Missing toc! signature.")

    count = struct.unpack_from("<Q", data, toc + 8)[0]
    pos = toc + 64
    root = os.path.abspath(out_dir)

    for _ in range(count):
        length = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        name = data[pos:pos + length].decode("utf-8")
        pos += length + 1  # skip name + null terminator
        size, offset = struct.unpack_from("<QQ", data, pos)
        pos += 16

        # Entry names may use backslashes; refuse paths that escape out_dir.
        path = os.path.abspath(os.path.join(root, *name.replace("\\", "/").split("/")))
        if os.path.commonpath([root, path]) != root:
            sys.exit(f"Unsafe entry name: {name}")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data[offset:offset + size])
        print(f"{name} ({size} bytes)")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python extract_simp.py <file.package> <output_dir>")
    extract(sys.argv[1], sys.argv[2])
