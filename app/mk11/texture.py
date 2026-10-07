"""MK11 Texture2D: mip table, mip data (.psf stream or package bulk), BC encoding via the game's ispc_texcomp.dll.

Texture2D native tail (after properties), verified on LIU_SKIN_A_* textures:
    20 zero bytes, two 24-byte refs, u32 NumMips, NumMips x 36-byte entries:
    u64 key (= CookedBulkDataOwnerKey), u32 record, u32 flags (1 = .psf stream record, 0x40 = package bulk record),
    u64 size, u32 0, u32 width, u32 height
Formats seen: 22 = BC7 (ColorOverride/Normal/RMA), 5 = BC1/DXT1 (Id/Tone), 2 = 8-bit RGBA (palette lookup).
"""
import ctypes
import io
import os
import struct
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .oodle import MK11Error, read_segment, game_dll_path
from . import props as P

FORMATS = {22: "BC7", 5: "BC1", 2: "RGBA8"}
BLOCK_BYTES = {"BC7": 16, "BC1": 8}


def read_texture(pkg, idx):
    d = pkg.export_data(idx)
    end, ps = P.read_props(pkg, d, 0)
    key = P.prop(ps, "CookedBulkDataOwnerKey")["value"]
    t = dict(idx=idx, name=pkg.obj_name(idx), fmt=P.prop(ps, "Format")["value"][0], key=key,
             w=struct.unpack("<i", P.prop(ps, "SizeX")["value"])[0], h=struct.unpack("<i", P.prop(ps, "SizeY")["value"])[0],
             srgb=bool(P.prop(ps, "SRGB")["extra"]) if P.prop(ps, "SRGB") else False)
    tail = d[end:]
    mips = []
    # Mip table: u32 count at tail offset 68, then count x 36-byte entries. Mips that aren't shipped (e.g. the top mip
    # of many gear textures) have a different key; they are skipped, and `level` stays the real mip level.
    if len(tail) >= 72:
        n = struct.unpack_from("<I", tail, 68)[0]
        if 0 < n <= 16 and 72 + 36 * n <= len(tail):
            for i in range(n):
                kk, rec, flags, size, _z, w, h = struct.unpack_from("<8sIIQIII", tail, 72 + 36 * i)
                if kk == key:
                    mips.append(dict(level=i, rec=rec, flags=flags, size=size, w=w, h=h))
    if not mips:
        raise MK11Error("mip table not found for %s" % t["name"])
    t["mips"] = mips
    return t


def mip_record(pkg, t, m):
    if m["flags"] & 1:
        ent = pkg.stream_entry(t["key"]); kind = "psf"
    elif m["flags"] & 0x40:
        ent = pkg.bulk_entry(t["key"]); kind = "pkg"
    else:
        raise MK11Error("unknown mip storage flags %#x" % m["flags"])
    if ent is None:
        raise MK11Error("no %s record for %s" % (kind, t["name"]))
    return kind, ent["recs"][m["rec"]]


def read_mip(pkg, t, m, psf_file):
    kind, rec = mip_record(pkg, t, m)
    if kind == "psf":
        data, _ = read_segment(psf_file, rec["vals"][2])
    else:
        data = pkg.uread(rec["vals"][2], rec["vals"][0])
    if len(data) != m["size"]:
        raise MK11Error("mip size mismatch in %s" % t["name"])
    return data


# --------------------------------------------------------------------------------------------- decoding (preview)
def dds_bytes(fmt, w, h, data, srgb=False):
    dx = {"BC1": 72 if srgb else 71, "BC7": 99 if srgb else 98}[fmt]
    hdr = struct.pack("<4sIIIIIII44sIIIIIIIII", b"DDS ", 124, 0x1007 | 0x80000, h, w, len(data), 1, 1, b"\0" * 44,
                      32, 4, 0x30315844, 0, 0, 0, 0, 0, 0x1000) + struct.pack("<IIII", 0, 0, 0, 0)
    return hdr + struct.pack("<IIIII", dx, 3, 0, 1, 0) + data


