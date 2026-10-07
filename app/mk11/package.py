"""MK11 .xxx package model: read, edit in place (same-size edits in uncompressed space), rebuild, and .psf patching.

Header layout (verified on CHAR_LIU / GEARASSETS_LIU, file version 769, licensee 157):
    u32 tag, u16 ver, u16 licensee, u32 data_offset, u32 shader_ver, u32 engine_ver, 'MK11' u32 u32, 'MAIN' u32 flags,
    {u32 count, u64 off} names / exports / imports (offsets in uncompressed space), u64 bulk_off, GUID, u32 compression,
    u32 N, N x InnerPackage{FString name, u64 uoff, usize, coff, csize, u32 n, n x {u64 uoff, usize, coff, csize}},
    u32 M, M x PsfPackage (same shape; coff = .psf file offset),
    24 zero bytes, FString file name,
    stream table: u32 K, K x {u64 key, FString stream, u32 n, n x {u64 usize, csize, psf_off, psf_off}, u32 comp},
    bulk table:   u32 K, K x {u64 key, FString seg,    u32 n, n x {u64 usize, -1, uoff, -1},           u32 comp}.
The header ends exactly at the first compressed segment; segments are contiguous to end of file.
"""
import os
import shutil
import struct
import uuid

from .oodle import MK11Error, read_segment, build_segment, use_game, LEVEL_NORMAL, LEVEL_OPTIMAL


class Rd:
    def __init__(self, b, o=0):
        self.b, self.o = b, o

    def u8(self):
        v = self.b[self.o]; self.o += 1; return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.o)[0]; self.o += 4; return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.o)[0]; self.o += 4; return v

    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.o)[0]; self.o += 8; return v

    def take(self, n):
        v = bytes(self.b[self.o:self.o + n]); self.o += n; return v

    def fstr(self):
        n = self.i32()
        if n < 0:
            return self.take(-n * 2).decode("utf-16le").rstrip("\0")
        return self.take(n).decode("latin1").rstrip("\0")


