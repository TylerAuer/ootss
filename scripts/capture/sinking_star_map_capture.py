"""Capture a top-down map of the Order of the Sinking Star overworld (ground layer).

To capture different worlds, use `switch <level>` command in using ootss-cmd.

heroes1_1 - overworld
3d_intro_skipping - sky
push_down_chain - underground

Drives the running game from outside with ReadProcessMemory/WriteProcessMemory: no DLL injection
and no change to the exe. The game renders every tile itself through its own screenshot code;
this script only points the camera, takes the shots and stitches them.

What it does to the running game (restart the game afterwards to undo it all):
  - clears the overworld fog of war and hides the hero
  - freezes simulation time, forces the highest LOD, turns off planar reflections
  - draws every wrap copy of the overworld so the map edges are complete
  - freezes the camera straight down from far above with a narrow field of view (near-orthographic)
  - renders at the tile resolution, without HUD, and saves one game screenshot per tile

Usage (game running on the overworld, any save you don't care about):
  python sinking_star_map_capture.py [--px 128] [--tiles 8 8] [--out map_tiles] [--stitch-only] [--capture-only]

  --px      pixels per grid square in the final map
  --tiles   tile columns and rows; each tile must be a whole number of pixels
  --out     folder for the tiles and the stitched map

Needs: Windows, Python 3, `pip install pefile pillow`.

Pixel position of square (x, y)'s centre in the stitched map, with x0/y0 = the grid corner
minus half a square and H = grid rows:
  pixel_x = (x - x0) * px
  pixel_y = (y0 + H - y) * px          # north up

Known limits: pickup particles keep animating (they don't use the simulation clock); the game's
wrap copies leave out static terrain, so terrain overhanging a wrap join can miss a sliver there.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import math
import os
import re
import shutil
import struct
import sys
import time

import pefile

EXE_NAME = 'sinking_star.exe'

# Globals of the 2026-10-17 evening build (PE TimeDateStamp below), as addresses at the exe's
# preferred image base. Struct member offsets are read from the game's Type_Info instead.
BUILD_TIMESTAMP = 0x6AD32EAE
IMAGE_BASE = 0x140000000
SCREENSHOT_REQUEST = 0x140B693A8      # u8: 1 = take a screenshot at the end of the frame
RENDER_OVERRIDE = 0x140802B27         # u8 Render_Resolution; 7 = Custom_Screenshot
CUSTOM_WIDTH = 0x140802B30            # s64
CUSTOM_HEIGHT = 0x140802B38           # s64
SWAPCHAIN = 0x140B69EC8               # pointer: [0] = hWnd, [+8] = cached width
FINAL_TARGET = 0x140802CD0            # pointer to the render target the screenshot reads
CAMERA_MODE = 0x140B6A808             # s64: 0 follow, 1 debug free camera, 2 pose left alone
TIME_MULTIPLIER = 0x140802028         # f64 base simulation time multiplier
LOD_LEVEL2_THRESHOLD = 0x140802064    # f32
LOD_LEVEL1_THRESHOLD = 0x140802068    # f32
SHADOW_FADE_THRESHOLD = 0x14080205C   # f32
FORCE_MAP_LEVEL0 = 0x140802B55        # u8
REFLECTION_COUNT = 0x14108D338        # s64 live Graphics_Settings.reflection_count

# Camera pose inside the Entity_Manager.
MANAGER_CAMERA_POSITION = 0x3D98      # Vector3
MANAGER_CAMERA_ORIENTATION = 0x3DB0   # quaternion x, y, z, w
MANAGER_CAMERA_PROJECTION = 0x3DC0    # fov (radians), z_near, z_far

FINAL_TARGET_WIDTH = 0x38
FINAL_TARGET_HEIGHT = 0x3C
TOPDOWN_NORTH_UP = (-0.5, 0.5, 0.5, 0.5)
CAMERA_HEIGHT = 50000.0
FOG_CLEARED = 2
WM_SIZE = 0x0005

# ---------------------------------------------------------------------------------------------
# Process memory

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
LIST_MODULES_ALL = 0x3
MEM_COMMIT = 0x1000
PAGE_READWRITE = 0x04

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
user32 = ctypes.WinDLL('user32', use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
        ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
        ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wt.DWORD),
        ('szExeFile', ctypes.c_wchar * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p), ('AllocationProtect', wt.DWORD),
        ('PartitionId', wt.WORD), ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD), ('Protect', wt.DWORD),
        ('Type', wt.DWORD),
    ]


kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p), wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_wchar_p, wt.DWORD]
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]


def find_pid(name):
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    found = []
    ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        if entry.szExeFile.lower() == name.lower():
            found.append(entry.th32ProcessID)
        ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    if len(found) != 1:
        raise RuntimeError('expected one %s process, found %d' % (name, len(found)))
    return found[0]


class Game:
    def __init__(self):
        self.pid = find_pid(EXE_NAME)
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = kernel32.OpenProcess(access, False, self.pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.base = self.main_module_base()
        self.exe_path = self.image_path()
        pe_offset = self.u32(self.base + 0x3C)
        timestamp = self.u32(self.base + pe_offset + 8)
        if timestamp != BUILD_TIMESTAMP:
            raise RuntimeError('running build %08x is not the one this script was made for (%08x)' % (timestamp, BUILD_TIMESTAMP))

    def main_module_base(self):
        modules = (ctypes.c_void_p * 1024)()
        needed = wt.DWORD()
        if not psapi.EnumProcessModulesEx(self.handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), LIST_MODULES_ALL):
            raise ctypes.WinError(ctypes.get_last_error())
        name = ctypes.create_unicode_buffer(260)
        for module in modules[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
            psapi.GetModuleBaseNameW(self.handle, module, name, 260)
            if name.value.lower() == EXE_NAME:
                return module
        raise RuntimeError('main module not found')

    def image_path(self):
        buffer = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value

    def va(self, address):
        """Runtime address of an address at the exe's preferred image base."""
        return self.base + (address - IMAGE_BASE)

    def read(self, address, size):
        buffer = ctypes.create_string_buffer(size)
        done = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(done)) or done.value != size:
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw

    def write(self, address, data):
        done = ctypes.c_size_t()
        if not kernel32.WriteProcessMemory(self.handle, address, data, len(data), ctypes.byref(done)) or done.value != len(data):
            raise ctypes.WinError(ctypes.get_last_error())

    def u8(self, address): return self.read(address, 1)[0]
    def u32(self, address): return struct.unpack('<I', self.read(address, 4))[0]
    def s32(self, address): return struct.unpack('<i', self.read(address, 4))[0]
    def s64(self, address): return struct.unpack('<q', self.read(address, 8))[0]
    def u64(self, address): return struct.unpack('<Q', self.read(address, 8))[0]
    def f32(self, address): return struct.unpack('<f', self.read(address, 4))[0]

    def put_u8(self, address, value): self.write(address, bytes([value]))
    def put_s64(self, address, value): self.write(address, struct.pack('<q', value))
    def put_f32(self, address, value): self.write(address, struct.pack('<f', value))
    def put_f64(self, address, value): self.write(address, struct.pack('<d', value))

    def writable_regions(self):
        address = 0
        info = MEMORY_BASIC_INFORMATION()
        while kernel32.VirtualQueryEx(self.handle, address, ctypes.byref(info), ctypes.sizeof(info)):
            base = info.BaseAddress or 0
            if info.State == MEM_COMMIT and info.Protect == PAGE_READWRITE and info.RegionSize < 1 << 31:
                yield base, info.RegionSize
            address = base + info.RegionSize


