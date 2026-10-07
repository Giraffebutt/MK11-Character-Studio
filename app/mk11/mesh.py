"""MK11 skeletal mesh: skeleton reader, LOD reader/writer, LOD hider, mesh export (native tail) editing.

FSkeletalLODModel layout, as written here:
    u32 NumSections; Section[15 B] {u16 Material, u16 Chunk, u32 BaseIndex, u32 NumTris, u8 x3}
    u32 NumStreams (1)
    IndexBuffer {u32 count, u16[]}; SecondaryIndexBuffer {u32 0}
    Positions {u32 12, u32 N, u32 N, float3[]}
    Tangents  {u32 8,  u32 N, u32 N, (u32 tangent, u32 normal)[]}  signed 10:10:10:2; normal.w = bitangent sign (1 / 3)
    Weights   {u8 0, u32 8, u32 N, u32 N, (40-bit 4x10-bit chunk-local bones, u8 w0,w1,w2; w3 = 255-sum)[]}
    UVs       {u32 NumTexCoords, u32 4*NumTexCoords, u32 N, u32 N, half2[N][NumTexCoords]}
    Colors    {u32 4, u32 N, u32 N, rgba8[]}   or {u32 0, u32 0} when absent
    Extra     {u32 4, u32 N, u32 N, u32[]}     or {u32 0, u32 0} when absent
    {u32 0}  u32 NumVertices
    u32 NumChunks; Chunk {u32 BaseVertex, u32 0 x4, u32 n, u16 BoneMap[n], u32 NumRigid (1-influence verts), u32 NumSoft, u32 MaxInfluences (actual max in chunk)}
    ActiveBones {u32 n, u16[]} (chunk bone maps concatenated, duplicates removed, in order)
    RequiredBones {u32 n, u16[] = 0..NumBones-1}
    u32 NumVertices, 5 x {u32 0}, u32 1
"""
import struct
import numpy as np

from .oodle import MK11Error
from .package import Rd
from . import props as P


# ------------------------------------------------------------------------------------------- skeleton
def read_skeleton(pkg, skel_idx):
    d = pkg.export_data(skel_idx)
    end, ps = P.read_props(pkg, d, 0)
    nim = P.prop(ps, "NameIndexMap")["value"]
    n = struct.unpack_from("<i", nim)[0]
    pairs = [struct.unpack_from("<IIi", nim, 4 + 12 * k) for k in range(n)]
    names = [pkg.names[a] for a, _, _ in sorted(pairs, key=lambda x: x[2])]
    t = d[end:]
    nb = struct.unpack_from("<I", t, 0)[0]
    if nb != len(names):
        raise MK11Error("skeleton bone count mismatch")
    o = 4 + nb * 28
    if struct.unpack_from("<I", t, o)[0] != nb:
        raise MK11Error("unexpected skeleton layout")
    world = np.frombuffer(t, "<f4", nb * 16, o + 4).reshape(nb, 4, 4).astype(np.float64)  # row-vector, cm
    k = t.find(struct.pack("<I", nb), o + 4 + nb * 64)
    parents = None
    while k >= 0:
        par = np.frombuffer(t, "<i2", nb, k + 4).astype(np.int64)
        if par[0] == 0 and all(0 <= par[i] < i for i in range(1, nb)):
            parents = par; break
        k = t.find(struct.pack("<I", nb), k + 1)
    if parents is None:
        raise MK11Error("skeleton parent table not found")
    return dict(names=names, parents=parents, world=world, index={n: i for i, n in enumerate(names)})


# ------------------------------------------------------------------------------------------- mesh export
def mesh_info(pkg, mesh_idx):
    """Properties + native tail (LOD stream headers) of a USkeletalMesh export."""
    d = pkg.export_data(mesh_idx)
    end, ps = P.read_props(pkg, d, 0)
    r = Rd(d, end)
    lods = []
    for _ in range(r.u32()):
        lod_index = r.u32(); pos = r.o
        streamed = r.u8(); key = r.take(8); rec = r.u32(); size = r.u64()
        lods.append(dict(lod_index=lod_index, header_pos=pos, streamed=streamed, key=key, rec=rec, size=size))
        if not streamed:
            break   # inline LOD data follows; not handled
    return dict(idx=mesh_idx, props=ps, prop_end=end, lods=lods, data=d)