class Package:
    def __init__(self, path):
        self.path = path
        asset = os.path.dirname(os.path.abspath(path))
        if os.path.basename(asset).lower() == "asset":
            use_game(os.path.dirname(asset))          # <game>\Asset\X.xxx -> the game's own Oodle/ISPC DLLs
        with open(path, "rb") as f:
            self.raw = bytearray(f.read())
        b = self.raw
        r = Rd(b)
        if r.u32() != 0x9E2A83C1:
            raise MK11Error("%s is not an MK11 package" % path)
        self.file_ver, self.lic_ver = struct.unpack_from("<HH", b, 4)
        if (self.file_ver, self.lic_ver) != (769, 157) or b[0x14:0x18] != b"MK11":
            raise MK11Error("unexpected package version %d/%d" % (self.file_ver, self.lic_ver))
        r.o = 0x28
        self.name_count, self.name_off = r.u32(), r.u64()
        self.export_count, self.export_off = r.u32(), r.u64()
        self.import_count, self.import_off = r.u32(), r.u64()
        r.o = 0x68
        self.inner = self._read_pkg_list(r)
        self.psf_list = self._read_pkg_list(r)
        r.take(24)
        self.file_name = r.fstr()
        self.stream_table = self._read_key_table(r)
        self.bulk_table = self._read_key_table(r)
        self.header_end = r.o
        subs = sorted((s for p in self.inner for s in p["subs"]), key=lambda s: s["coff"])
        if subs[0]["coff"] != self.header_end:
            raise MK11Error("header/segment layout not as expected")
        for a, c in zip(subs, subs[1:]):
            if a["coff"] + a["csize"] != c["coff"]:
                raise MK11Error("segments are not contiguous")
        if subs[-1]["coff"] + subs[-1]["csize"] != len(b):
            raise MK11Error("trailing data after last segment")
        self._seg = {}            # id(sub) -> bytearray (decompressed)
        self.dirty = set()
        self._load_tables()

    # ------------------------------------------------------------------ header
    def _read_pkg_list(self, r):
        out = []
        for _ in range(r.u32()):
            name = r.fstr()
            pos = r.o
            uoff, usize, coff, csize = r.u64(), r.u64(), r.u64(), r.u64()
            subs = []
            for _ in range(r.u32()):
                sp = r.o
                subs.append(dict(pos=sp, uoff=r.u64(), usize=r.u64(), coff=r.u64(), csize=r.u64(), pkg=name))
            out.append(dict(name=name, pos=pos, uoff=uoff, usize=usize, coff=coff, csize=csize, subs=subs))
        return out

    def _read_key_table(self, r):
        out = []
        for _ in range(r.u32()):
            key = r.take(8)
            name = r.fstr()
            recs = []
            for _ in range(r.u32()):
                p = r.o
                recs.append(dict(pos=p, vals=list(struct.unpack_from("<QqQq", self.raw, p)))); r.o += 32
            comp = r.u32()
            out.append(dict(key=key, seg=name, recs=recs, comp=comp))
        return out

    def stream_entry(self, key):
        return next((e for e in self.stream_table if e["key"] == key), None)

    def bulk_entry(self, key):
        return next((e for e in self.bulk_table if e["key"] == key), None)

    def set_record(self, rec, vals):
        rec["vals"] = list(vals)
        struct.pack_into("<QqQq", self.raw, rec["pos"], *vals)

    @staticmethod
    def _sub_fields(raw, s):
        struct.pack_into("<QQQQ", raw, s["pos"], s["uoff"], s["usize"], s["coff"], s["csize"])

    # ------------------------------------------------------------------ uncompressed space
    def _sub_for(self, uoff):
        for p in self.inner:
            for s in p["subs"]:
                if s["uoff"] <= uoff < s["uoff"] + s["usize"]:
                    return s
        raise MK11Error("offset %#x not in any segment" % uoff)

    def _seg_data(self, s):
        k = id(s)
        if k not in self._seg:
            if not hasattr(self, "_bio"):
                import io
                self._bio = io.BytesIO(bytes(self.raw))      # segment bytes never change in self.raw
            data, _ = read_segment(self._bio, s["coff"])
            if len(data) != s["usize"]:
                raise MK11Error("segment size mismatch")
            self._seg[k] = bytearray(data)
        return self._seg[k]

    def uread(self, uoff, n):
        out = bytearray()
        while n > 0:
            s = self._sub_for(uoff)
            d = self._seg_data(s); o = uoff - s["uoff"]; k = min(n, s["usize"] - o)
            out += d[o:o + k]; uoff += k; n -= k
        return bytes(out)

    def uwrite(self, uoff, data):
        """Same-size overwrite in uncompressed package space (may span segments)."""
        i = 0
        while i < len(data):
            s = self._sub_for(uoff)
            d = self._seg_data(s); o = uoff - s["uoff"]; k = min(len(data) - i, s["usize"] - o)
            if d[o:o + k] != data[i:i + k]:
                d[o:o + k] = data[i:i + k]; self.dirty.add(id(s))
            uoff += k; i += k

    def pkg_bytes(self, name):
        p = next(x for x in self.inner if x["name"] == name)
        return p["uoff"], self.uread(p["uoff"], p["usize"])

    # ------------------------------------------------------------------ names / imports / exports
    def _load_tables(self):
        hb_off = self.name_off
        hb = self.uread(self.name_off, (self.export_off + self.export_count * 76) - self.name_off)
        r = Rd(hb)
        self.names = [r.fstr() for _ in range(self.name_count)]
        o = self.import_off - hb_off
        self.imports = [struct.unpack_from("<iIIIi", hb, o + 20 * k) for k in range(self.import_count)]
        o = self.export_off - hb_off
        self.exports = []
        for k in range(self.export_count):
            v = struct.unpack_from("<iiIIIII16sIIIIIIII", hb, o + 76 * k)
            self.exports.append(dict(index=k + 1, cls=v[0], outer=v[1], name=v[2], num=v[3], flags=v[6],
                                     guid=uuid.UUID(bytes_le=v[7]), seg=self.names[v[8]], size=v[10], offset=v[11]))
        self.children = {}
        for e in self.exports:
            self.children.setdefault(e["outer"], []).append(e["index"])

    def nm(self, i, num=0):
        return self.names[i] if not num else "%s_%d" % (self.names[i], num - 1)

    def obj_name(self, ref):
        if ref < 0:
            e = self.imports[-ref - 1]; return self.nm(e[1], e[2])
        if ref > 0:
            e = self.exports[ref - 1]; return self.nm(e["name"], e["num"])
        return "None"

    def class_name(self, idx):
        e = self.exports[idx - 1]
        return self.obj_name(e["cls"]) if e["cls"] else "Class"

    def full_path(self, ref):
        parts = []
        while ref:
            parts.append(self.obj_name(ref))
            ref = self.imports[-ref - 1][0] if ref < 0 else self.exports[ref - 1]["outer"]
            if len(parts) > 64:
                break
        return ".".join(reversed(parts))

    def find(self, name, cls):
        """By object name (first match) or by full object path such as 'DLC.Nightwolf.Characters...LIU_GEAR_A_004'
        (names can repeat across DLC folders; paths are unique)."""
        if "." in name:
            for e in self.exports:
                if self.class_name(e["index"]) == cls and self.full_path(e["index"]) == name:
                    return e["index"]
        for e in self.exports:
            if self.obj_name(e["index"]) == name and self.class_name(e["index"]) == cls:
                return e["index"]
        raise MK11Error("%s '%s' not found in %s" % (cls, name, os.path.basename(self.path)))

    def child(self, idx, cls):
        for c in self.children.get(idx, []):
            if self.class_name(c) == cls:
                return c
        return None

    def export_data(self, idx):
        e = self.exports[idx - 1]
        return self.uread(e["offset"], e["size"])

    def export_write(self, idx, rel, data):
        e = self.exports[idx - 1]
        if rel < 0 or rel + len(data) > e["size"]:
            raise MK11Error("write outside export")
        self.uwrite(e["offset"] + rel, data)

    # ------------------------------------------------------------------ save
    def save(self, out_path, level=LEVEL_NORMAL, log=print):
        """Write the package: unchanged segments are copied byte-for-byte, dirty ones recompressed; all compressed
        offsets/sizes in the header are updated."""
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if (os.path.abspath(out_path) == os.path.abspath(self.path) or
                os.path.normcase(out_dir) == os.path.normcase(os.path.dirname(os.path.abspath(self.path)))):
            raise MK11Error("refusing to write into the original package's folder")
        raw = bytearray(self.raw[:self.header_end])
        body = bytearray()
        pos = self.header_end
        subs = sorted((s for p in self.inner for s in p["subs"]), key=lambda s: s["coff"])
        new = {}
        for s in subs:
            if id(s) in self.dirty:
                blob = build_segment(bytes(self._seg_data(s)), level)
            else:
                blob = bytes(self.raw[s["coff"]:s["coff"] + s["csize"]])
            new[id(s)] = (pos, len(blob))
            body += blob; pos += len(blob)
        for p in self.inner:
            for s in p["subs"]:
                c, n = new[id(s)]
                struct.pack_into("<QQQQ", raw, s["pos"], s["uoff"], s["usize"], c, n)
            struct.pack_into("<QQQQ", raw, p["pos"], p["uoff"], p["usize"],
                             new[id(p["subs"][0])][0], sum(new[id(s)][1] for s in p["subs"]))
        with open(out_path, "wb") as f:
            f.write(raw); f.write(body)
        log("wrote %s (%d of %d segments recompressed)" % (os.path.basename(out_path), len(self.dirty), len(subs)))