# ---------------------------------------------------------------------------------------------
# Jai Type_Info, read from the exe file

TYPE_STRUCT = 7
TYPE_VARIANT = 18


class TypeInfo:
    def __init__(self, exe_path):
        self.pe = pefile.PE(exe_path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.image = self.pe.get_memory_mapped_image()
        self.structs = self.scan_structs()

    def q(self, address): return struct.unpack_from('<Q', self.image, address - self.base)[0]
    def d(self, address): return struct.unpack_from('<I', self.image, address - self.base)[0]

    def string(self, address):
        length = self.q(address)
        data = self.q(address + 8)
        if length <= 0 or length > 4096 or not data:
            return ''
        return self.image[data - self.base:data - self.base + length].decode('utf-8', 'replace')

    def scan_structs(self):
        """Type_Info_Struct records by name: tag 7, then size, then the name string."""
        found = {}
        for section in self.pe.sections:
            if section.Name.rstrip(b'\0') not in (b'.rdata', b'.data'):
                continue
            start = self.base + section.VirtualAddress
            data = self.image[section.VirtualAddress:section.VirtualAddress + section.Misc_VirtualSize]
            for offset in range(0, len(data) - 96, 8):
                if struct.unpack_from('<I', data, offset)[0] != TYPE_STRUCT:
                    continue
                size, name_length, name_data = struct.unpack_from('<qqQ', data, offset + 8)
                if not (0 < name_length < 64 and self.base <= name_data < self.base + len(self.image) and 0 <= size < 1 << 24):
                    continue
                name_bytes = self.image[name_data - self.base:name_data - self.base + name_length]
                if not re.fullmatch(rb'[A-Za-z_]\w*', name_bytes):
                    continue
                found.setdefault(name_bytes.decode(), []).append(start + offset)
        return found

    def struct_info(self, name):
        records = self.structs.get(name)
        if not records:
            raise RuntimeError('no Type_Info for struct %s' % name)
        return records[0]

    def offset(self, struct_name, member_name):
        record = self.struct_info(struct_name)
        count = self.q(record + 64)
        members = self.q(record + 72)
        for index in range(count):
            member = members + index * 64
            if self.string(member) == member_name:
                return self.q(member + 24)
        raise RuntimeError('%s has no member %s' % (struct_name, member_name))


# ---------------------------------------------------------------------------------------------
# Entities

def find_entities(game, types, type_names):
    """Live entities whose Entity.entity_type points at the named Type_Info: [(entity, manager)]."""
    entity_type = types.offset('Entity', 'entity_type')
    entity_manager = types.offset('Entity', 'entity_manager')
    wanted = {}
    for name in type_names:
        for record in types.structs.get(name, []):
            wanted[struct.pack('<Q', game.va(record))] = name
    found = {name: [] for name in type_names}
    for base, size in game.writable_regions():
        try:
            data = game.read(base, size)
        except OSError:
            continue
        for needle, name in wanted.items():
            position = data.find(needle)
            while position >= 0:
                if position % 8 == 0 and position >= entity_type:
                    entity_start = position - entity_type
                    manager = struct.unpack_from('<Q', data, entity_start + entity_manager)[0]
                    if manager:
                        found[name].append((base + entity_start, manager))
                position = data.find(needle, position + 1)
    return found


class Overworld:
    def __init__(self, game, types):
        self.game = game
        self.types = types
        entities = find_entities(game, types, ['Settings', 'Fog_Of_War', 'Guy'])

        grid = types.offset('Settings', 'grid')
        squares_x = types.offset('Gridlike', 'squares_x')
        squares_y = types.offset('Gridlike', 'squares_y')
        corner = types.offset('Gridlike', 'corner')
        # The overworld is the level with the largest grid.
        best_area = 0
        for settings, manager in entities['Settings']:
            columns = game.s64(settings + grid + squares_x)
            rows = game.s64(settings + grid + squares_y)

            #corner_x, corner_y, corner_z = struct.unpack('<3i', game.read(settings + grid + corner, 12))
            #print('columns,rows', columns, rows, 'corner', corner_x,corner_y,corner_z)

            if 0 < columns < 10000 and 0 < rows < 10000 and columns * rows > best_area:
                best_area = columns * rows
                self.settings = settings
                self.manager = manager
                self.squares_x = columns
                self.squares_y = rows

        if not best_area:
            raise RuntimeError('overworld Settings not found')
        corner_x, corner_y, corner_z = struct.unpack('<3i', game.read(self.settings + grid + corner, 12))
        # Whole-number coordinates are square centres.
        self.world_x0 = corner_x - 0.5
        self.world_y0 = corner_y - 0.5

        self.fogs = [entity for entity, manager in entities['Fog_Of_War'] if manager == self.manager]
        self.guys = [entity for entity, manager in entities['Guy'] if manager == self.manager]
        print('overworld grid %d x %d, corner (%d, %d), %d fog regions, %d heroes' % (
            self.squares_x, self.squares_y, corner_x, corner_y, len(self.fogs), len(self.guys)))

    def clear_fog(self):
        region_states = self.types.offset('Fog_Of_War', 'region_states')
        for fog in self.fogs:
            count = self.game.s64(fog + region_states)
            data = self.game.u64(fog + region_states + 8)
            if count > 0 and data:
                self.game.write(data, bytes([FOG_CLEARED]) * count)

    def hide_hero(self):
        undoable_data = self.types.offset('Entity', 'undoable_data')
        scale = self.types.offset('Undoable_Entity_Data', 'scale')
        for guy in self.guys:
            self.game.write(guy + undoable_data + scale, struct.pack('<3f', 0.0, 0.0, 0.0))

    def draw_all_wrap_copies(self):
        force = self.types.offset('Settings', 'force_wrapping_override_despite_per_screen')
        override = self.types.offset('Settings', 'wrapping_override')
        self.game.put_u8(self.settings + override, 0)
        self.game.put_u8(self.settings + force, 1)

    def set_shadow_distance(self, distance):
        offset = self.types.offset('Settings', 'shadow_max_visible_distance')
        self.game.put_f32(self.settings + offset, distance)

    def place_camera(self, center_x, center_y, height_squares):
        game = self.game
        fov = 2 * math.atan(height_squares / 2 / CAMERA_HEIGHT)
        game.put_s64(game.va(CAMERA_MODE), 2)
        game.write(self.manager + MANAGER_CAMERA_POSITION, struct.pack('<3f', center_x, center_y, CAMERA_HEIGHT))
        game.write(self.manager + MANAGER_CAMERA_ORIENTATION, struct.pack('<4f', *TOPDOWN_NORTH_UP))
        game.write(self.manager + MANAGER_CAMERA_PROJECTION, struct.pack('<3f', fov, CAMERA_HEIGHT - 100, CAMERA_HEIGHT + 100))


# ---------------------------------------------------------------------------------------------
# Rendering and screenshots

def freeze_and_force_detail(game):
    game.put_f64(game.va(TIME_MULTIPLIER), 0.0)
    game.put_f32(game.va(LOD_LEVEL2_THRESHOLD), -1.0)
    game.put_f32(game.va(LOD_LEVEL1_THRESHOLD), -1.0)
    game.put_f32(game.va(SHADOW_FADE_THRESHOLD), -1.0)
    game.put_u8(game.va(FORCE_MAP_LEVEL0), 1)
    game.put_s64(game.va(REFLECTION_COUNT), 0)


def render_size(game):
    target = game.u64(game.va(FINAL_TARGET))
    if not target:
        return None
    return game.s32(target + FINAL_TARGET_WIDTH), game.s32(target + FINAL_TARGET_HEIGHT)


def set_resolution(game, width, height):
    if render_size(game) == (width, height):
        return
    game.put_s64(game.va(CUSTOM_WIDTH), width)
    game.put_s64(game.va(CUSTOM_HEIGHT), height)
    game.put_u8(game.va(RENDER_OVERRIDE), 7)
    # The resolution is only recomputed on a swapchain resize: forget the cached width and resize.
    swapchain = game.u64(game.va(SWAPCHAIN))
    game.put_s64(swapchain + 8, 0)
    user32.PostMessageW(game.u64(swapchain), WM_SIZE, 0, 0)
    time.sleep(2.0)
    actual = render_size(game)
    if actual != (width, height):
        raise RuntimeError('render target is %s, wanted %dx%d' % (actual, width, height))


def shoot(game, screenshot_dir, out_path):
    os.makedirs(screenshot_dir, exist_ok=True)
    existing = set(os.listdir(screenshot_dir))
    request = game.va(SCREENSHOT_REQUEST)
    game.put_u8(request, 1)
    start = time.time()
    while game.u8(request) != 0:
        if time.time() - start > 60:
            raise RuntimeError('screenshot request was not taken')
        time.sleep(0.05)
    new_files = set(os.listdir(screenshot_dir)) - existing
    if len(new_files) != 1:
        raise RuntimeError('expected one new screenshot, got %s' % sorted(new_files))
    shutil.move(os.path.join(screenshot_dir, new_files.pop()), out_path)
    # Screenshot names have one-second resolution; wait so the next name differs.
    time.sleep(1.0)


# ---------------------------------------------------------------------------------------------
# Tiles

def tile_path(out_dir, row, column):
    return os.path.join(out_dir, 'tile_r%02d_c%02d.png' % (row, column))


def tile_size(squares_x, squares_y, tiles_x, tiles_y, px_per_square):
    width_squares = squares_x / tiles_x
    height_squares = squares_y / tiles_y
    width_px = width_squares * px_per_square
    height_px = height_squares * px_per_square
    if width_px != int(width_px) or height_px != int(height_px):
        raise ValueError('tiles must be a whole number of pixels: %g x %g' % (width_px, height_px))
    return width_squares, height_squares, int(width_px), int(height_px)


def shoot_tiles(game, world, px_per_square, tiles_x, tiles_y, out_dir):
    width_squares, height_squares, width_px, height_px = tile_size(world.squares_x, world.squares_y, tiles_x, tiles_y, px_per_square)
    screenshot_dir = os.path.join(os.path.dirname(game.exe_path), 'screenshots')
    os.makedirs(out_dir, exist_ok=True)
    set_resolution(game, width_px, height_px)
    for row in range(tiles_y):
        for column in range(tiles_x):
            out_path = tile_path(out_dir, row, column)
            if os.path.exists(out_path):
                continue
            # Row 0 is the north (top) row.
            center_x = world.world_x0 + (column + 0.5) * width_squares
            center_y = world.world_y0 + world.squares_y - (row + 0.5) * height_squares
            world.place_camera(center_x, center_y, height_squares)
            time.sleep(0.5)
            shoot(game, screenshot_dir, out_path)
            print('tile', row, column, 'at', round(center_x, 3), round(center_y, 3), flush=True)


def stitch(squares_x, squares_y, px_per_square, tiles_x, tiles_y, out_dir):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    width_squares, height_squares, width_px, height_px = tile_size(squares_x, squares_y, tiles_x, tiles_y, px_per_square)
    canvas = Image.new('RGB', (squares_x * px_per_square, squares_y * px_per_square))
    for row in range(tiles_y):
        for column in range(tiles_x):
            tile = Image.open(tile_path(out_dir, row, column)).convert('RGB')
            if tile.size != (width_px, height_px):
                raise RuntimeError('tile %d,%d is %s, wanted %dx%d' % (row, column, tile.size, width_px, height_px))
            canvas.paste(tile, (column * width_px, row * height_px))
    out_path = os.path.join(out_dir, 'overworld_ground_%dpx.png' % px_per_square)
    canvas.save(out_path)
    print('saved', out_path, canvas.size)


def main():
    parser = argparse.ArgumentParser(description='Capture the overworld map from the running game.')
    parser.add_argument('--px', type=int, default=128, help='pixels per grid square')
    parser.add_argument('--tiles', type=int, nargs=2, default=(8, 8), metavar=('COLUMNS', 'ROWS'))
    parser.add_argument('--out', default='map_tiles')
    parser.add_argument('--stitch-only', action='store_true')
    parser.add_argument('--capture-only', action='store_true')
    args = parser.parse_args()
    tiles_x, tiles_y = args.tiles

    game = Game()
    print('reading Type_Info from', game.exe_path)
    types = TypeInfo(game.exe_path)
    print('scanning memory for entities')
    world = Overworld(game, types)
    tile_size(world.squares_x, world.squares_y, tiles_x, tiles_y, args.px)

    if not args.stitch_only:
        world.clear_fog()
        world.hide_hero()
        world.draw_all_wrap_copies()
        world.set_shadow_distance(CAMERA_HEIGHT + 200)
        freeze_and_force_detail(game)
        shoot_tiles(game, world, args.px, tiles_x, tiles_y, args.out)

    if not args.capture_only:
        stitch(world.squares_x, world.squares_y, args.px, tiles_x, tiles_y, args.out)

if __name__ == '__main__':
    main()