def lod_record(pkg, lod):
    ent = pkg.stream_entry(lod["key"])
    if ent is None:
        raise MK11Error("stream key %s not in package" % lod["key"].hex())
    return ent["recs"][lod["rec"]]


def set_lod_size(pkg, info, lod, size):
    # header_pos: u8 streamed, u64 key, u32 rec, u64 size
    pkg.export_write(info["idx"], lod["header_pos"] + 1 + 8 + 4, struct.pack("<Q", size))
    lod["size"] = size


def set_bounds(pkg, info, origin, extent, radius):
    b = P.prop(info["props"], "Bounds")
    if b is None or b["size"] != 28:
        raise MK11Error("Bounds property not found")
    pkg.export_write(info["idx"], b["off"], struct.pack("<7f", *origin, *extent, radius))


def get_bounds(info):
    b = P.prop(info["props"], "Bounds")
    v = struct.unpack("<7f", b["value"])
    return np.array(v[:3]), np.array(v[3:6]), v[6]


# ------------------------------------------------------------------------------------------- packing helpers
def _snorm10(v):
    q = np.clip(np.rint(np.clip(v, -1, 1) * 511), -511, 511).astype(np.int64)
    return q & 0x3FF


def pack_tangent_basis(normal, tangent, sign):
    t = _snorm10(tangent); n = _snorm10(normal)
    tw = t[:, 0] | (t[:, 1] << 10) | (t[:, 2] << 20)
    nw = n[:, 0] | (n[:, 1] << 10) | (n[:, 2] << 20) | (np.where(sign < 0, 3, 1).astype(np.int64) << 30)
    return np.stack([tw, nw], 1).astype("<u4")


def unpack_tangent_basis(words):
    def dec(w):
        w = w.astype(np.int64)
        s = lambda q: np.where(q >= 512, q - 1024, q) / 511.0
        return np.stack([s(w & 0x3FF), s((w >> 10) & 0x3FF), s((w >> 20) & 0x3FF)], 1), (w >> 30) & 3
    t, _ = dec(words[:, 0]); n, w = dec(words[:, 1])
    return n, t, np.where(w == 3, -1.0, 1.0)


def pack_influences(local, w8):
    """local: (N,4) chunk-local bone indices (<1024); w8: (N,4) u8 weights summing to 255 (w8[:,3] implied)."""
    v = np.zeros(len(local), np.uint64)
    for k in range(4):
        v |= (local[:, k].astype(np.uint64) & np.uint64(0x3FF)) << np.uint64(10 * k)
    out = np.zeros((len(local), 8), np.uint8)
    for k in range(5):
        out[:, k] = ((v >> np.uint64(8 * k)) & np.uint64(0xFF)).astype(np.uint8)
    out[:, 5:8] = w8[:, :3]
    return out


def unpack_influences(b8):
    v = np.zeros(len(b8), np.uint64)
    for k in range(5):
        v |= b8[:, k].astype(np.uint64) << np.uint64(8 * k)
    local = np.stack([((v >> np.uint64(10 * k)) & np.uint64(0x3FF)).astype(np.int64) for k in range(4)], 1)
    w = b8[:, 5:8].astype(np.int64)
    return local, np.concatenate([w, (255 - w.sum(1, keepdims=True)).clip(0, 255)], 1)


# ------------------------------------------------------------------------------------------- LOD read
def _vb(r, pre=None, optional=False):
    p = r.u8() if pre == "u8" else (r.u32() if pre == "u32" else None)
    stride, num = r.u32(), r.u32()
    if optional and num == 0:
        return p, stride, 0, b""
    cnt = r.u32()
    if cnt != num:
        raise MK11Error("vertex buffer count mismatch")
    return p, stride, num, r.take(stride * num)