class PsfPatcher:
    """Copies the original .psf next to the output and replaces individual records in it.
    A record that fits in its original space is written in place (file size unchanged); otherwise it is appended."""

    def __init__(self, pkg, src_psf, out_psf, log=print):
        self.pkg, self.out, self.log = pkg, out_psf, log
        if os.path.abspath(src_psf) == os.path.abspath(out_psf):
            raise MK11Error("refusing to modify the original .psf")
        shutil.copyfile(src_psf, out_psf)
        self.f = open(out_psf, "r+b")
        self.f.seek(0, 2); self.end = self.f.tell()
        self.appended = 0; self.inplace = 0

    def replace(self, rec, raw):
        usize, csize, off, _ = rec["vals"]
        blob = build_segment(raw)
        if len(blob) > csize:
            blob = build_segment(raw, LEVEL_OPTIMAL)
        if len(blob) <= csize:
            where = off; self.inplace += 1
        else:
            where = self.end; self.end += len(blob); self.appended += 1
        self.f.seek(where); self.f.write(blob)
        # keep the package's .psf segment list consistent with the record
        for p in self.pkg.psf_list:
            for s in p["subs"]:
                if s["coff"] == off and s["csize"] == csize:
                    s["usize"], s["coff"], s["csize"] = len(raw), where, len(blob)
                    Package._sub_fields(self.pkg.raw, s)
        self.pkg.set_record(rec, (len(raw), len(blob), where, where))
        return where, len(blob)

    def close(self):
        self.f.close()
        self.log("wrote %s (%d records in place, %d appended)" % (os.path.basename(self.out), self.inplace, self.appended))
