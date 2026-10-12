import argparse
import json
import math
import os
import shutil
import sys

from PIL import Image

TILE = 512


# ---------------------------------------------------------------- manifest

def load_manifest(directory):
    with open(os.path.join(directory, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    for key in ("columns", "rows", "crop_width", "crop_height", "step_x", "step_y",
                "pixels_per_unit", "plan"):
        if key not in manifest:
            sys.exit(f"manifest.json has no field '{key}' (see notes: field names are guessed).")
    return manifest


def frame_path(directory, index):
    return os.path.join(directory, f"frame_{index:06d}.png")


def build_grid(manifest, directory):
    """Return {(column, row): frame index}. Capture order does not matter."""
    columns, rows = manifest["columns"], manifest["rows"]
    grid = {}
    frames = manifest.get("frames", [])
    if frames and all("row" in f and "column" in f for f in frames):
        for index, f in enumerate(frames):
            grid[(f["column"], f["row"])] = index
    else:
        # Fallback: frames stored row by row, north to south.
        print("warning: frames have no row/column fields; assuming index = row*columns+column")
        for row in range(rows):
            for column in range(columns):
                grid[(column, row)] = row * columns + column

    missing = [(c, r) for r in range(rows) for c in range(columns)
               if (c, r) not in grid or not os.path.isfile(frame_path(directory, grid[(c, r)]))]
    if missing:
        sys.exit(f"Capture is incomplete: {len(missing)} frames missing, first: {missing[0]}")
    return grid


def grid_origin(manifest):
    """World coordinates of the top-left corner (xmin, ymax) of the mosaic."""
    if "origin_x" in manifest and "origin_y" in manifest:
        return manifest["origin_x"], manifest["origin_y"]
    frames = manifest.get("frames", [])
    for f in frames:
        if f.get("row") == 0 and f.get("column") == 0 and "x" in f and "y" in f:
            return f["x"] - manifest["step_x"] / 2, f["y"] + manifest["step_y"] / 2
    sys.exit("Cannot find the grid origin (no origin_x/origin_y, no x/y on frame 0,0).")


# ---------------------------------------------------------------- assembly

def read_cell(directory, grid, manifest, column, row):
    """The centered crop_width x crop_height rectangle of one frame."""
    img = Image.open(frame_path(directory, grid[(column, row)])).convert("RGBA")
    cw, ch = manifest["crop_width"], manifest["crop_height"]
    left = (img.width - cw) // 2
    top = (img.height - ch) // 2
    return img.crop((left, top, left + cw, top + ch))


def assemble_region(directory, grid, manifest, left, top, width, height):
    """RGBA image of the mosaic region [left, left+width) x [top, top+height), in mosaic pixels."""
    cw, ch = manifest["crop_width"], manifest["crop_height"]
    mosaic_w, mosaic_h = manifest["columns"] * cw, manifest["rows"] * ch
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    right, bottom = min(left + width, mosaic_w), min(top + height, mosaic_h)
    if right <= left or bottom <= top:
        return out
    for row in range(top // ch, (bottom - 1) // ch + 1):
        for column in range(left // cw, (right - 1) // cw + 1):
            cell = read_cell(directory, grid, manifest, column, row)
            cell_left, cell_top = column * cw, row * ch
            x0, x1 = max(left, cell_left), min(right, cell_left + cw)
            y0, y1 = max(top, cell_top), min(bottom, cell_top + ch)
            part = cell.crop((x0 - cell_left, y0 - cell_top, x1 - cell_left, y1 - cell_top))
            out.paste(part, (x0 - left, y0 - top))
    return out


def max_zoom(width, height):
    zoom, side = 0, TILE
    while side < max(width, height):
        side *= 2
        zoom += 1
    return zoom


def reduce_children(children):
    """children: {(dx, dy): 512x512 RGBA tile}. Alpha-weighted box filter into one tile."""
    out = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    for (dx, dy), tile in children.items():
        # "RGBa" is premultiplied alpha, so transparent padding doesn't darken the edges.
        small = tile.convert("RGBa").resize((TILE // 2, TILE // 2), Image.BOX).convert("RGBA")
        out.paste(small, (dx * TILE // 2, dy * TILE // 2))
    return out


# ------------------------------------------------------------------ tiles

def save_tile(output, z, x, y, tile):
    png_dir = os.path.join(output, "pyramid", str(z), str(y))
    webp_dir = os.path.join(output, "tiles", str(z), str(y))
    os.makedirs(png_dir, exist_ok=True)
    os.makedirs(webp_dir, exist_ok=True)
    tile.save(os.path.join(png_dir, f"{x}.png"))
    tile.save(os.path.join(webp_dir, f"{x}.webp"), "WEBP", lossless=True)


def stitch(directory, output, viewer):
    manifest = load_manifest(directory)
    grid = build_grid(manifest, directory)
    if os.path.exists(output):
        sys.exit("Output directory already exists. Choose a new directory; source frames are retained.")
    os.makedirs(output)

    width = manifest["columns"] * manifest["crop_width"]
    height = manifest["rows"] * manifest["crop_height"]
    zoom = max_zoom(width, height)

    for z in range(zoom, -1, -1):
        scale = 1 << (zoom - z)
        nx = -(-width // (TILE * scale))
        ny = -(-height // (TILE * scale))
        print(f"Zoom {z}: {nx} x {ny} tiles")
        for y in range(ny):
            for x in range(nx):
                if z == zoom:
                    tile = assemble_region(directory, grid, manifest, x * TILE, y * TILE, TILE, TILE)
                else:
                    children = {}
                    for dy in (0, 1):
                        for dx in (0, 1):
                            path = os.path.join(output, "pyramid", str(z + 1),
                                                str(y * 2 + dy), f"{x * 2 + dx}.png")
                            if os.path.isfile(path):
                                children[(dx, dy)] = Image.open(path).convert("RGBA")
                    tile = reduce_children(children)
                save_tile(output, z, x, y, tile)

    origin_x, origin_y = grid_origin(manifest)
    plan = manifest["plan"]
    metadata = {
        "version": 1,
        "scene": plan.get("scene", ""),
        "projection": "perspective-center-crops",
        "width": width,
        "height": height,
        "max_zoom": zoom,
        "tile_size": TILE,
        "xmin": origin_x,
        "ymin": origin_y - manifest["rows"] * manifest["step_y"],
        "xmax": origin_x + manifest["columns"] * manifest["step_x"],
        "ymax": origin_y,
        "pixels_per_unit": manifest["pixels_per_unit"],
        "ground": plan.get("ground", 0),
        "camera_z": plan.get("z", 0),
        "tile_url": "tiles/{z}/{y}/{x}.webp",
    }
    with open(os.path.join(output, "map.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    if viewer and os.path.isfile(viewer):
        shutil.copyfile(viewer, os.path.join(output, "index.html"))
    else:
        print("note: no map_viewer.html given; index.html not written (use --viewer)")
    print(f"Map ready: {os.path.join(output, 'map.json')}")


# ------------------------------------------------------------- single image

def export_image(directory, output, max_side, quality):
    manifest = load_manifest(directory)
    grid = build_grid(manifest, directory)
    width = manifest["columns"] * manifest["crop_width"]
    height = manifest["rows"] * manifest["crop_height"]
    ext = os.path.splitext(output)[1].lower()
    jpeg = ext in (".jpg", ".jpeg")
    if not jpeg and ext != ".png":
        sys.exit("Output must use .jpg, .jpeg or .png.")
    limit = 32768 if jpeg else 16384

    # Same idea as the Jai layout: shrink each cell by an integer divisor until it fits.
    cw, ch = manifest["crop_width"], manifest["crop_height"]
    divisor = 1
    while True:
        cell_w, cell_h = -(-cw // divisor), -(-ch // divisor)
        w, h = manifest["columns"] * cell_w, manifest["rows"] * cell_h
        if w <= (max_side or limit) and h <= (max_side or limit):
            break
        if not max_side:
            sys.exit("Single image exceeds its size limit. Set --max-side to resize, or export tiles.")
        divisor += 1

    print(f"Assembling {w} x {h} pixels")
    image = Image.new("RGB", (w, h))
    for row in range(manifest["rows"]):
        print(f"Row {row + 1} / {manifest['rows']}")
        for column in range(manifest["columns"]):
            cell = read_cell(directory, grid, manifest, column, row)
            if (cell_w, cell_h) != (cw, ch):
                cell = cell.resize((cell_w, cell_h), Image.BOX)
            image.paste(cell.convert("RGB"), (column * cell_w, row * cell_h))

    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    temporary = output + ".tmp"
    if jpeg:
        image.save(temporary, quality=quality, format="JPEG")
    else:
        image.save(temporary, format="PNG")
    os.replace(temporary, output)  # keep the old map if saving failed
    print(f"Map ready: {output} ({w} x {h})")


def main():
    parser = argparse.ArgumentParser(
        description="Stitch captured frames into a tile pyramid or a single image.",
        epilog="examples:\n"
               "  python stitch_map.py map/capture-world --tiles map/tiles-out --viewer scripts/map_viewer.html\n"
               "  python stitch_map.py map/capture-world --image map/map.jpg --max-side 32768 --quality 92\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", metavar="CAPTURE_DIR",
                        help="capture folder with manifest.json and frame_NNNNNN.png")
    parser.add_argument("--tiles", metavar="NEW_DIR",
                        help="write a tile pyramid (PNG + lossless WebP) and map.json here; "
                             "the folder must not exist")
    parser.add_argument("--viewer", metavar="HTML",
                        help="map_viewer.html to copy as index.html (with --tiles)")
    parser.add_argument("--image", metavar="FILE",
                        help="write one .jpg/.jpeg/.png instead of tiles")
    parser.add_argument("--max-side", type=int, default=0, metavar="PIXELS",
                        help="shrink the single image to at most this size (0 = native)")
    parser.add_argument("--quality", type=int, default=92, metavar="1-100",
                        help="JPEG quality (default 92)")
    args = parser.parse_args()

    if bool(args.tiles) == bool(args.image):
        parser.error("choose exactly one of --tiles or --image")
    if args.tiles:
        stitch(args.directory, args.tiles, args.viewer)
    else:
        export_image(args.directory, args.image, args.max_side, args.quality)


if __name__ == "__main__":
    main()