def decode(fmt_id, w, h, data, srgb=False):
    from PIL import Image
    fmt = FORMATS.get(fmt_id)
    if fmt == "RGBA8":
        return Image.frombytes("RGBA", (w, h), data)
    return Image.open(io.BytesIO(dds_bytes(fmt, w, h, data, srgb))).convert("RGBA")


# --------------------------------------------------------------------------------------------- encoding
class _Surface(ctypes.Structure):
    _fields_ = [("ptr", ctypes.c_void_p), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                ("stride", ctypes.c_int32)]


_ispc = None


def ispc():
    global _ispc
    if _ispc is None:
        _ispc = ctypes.CDLL(game_dll_path("ispc_texcomp.dll"))
    return _ispc


def _encode_rows(fmt, rgba, settings):
    h, w = rgba.shape[:2]
    buf = np.ascontiguousarray(rgba, np.uint8)
    out = ctypes.create_string_buffer((w // 4) * (h // 4) * BLOCK_BYTES[fmt])
    s = _Surface(buf.ctypes.data, w, h, w * 4)
    if fmt == "BC7":
        ispc().CompressBlocksBC7(ctypes.byref(s), out, settings)
    else:
        ispc().CompressBlocksBC1(ctypes.byref(s), out)
    return out.raw


def encode(fmt, rgba, alpha=True, threads=None):
    """BC-encode an (H, W, 4) uint8 array (H, W multiples of 4) with the game's ISPC compressor."""
    h, w = rgba.shape[:2]
    if h % 4 or w % 4:
        raise MK11Error("image size must be a multiple of 4")
    settings = None
    if fmt == "BC7":
        settings = ctypes.create_string_buffer(256)
        (ispc().GetProfile_alpha_basic if alpha else ispc().GetProfile_basic)(settings)
    band = 64 if h >= 64 else h
    bands = [rgba[y:y + band] for y in range(0, h, band)]
    with ThreadPoolExecutor(max_workers=threads or min(16, os.cpu_count() or 4)) as ex:
        parts = list(ex.map(lambda b: _encode_rows(fmt, b, settings), bands))
    return b"".join(parts)


def _resize_channels(im, size, method):
    """Resize each RGBA channel separately (Pillow premultiplies alpha otherwise, destroying mask data)."""
    from PIL import Image
    return Image.merge("RGBA", [b.resize(size, method) for b in im.split()])


def build_mips(t, source, log=print):
    """source: PIL image or (r, g, b, a) solid colour. Returns encoded bytes per mip, matching the texture's table."""
    from PIL import Image
    fmt = FORMATS.get(t["fmt"])
    if fmt not in ("BC7", "BC1"):
        raise MK11Error("%s uses format %d; only BC7/BC1 textures can be replaced" % (t["name"], t["fmt"]))
    w, h = t["w"], t["h"]
    if isinstance(source, tuple):
        base = Image.new("RGBA", (w, h), source)
    else:
        base = source.convert("RGBA")
        if base.size != (w, h):
            log("  resizing %dx%d -> %dx%d" % (base.size[0], base.size[1], w, h))
            base = _resize_channels(base, (w, h), Image.LANCZOS)
    out = []
    for m in t["mips"]:
        k = m["level"]
        mw, mh = max(1, w >> k), max(1, h >> k)
        if isinstance(source, tuple):
            arr = np.empty((max(4, mh), max(4, mw), 4), np.uint8); arr[:] = source
        else:
            im = base if k == 0 else _resize_channels(base, (mw, mh), Image.BOX)
            arr = np.asarray(im, np.uint8)
            if mw < 4 or mh < 4:
                arr = np.pad(arr, ((0, max(0, 4 - mh)), (0, max(0, 4 - mw)), (0, 0)), mode="edge")
        data = encode(fmt, np.ascontiguousarray(arr), alpha=True)
        if len(data) != m["size"]:
            raise MK11Error("mip %d of %s encoded to %d bytes, expected %d" % (k, t["name"], len(data), m["size"]))
        out.append(data)
    return out


def write_mips(pkg, psf_patcher, t, datas):
    for m, data in zip(t["mips"], datas):
        kind, rec = mip_record(pkg, t, m)
        if kind == "psf":
            psf_patcher.replace(rec, data)
        else:
            pkg.uwrite(rec["vals"][2], data)