def read_lod(b):
    r = Rd(b)
    secs = []
    for _ in range(r.u32()):
        mat, chunk, base, ntri = struct.unpack_from("<HHII", b, r.o)
        secs.append(dict(mat=mat, chunk=chunk, base=base, ntri=ntri, flags=bytes(b[r.o + 12:r.o + 15]))); r.o += 15
    if r.u32() != 1:
        raise MK11Error("multi-stream LOD not supported")
    nidx = r.u32(); idx = np.frombuffer(r.take(nidx * 2), "<u2").astype(np.int64)
    n2 = r.u32(); r.take(n2 * 2)
    _, s, nv, pos = _vb(r)
    if s != 12: raise MK11Error("unexpected position stride")
    _, s, _, tan = _vb(r)
    if s != 8: raise MK11Error("unexpected tangent stride")
    wflag, s, _, wts = _vb(r, "u8")
    if s != 8: raise MK11Error("unexpected weight stride %d" % s)
    ntc, s, _, uvs = _vb(r, "u32")
    _, cs, ncol, col = _vb(r, optional=True)
    _, xs, nx, ext = _vb(r, optional=True)
    n8 = r.u32(); r.take(n8 * 8)
    if r.u32() != nv: raise MK11Error("vertex count mismatch")
    chunks = []
    for _ in range(r.u32()):
        base = r.u32()
        for _ in range(4):
            if r.u32() != 0: raise MK11Error("legacy chunk arrays not empty")
        nb = r.u32(); bonemap = np.frombuffer(r.take(nb * 2), "<u2").astype(np.int64)
        rigid, soft, maxinf = r.u32(), r.u32(), r.u32()
        chunks.append(dict(base=base, bonemap=bonemap, nverts=rigid + soft, rigid=rigid, maxinf=maxinf))
    na = r.u32(); active = np.frombuffer(r.take(na * 2), "<u2").astype(np.int64)
    nr = r.u32(); required = np.frombuffer(r.take(nr * 2), "<u2").astype(np.int64)
    tail = r.take(len(b) - r.o)
    words = np.frombuffer(tan, "<u4").reshape(nv, 2)
    normal, tangent, sign = unpack_tangent_basis(words)
    local, w = unpack_influences(np.frombuffer(wts, np.uint8).reshape(nv, 8))
    bones = np.zeros((nv, 4), np.int64)
    for c in chunks:
        sl = slice(c["base"], c["base"] + c["nverts"])
        li = local[sl].copy(); li[w[sl] == 0] = 0
        bones[sl] = c["bonemap"][li]
    return dict(sections=secs, indices=idx, pos=np.frombuffer(pos, "<f4").reshape(nv, 3), normal=normal,
                tangent=tangent, sign=sign, bones=bones, weights=w, local=local, weight_flag=wflag,
                uv=np.frombuffer(uvs, "<f2").reshape(nv, ntc, 2).astype(np.float32), ntc=ntc,
                colors=np.frombuffer(col, np.uint8).reshape(-1, 4) if ncol else None, color_stride=cs,
                extra=np.frombuffer(ext, "<u4") if nx else None, extra_stride=xs,
                chunks=chunks, active=active, required=required, tail=tail, nverts=nv)


