"""Oodle (Kraken) compression via the game's own oo2core_5_win64.dll, and MK11/UE3 compressed-segment I/O.

Segment layout (verified on .xxx and .psf):
    u32 tag 0x9E2A83C1, u32 0, u64 block size (0x20000), u64 payload size, u64 raw size,
    {u64 compressed, u64 raw} per block, then the Kraken-compressed blocks back to back.
"""
import ctypes
import os
import struct

TAG = 0x9E2A83C1
BLOCK = 0x20000
KRAKEN = 8          # OodleLZ_Compressor_Kraken
LEVEL_NORMAL = 4    # OodleLZ_CompressionLevel_Normal
LEVEL_OPTIMAL = 8   # OodleLZ_CompressionLevel_Optimal2 (used when an in-place fit is needed)

_dll = None
_game_dir = None    # the user's MK11 folder; the game's own DLLs are loaded from it (none are bundled)


class MK11Error(Exception):
    pass


def use_game(game_dir):
    """Remember the user's game folder (done automatically when a package inside it is opened)."""
    global _game_dir
    if game_dir and os.path.isdir(os.path.join(game_dir, "Binaries", "Retail")):
        _game_dir = game_dir


def game_dll_path(name):
    if not _game_dir:
        raise MK11Error("Choose your Mortal Kombat 11 folder first (%s is loaded from its Binaries\\Retail)" % name)
    path = os.path.join(_game_dir, "Binaries", "Retail", name)
    if not os.path.isfile(path):
        raise MK11Error("%s not found in %s" % (name, os.path.dirname(path)))
    return path


def dll():
    global _dll
    if _dll is None:
        _dll = ctypes.WinDLL(game_dll_path("oo2core_5_win64.dll"))
        d = _dll.OodleLZ_Decompress
        d.restype = ctypes.c_int64
        d.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_int64, ctypes.c_int]
        c = _dll.OodleLZ_Compress
        c.restype = ctypes.c_int64
        c.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int64]
    return _dll


def decompress(src, rawlen):
    out = ctypes.create_string_buffer(rawlen)
    n = dll().OodleLZ_Decompress(src, len(src), out, rawlen, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != rawlen:
        raise MK11Error("Oodle decompression failed (%d of %d bytes)" % (n, rawlen))
    return out.raw


def compress(raw, level=LEVEL_NORMAL):
    out = ctypes.create_string_buffer(len(raw) + 65536 + len(raw) // 8)
    n = dll().OodleLZ_Compress(KRAKEN, raw, len(raw), out, level, None, None, None, None, 0)
    if n <= 0:
        raise MK11Error("Oodle compression failed")
    blob = out.raw[:n]
    if decompress(blob, len(raw)) != raw:          # never write data we can't read back
        raise MK11Error("Oodle round-trip check failed")
    return blob


def read_segment(f, offset):
    """Read and decompress one segment at `offset` of an open binary file. Returns (raw_bytes, total_size_on_disk)."""
    f.seek(offset)
    tag, zero, bsz, csz, usz = struct.unpack("<IIQQQ", f.read(32))
    if tag != TAG:
        raise MK11Error("bad segment tag at %#x" % offset)
    blocks = []
    total = 0
    while total < usz:
        c, u = struct.unpack("<QQ", f.read(16))
        blocks.append((c, u)); total += u
    out = bytearray()
    for c, u in blocks:
        data = f.read(c)
        out += data if c == u else decompress(data, u)
    return bytes(out), 32 + 16 * len(blocks) + csz


def build_segment(raw, level=LEVEL_NORMAL):
    """Compress `raw` into a complete segment (header + block table + blocks)."""
    blocks = []
    for o in range(0, max(len(raw), 1), BLOCK):
        chunk = raw[o:o + BLOCK]
        blocks.append((compress(chunk, level), len(chunk)))
    if not raw:
        blocks = []
    payload = b"".join(b for b, _ in blocks)
    head = struct.pack("<IIQQQ", TAG, 0, BLOCK, len(payload), len(raw))
    table = b"".join(struct.pack("<QQ", len(b), u) for b, u in blocks)
    return head + table + payload