# ------------------------------------------------------------------------------------------- LOD write
def write_lod(sections, indices, pos, normal, tangent, sign, chunk_bones, local, w8, uv, nbones_total,
              colors=None, extra=None, active_order=None):
    """Serialize an FSkeletalLODModel. Vertices must already be grouped per chunk; `chunk_bones` is a list of
    (base_vertex, num_vertices, bonemap list). `local` holds chunk-local indices into each chunk's bone map."""
    nv = len(pos)
    if nv >= 65536:
        raise MK11Error("mesh has %d vertices; MK11 meshes use 16-bit indices (max 65,535)" % nv)
    out = bytearray()
    out += struct.pack("<I", len(sections))
    for s in sections:
        out += struct.pack("<HHII", s["mat"], s["chunk"], s["base"], s["ntri"]) + s.get("flags", b"\0\0\0")
    out += struct.pack("<I", 1)
    idx = np.asarray(indices, "<u2")
    out += struct.pack("<I", len(idx)) + idx.tobytes()
    out += struct.pack("<I", 0)
    out += struct.pack("<III", 12, nv, nv) + np.asarray(pos, "<f4").tobytes()
    out += struct.pack("<III", 8, nv, nv) + pack_tangent_basis(normal, tangent, sign).tobytes()
    out += struct.pack("<BIII", 0, 8, nv, nv) + pack_influences(local, w8).tobytes()
    ntc = uv.shape[1]
    out += struct.pack("<IIII", ntc, 4 * ntc, nv, nv) + np.asarray(uv, "<f2").tobytes()
    if colors is None:                       # optional streams: absent = {stride 0, count 0}, no data block
        out += struct.pack("<II", 0, 0)
    else:
        out += struct.pack("<III", 4, nv, nv) + np.asarray(colors, np.uint8).tobytes()
    if extra is None:
        out += struct.pack("<II", 0, 0)
    else:
        out += struct.pack("<III", 4, nv, nv) + np.asarray(extra, "<u4").tobytes()
    out += struct.pack("<I", 0) + struct.pack("<I", nv)
    out += struct.pack("<I", len(chunk_bones))
    active = []
    nz = (w8 > 0).sum(1)
    for base, count, bonemap in chunk_bones:
        seg = nz[base:base + count]
        rigid = int((seg == 1).sum())                 # 1-influence vertices (a count, not an ordering)
        maxinf = int(seg.max()) if count else 0       # actual max influences in this chunk
        out += struct.pack("<I", base) + b"\0" * 16
        out += struct.pack("<I", len(bonemap)) + np.asarray(bonemap, "<u2").tobytes()
        out += struct.pack("<III", rigid, count - rigid, maxinf)
        for b in bonemap:
            if b not in active:
                active.append(int(b))
    if active_order is not None:             # NRS's own order (multi-chunk bodies use a source-data order)
        if sorted(active_order) != sorted(active):
            raise MK11Error("active bone set mismatch")
        active = list(active_order)
    out += struct.pack("<I", len(active)) + np.asarray(active, "<u2").tobytes()
    out += struct.pack("<I", nbones_total) + np.arange(nbones_total, dtype="<u2").tobytes()
    out += struct.pack("<I", nv) + b"\0" * 20 + struct.pack("<I", 1)
    return bytes(out)


# ------------------------------------------------------------------------------------------- hide
def hide_lod(b):
    """Make a LOD draw nothing while keeping every vertex buffer, chunk and bone list intact: each section keeps
    exactly one zero-area triangle (its first index repeated). Vertex count is unchanged, so morph targets,
    cloth and LOD bookkeeping stay valid. Returns new bytes, or None if the layout isn't the standard one."""
    r = Rd(b)
    ns = r.u32()
    secs = []
    for _ in range(ns):
        secs.append(list(struct.unpack_from("<HHII", b, r.o)) + [bytes(b[r.o + 12:r.o + 15])]); r.o += 15
    if r.u32() != 1:
        return None
    nidx = r.u32(); idx_off = r.o
    idx = np.frombuffer(b, "<u2", nidx, idx_off)
    after = idx_off + 2 * nidx
    # sanity: secondary index buffer (u32 n + data) then position header {12, N, N}
    n2 = struct.unpack_from("<I", b, after)[0]
    ph = after + 4 + 2 * n2
    st, nv, nv2 = struct.unpack_from("<III", b, ph)
    if st != 12 or nv != nv2:
        return None
    out = bytearray(struct.pack("<I", ns))
    new_idx = []
    for k, (mat, chunk, base, ntri, fl) in enumerate(secs):
        first = int(idx[base]) if ntri else 0
        out += struct.pack("<HHII", mat, chunk, 3 * k, 1) + fl
        new_idx += [first] * 3
    out += struct.pack("<I", 1) + struct.pack("<I", len(new_idx)) + np.asarray(new_idx, "<u2").tobytes()
    out += b[after:]
    return bytes(out)
