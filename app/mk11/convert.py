"""Custom .glb -> MK11 character mesh conversion (replaces one body mesh, hides the rest of its outfit).

Pipeline: read model -> build FSkeletalLODModel -> patch package (.xxx) and stream file (.psf) copies ->
textures (optional) -> save -> re-open the output and verify -> preview .glb + build report.
"""
import datetime
import hashlib
import io
import json
import os
import shutil
import struct
import uuid

import numpy as np

from .oodle import MK11Error, read_segment
from .package import Package, PsfPatcher
from . import props as P
from . import mesh as M
from . import texture as T
from .gltf import GLB, GLBWriter, to_game, dir_to_game, C, SCALE

ROOT_BONES = {"Reference", "CenterOfMass", "position_locator"}
# Bones present on MK11 body skeletons that NRS never skins to (forearms use *_helper bones instead).
NRS_UNUSED = {"LeftArmRoll", "LeftForeArm", "LeftForeArmRoll", "LeftFingerBase", "LeftInHandRing", "LeftShoulderRoll_helper",
              "RightArmRoll", "RightForeArm", "RightForeArmRoll", "RightFingerBase", "RightInHandRing", "RightShoulderRoll_helper"}
MAX_CHUNK_BONES = 121          # largest bone map NRS uses on a body chunk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = os.path.join(HERE, "config", "texture_defaults.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ============================================================================================ game-side lookups
def guid_map(pkg):
    return {e["guid"]: e["index"] for e in pkg.exports}


def cap_items(pkg):
    """All CAP item presets: [(asset_name, item_guid, [mesh export indices])]."""
    gm = guid_map(pkg)
    out = []
    for e in pkg.exports:
        if pkg.class_name(e["index"]) != "CAPItemPresetsAsset":
            continue
        d = pkg.export_data(e["index"]); _, ps = P.read_props(pkg, d, 0)
        items = P.prop(ps, "Items")
        if not items:
            continue
        for _, ip in P.array_of_structs(pkg, items["value"]):
            ms = P.prop(ip, "Meshes"); idp = P.prop(ip, "Id")
            meshes = []
            if ms:
                for _, mp in P.array_of_structs(pkg, ms["value"]):
                    sm = P.prop(mp, "SkeletalMesh")
                    if sm and sm["type"] == "WeakUObjectHandleProperty":
                        g = uuid.UUID(bytes_le=sm["value"])
                        if g in gm:
                            meshes.append(gm[g])
            out.append((pkg.obj_name(e["index"]), uuid.UUID(bytes_le=idp["value"]) if idp else None, meshes))
    return out


def material_slots(pkg, info):
    """[(slot_name, material_export_index_or_None)] for LOD0 of a mesh."""
    lp = next((p for p in info["props"] if p["name"] == "LODProperties" and p["index"] == 0), None)
    names, mats = [], []
    if lp:
        _, sub = P.read_props(pkg, lp["value"], 0)
        mp = P.prop(sub, "MaterialProperties")
        if mp:
            for _, ps in P.array_of_structs(pkg, mp["value"]):
                n = P.prop(ps, "ImportedMaterialName")
                names.append(pkg.names[struct.unpack_from("<I", n["value"])[0]] if n else "slot%d" % len(names))
        ms = P.prop(sub, "MaterialSets")
        if ms:
            for _, ps in P.array_of_structs(pkg, ms["value"])[:1]:          # "Default" set
                mm = P.prop(ps, "Materials")
                if mm:
                    n = struct.unpack_from("<i", mm["value"])[0]
                    mats = list(struct.unpack_from("<%di" % n, mm["value"], 4))
    while len(mats) < len(names):
        mats.append(0)
    return [(names[i], mats[i] if mats[i] > 0 else None) for i in range(len(names))]


def material_textures(pkg, mat_idx):
    """{ParameterName: texture export index} of a MaterialInstanceConstant(Template)."""
    d = pkg.export_data(mat_idx); _, ps = P.read_props(pkg, d, 0)
    tv = P.prop(ps, "TextureParameterValues")
    out = {}
    if tv:
        for _, ep in P.array_of_structs(pkg, tv["value"]):
            pn = P.prop(ep, "ParameterName"); pv = P.prop(ep, "ParameterValue")
            if pn and pv:
                ref = struct.unpack("<i", pv["value"])[0]
                if ref > 0 and pkg.class_name(ref) == "Texture2D":
                    out[pkg.names[struct.unpack_from("<I", pn["value"])[0]]] = ref
    return out


def read_lod_raw(pkg, lod, psf):
    rec = M.lod_record(pkg, lod)
    raw, _ = read_segment(psf, rec["vals"][2])
    return rec, raw


# ============================================================================================ model -> LOD
def load_model(path, skel, slot_names, ignore_vertex_counts=(), log=print):
    """Read every skinned mesh of the .glb into game space, grouped by material slot."""
    g = GLB(path)
    j = g.j
    if not j.get("skins"):
        raise MK11Error("the .glb has no armature/skin")
    W = g.node_world()
    parts = []
    for ni, node in enumerate(j["nodes"]):
        if "mesh" not in node:
            continue
        name = node.get("name") or j["meshes"][node["mesh"]].get("name", "mesh%d" % ni)
        if "skin" not in node:
            raise MK11Error("mesh '%s' is not skinned to the armature" % name)
        skin = j["skins"][node["skin"]]
        jn = [j["nodes"][k].get("name", "") for k in skin["joints"]]
        for pi, pr in enumerate(j["meshes"][node["mesh"]]["primitives"]):
            if pr.get("mode", 4) != 4:
                raise MK11Error("mesh '%s' has non-triangle primitives" % name)
            a = pr["attributes"]
            pos = g.accessor(a["POSITION"]).astype(np.float64)
            if len(pos) in ignore_vertex_counts:
                log("  skipping '%s' (same vertex count as one of the game's original meshes)" % name); break
            if "JOINTS_0" not in a or "WEIGHTS_0" not in a:
                raise MK11Error("mesh '%s' has no bone weights" % name)
            if "JOINTS_1" in a:
                raise MK11Error("mesh '%s' has more than 4 bone influences per vertex (export with Bone Influences = 4)" % name)
            idx = g.accessor(pr["indices"]).astype(np.int64).ravel() if "indices" in pr else np.arange(len(pos))
            nrm = g.accessor(a["NORMAL"]).astype(np.float64) if "NORMAL" in a else None
            uvs = [g.accessor(a["TEXCOORD_%d" % k]).astype(np.float64) for k in range(8) if "TEXCOORD_%d" % k in a]
            if not uvs:
                raise MK11Error("mesh '%s' has no UV map" % name)
            jt = g.accessor(a["JOINTS_0"]).astype(np.int64); wt = g.accessor(a["WEIGHTS_0"]).astype(np.float64)
            mi = pr.get("material")
            mname = j["materials"][mi].get("name", "") if mi is not None else ""
            parts.append(dict(name=name, pos=pos, idx=idx, nrm=nrm, uvs=uvs, joints=jt, weights=wt,
                              jn=jn, material=mname, material_index=mi, node=ni))
    if not parts:
        raise MK11Error("no usable skinned meshes in the .glb")
    for pt in parts:
        if not np.allclose(W[pt["node"]], np.eye(4), atol=1e-4):
            log("  note: mesh '%s' has an object transform; glTF ignores it for skinned meshes (apply transforms in Blender if it looks offset)" % pt["name"])
    # ---- materials -> slots
    slot_of = {}
    lower = [s.lower() for s in slot_names]
    free = list(range(len(slot_names)))
    for pt in parts:
        mn = pt["material"]
        if mn in slot_of:
            continue
        k = None
        if mn.lower() in lower:
            k = lower.index(mn.lower())
        elif mn.lower().startswith("slot") and mn[4:6].isdigit() and int(mn[4:6]) < len(slot_names):
            k = int(mn[4:6])
        slot_of[mn] = k
    for mn in list(slot_of):
        if slot_of[mn] is None:
            unused = [s for s in free if s not in slot_of.values()]
            if not unused:
                raise MK11Error("model uses more materials than the %d slots of the target mesh" % len(slot_names))
            slot_of[mn] = unused[0]
            log("  material '%s' -> slot %d (%s)" % (mn, unused[0], slot_names[unused[0]]))
    return g, parts, slot_of


def quantize_weights(w):
    """(N,4) float weights -> (N,4) u8 summing to 255 (largest remainder), sorted descending with index order."""
    w = np.clip(w, 0, None)
    s = w.sum(1, keepdims=True)
    w = np.where(s > 0, w / np.maximum(s, 1e-12), 0)
    order = np.argsort(-w, axis=1, kind="stable")
    ws = np.take_along_axis(w, order, 1)
    f = ws * 255
    q = np.floor(f).astype(np.int64)
    rem = 255 - q.sum(1)
    frac = f - q
    for i in np.nonzero(rem > 0)[0]:
        for k in np.argsort(-frac[i], kind="stable")[:rem[i]]:
            q[i, k] += 1
    return q.astype(np.uint8), order


def compute_tangents(pos, nrm, uv, tri):
    """Per-vertex tangent (dP/du, orthogonalised to the normal) and bitangent sign (MK11 convention:
    +1 when cross(N, T) follows dP/dv)."""
    e1 = pos[tri[:, 1]] - pos[tri[:, 0]]; e2 = pos[tri[:, 2]] - pos[tri[:, 0]]
    d1 = uv[tri[:, 1]] - uv[tri[:, 0]]; d2 = uv[tri[:, 2]] - uv[tri[:, 0]]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    ok = np.abs(r) > 1e-20; r = np.where(ok, r, 1.0)
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]; sdir[~ok] = 0
    tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]; tdir[~ok] = 0
    S = np.zeros_like(pos); Tt = np.zeros_like(pos)
    for k in range(3):
        np.add.at(S, tri[:, k], sdir); np.add.at(Tt, tri[:, k], tdir)
    t = S - nrm * (S * nrm).sum(1, keepdims=True)
    ln = np.linalg.norm(t, axis=1, keepdims=True)
    bad = ln[:, 0] < 1e-12
    if bad.any():                     # no UV gradient: any vector perpendicular to the normal
        ref = np.where(np.abs(nrm[:, :1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
        alt = np.cross(nrm, ref); t[bad] = alt[bad]; ln = np.linalg.norm(t, axis=1, keepdims=True)
    t /= np.maximum(ln, 1e-12)
    sign = np.where((np.cross(nrm, t) * Tt).sum(1) < 0, -1.0, 1.0)
    return t, sign


def build_lod(parts, slot_of, skel, nslots, log=print):
    names = skel["index"]
    # ---- gather + validate weights
    allowed_problem = []
    built = []
    for pt in parts:
        jmap = np.array([names.get(n, -1) for n in pt["jn"]])
        w = pt["weights"].copy()
        w[w * 255 < 0.5] = 0                                        # rounds to 0 in MK11's u8 weights anyway
        used = jmap[pt["joints"]][w > 0]
        missing = sorted({pt["jn"][k] for k in np.unique(pt["joints"][w > 0]) if jmap[k] < 0})
        if "neutral_bone" in missing:
            n = int(((np.array(pt["jn"])[pt["joints"]] == "neutral_bone") & (w > 0)).any(1).sum())
            raise MK11Error("mesh '%s' has %d vertices without bone weights (Blender exported them on its stand-in "
                            "'neutral_bone'). Run 'Blender Tools\\blender_check_weights.py' in Blender - it weights them "
                            "like the skin they sit on - then re-export." % (pt["name"], n))
        if missing:
            raise MK11Error("mesh '%s' is weighted to bones that the game body doesn't have: %s" % (pt["name"], missing[:12]))
        badb = sorted({skel["names"][b] for b in np.unique(used)
                       if skel["names"][b].startswith("C_") or skel["names"][b] in ROOT_BONES})
        if badb:
            raise MK11Error("mesh '%s' is weighted to root or cloth bones %s - run blender_check_weights.py in Blender "
                            "(it transfers those weights to proper body bones) and re-export" % (pt["name"], badb))
        odd = sorted({skel["names"][b] for b in np.unique(used) if skel["names"][b] in NRS_UNUSED})
        if odd:
            allowed_problem.append("%s: %s" % (pt["name"], odd))
        bones = np.where(w > 0, jmap[pt["joints"]], 0)
        zero = w.sum(1) <= 0
        if zero.any():
            raise MK11Error("mesh '%s' has %d vertices with no bone weights" % (pt["name"], int(zero.sum())))
        built.append((pt, bones, w))
    if allowed_problem:
        log("  note: weights on valid-but-NRS-unused arm bones (should work): %s" % "; ".join(allowed_problem))
    # ---- per-part geometry in game space
    geo = []
    for pt, bones, w in built:
        pos = to_game(pt["pos"])
        tri = pt["idx"].reshape(-1, 3)
        if pt["nrm"] is not None:
            nrm = dir_to_game(pt["nrm"]); nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        else:
            nrm = None
        uvs = pt["uvs"]
        geo.append(dict(pt=pt, pos=pos, tri=tri, nrm=nrm, uvs=uvs, bones=bones, w=w, slot=slot_of[pt["material"]]))
    # ---- winding: MK11 stores normals along cross(e2, e1) of its index order
    votes = 0.0
    for g in geo:
        p, t = g["pos"], g["tri"]
        fn = np.cross(p[t[:, 2]] - p[t[:, 0]], p[t[:, 1]] - p[t[:, 0]])
        if g["nrm"] is not None:
            votes += (fn * (g["nrm"][t[:, 0]] + g["nrm"][t[:, 1]] + g["nrm"][t[:, 2]])).sum(1).clip(-1, 1).sum()
    flip = votes < 0
    for g in geo:
        if flip:
            g["tri"] = g["tri"][:, ::-1]
        if g["nrm"] is None:                      # smooth normals from geometry
            p, t = g["pos"], g["tri"]
            fn = np.cross(p[t[:, 2]] - p[t[:, 0]], p[t[:, 1]] - p[t[:, 0]])
            n = np.zeros_like(p)
            for k in range(3):
                np.add.at(n, t[:, k], fn)
            g["nrm"] = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    log("  winding %s; normals %s" % ("reversed to MK11 order" if flip else "already in MK11 order",
                                      "from model" if all(g["pt"]["nrm"] is not None for g in geo) else "computed"))
    # ---- group by slot -> one section + one chunk per slot
    secs, chunk_bones = [], []
    P_, N_, T_, S_, L_, W_, UV_, I_ = [], [], [], [], [], [], [], []
    nuv = 4
    vbase = 0; ibase = 0
    for slot in range(nslots):
        gs = [g for g in geo if g["slot"] == slot]
        if not gs:
            continue
        pos = np.concatenate([g["pos"] for g in gs]); nrm = np.concatenate([g["nrm"] for g in gs])
        tri = np.concatenate([g["tri"] + off for g, off in zip(gs, np.cumsum([0] + [len(g["pos"]) for g in gs])[:-1])])
        uv0 = np.concatenate([g["uvs"][0] for g in gs])
        uvsets = []
        for k in range(nuv):
            uvsets.append(np.concatenate([(g["uvs"][k] if k < len(g["uvs"]) else g["uvs"][0]) for g in gs]))
        bones = np.concatenate([g["bones"] for g in gs]); w = np.concatenate([g["w"] for g in gs])
        tan, sign = compute_tangents(pos, nrm, uv0, tri)
        w8, order = quantize_weights(w)
        bsorted = np.take_along_axis(bones, order, 1)
        bsorted[w8 == 0] = -1
        bonemap = []
        for b in bsorted.ravel():
            if b >= 0 and b not in bonemap:
                bonemap.append(int(b))
        if len(bonemap) > MAX_CHUNK_BONES:
            raise MK11Error("material slot %d uses %d bones (max %d per section)" % (slot, len(bonemap), MAX_CHUNK_BONES))
        lut = {b: i for i, b in enumerate(bonemap)}
        local = np.vectorize(lambda b: lut.get(int(b), 0))(bsorted).astype(np.int64)
        nv = len(pos)
        secs.append(dict(mat=slot, chunk=len(chunk_bones), base=ibase, ntri=len(tri), flags=b"\0\0\0"))
        chunk_bones.append((vbase, nv, bonemap))
        P_.append(pos); N_.append(nrm); T_.append(tan); S_.append(sign); L_.append(local); W_.append(w8)
        UV_.append(np.stack(uvsets, 1)); I_.append(tri.ravel() + vbase)
        vbase += nv; ibase += 3 * len(tri)
    pos = np.concatenate(P_).astype(np.float32)
    lod = dict(sections=secs, indices=np.concatenate(I_), pos=pos, normal=np.concatenate(N_),
               tangent=np.concatenate(T_), sign=np.concatenate(S_), chunk_bones=chunk_bones,
               local=np.concatenate(L_), w8=np.concatenate(W_), uv=np.concatenate(UV_).astype(np.float16))
    lod["bytes"] = M.write_lod(lod["sections"], lod["indices"], lod["pos"], lod["normal"], lod["tangent"], lod["sign"],
                               chunk_bones, lod["local"], lod["w8"], lod["uv"], len(skel["names"]),
                               colors=np.zeros((len(pos), 4), np.uint8), extra=np.zeros(len(pos), "<u4"))
    return lod


# ============================================================================================ textures
def load_defaults():
    with open(DEFAULTS, encoding="utf-8") as f:
        return json.load(f)


def solid(spec):
    vals = tuple(int(x) for x in spec.split(":", 1)[1].split(","))
    if len(vals) != 4:
        raise MK11Error("solid colours need 4 values: %s" % spec)
    return vals


def prepare_image(param, source, defaults, log):
    """Turn a user/embedded image or 'solid:' spec into what build_mips expects, applying per-parameter rules."""
    from PIL import Image
    rule = defaults.get("rules", {}).get(param, {})
    if isinstance(source, str) and source.startswith("solid:"):
        return solid(source), source
    im = source if not isinstance(source, str) else Image.open(source)
    label = getattr(im, "filename", "") or (source if isinstance(source, str) else "embedded image")
    im = im.convert("RGBA")
    if rule.get("flip_green"):                       # OpenGL (Blender) normal maps -> DirectX (MK11)
        r, g, b, a = im.split()
        im = Image.merge("RGBA", (r, g.point(lambda v: 255 - v), b, a))
    if "alpha" in rule:
        im.putalpha(int(rule["alpha"]))
    return im, os.path.basename(str(label))


# ============================================================================================ preview
_PREFER = ("Spine", "Neck", "Head", "Middle")      # chains a joint with several children should follow


def primary_child(names, parents, i, kids):
    """The child a joint 'points at' in a viewer: continuation of the same chain if any (Spine->Spine1,
    Finger1->Finger2, C_..Joint0->Joint1), else spine/neck/head/middle finger, else the farthest regular child.
    Helper, roll and locator bones are never targets (they sit on top of the joint they correct)."""
    import re
    nm = names[i]
    def base(s):
        return re.sub(r"\d+$", "", s).lower()
    if nm.startswith("C_"):
        cands = [k for k in kids if names[k].startswith("C_")]
    else:
        cands = [k for k in kids if not names[k].startswith("C_") and "helper" not in names[k].lower()
                 and "Roll" not in names[k] and names[k] not in ROOT_BONES]
    same = [k for k in cands if base(names[k]) == base(nm)]
    if same:
        return same[0]
    for t in _PREFER:
        pref = [k for k in cands if t in names[k]]
        if pref:
            return pref[0]
    if not cands:
        return None
    return cands[0] if len(cands) == 1 else None     # several unrelated children: caller picks the farthest


def aim_joints(names, parents, world):
    """Rotate each joint's frame (position unchanged) so its local +Y points at its primary child, the way
    Blender's glTF importer draws bones. glTF stores no bone tails, so without this Blender guesses directions
    from MK11's joint frames and the bones splay out. Positions, skinning and bone names are unaffected."""
    n = len(names)
    kids = [[] for _ in range(n)]
    for i in range(1, n):
        kids[parents[i]].append(i)
    pos = [w[:3, 3] for w in world]
    out, ydir = [], [None] * n
    for i in range(n):
        R = world[i][:3, :3]
        u, _, vt = np.linalg.svd(R); R = u @ vt
        if np.linalg.det(R) < 0:
            u[:, -1] *= -1; R = u @ vt
        c = primary_child(names, parents, i, kids[i])
        if c is None and kids[i]:                                    # several regular children: the farthest
            regular = [k for k in kids[i] if not names[k].startswith("C_") and "helper" not in names[k].lower()]
            if regular:
                c = max(regular, key=lambda k: np.linalg.norm(pos[k] - pos[i]))
        d = (pos[c] - pos[i]) if c is not None else None
        if d is None or np.linalg.norm(d) < 1e-5:                    # end joint: continue the parent's direction
            d = ydir[parents[i]] if i and ydir[parents[i]] is not None else R[:, 1]
        d = d / np.linalg.norm(d)
        y = R[:, 1]
        v = np.cross(y, d); s = np.linalg.norm(v); cth = float(np.dot(y, d))
        if s < 1e-9:
            rot = np.eye(3) if cth > 0 else -np.eye(3) + 2 * np.outer(R[:, 0], R[:, 0])
        else:
            k = v / s; K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
            rot = np.eye(3) + s * K + (1 - cth) * (K @ K)            # smallest rotation taking y onto d (keeps roll)
        W = np.eye(4); W[:3, :3] = rot @ R; W[:3, 3] = pos[i]
        out.append(W); ydir[i] = d
    return out


def write_preview(pkg, psf_path, mesh_name, out_path, color_param_tex=None, log=print, textures=True):
    """Export a mesh as a rigged .glb read straight from package + .psf (used for previews of converted output and
    for exporting original characters as Blender references). textures=False embeds no images at all."""
    mi = pkg.find(mesh_name, "SkeletalMesh"); info = M.mesh_info(pkg, mi)
    skel = M.read_skeleton(pkg, pkg.child(mi, "Skeleton"))
    with open(psf_path, "rb") as psf:
        lods = []
        for l in info["lods"]:
            _, raw = read_lod_raw(pkg, l, psf)
            lods.append(M.read_lod(raw))
        lod = max(lods, key=lambda x: x["nverts"])          # highest detail (record order != LOD order on faces)
        # one material per slot, named exactly like the game's slot (the converter maps materials back by name),
        # each showing its own colour texture
        slots = material_slots(pkg, info)
        nslots = max([len(slots)] + [s["mat"] + 1 for s in lod["sections"]])
        slot_names = [slots[k][0] if k < len(slots) else "slot%02d" % k for k in range(nslots)]
        slot_tex = []
        for k in range(nslots):
            tex = None
            if not textures:
                slot_tex.append(None); continue
            if k < len(slots) and slots[k][1]:
                try:
                    tex = material_textures(pkg, slots[k][1]).get("ColorOverride")
                except Exception:
                    tex = None
            slot_tex.append(tex or color_param_tex)
        pngs = {}
        for tex in set(t for t in slot_tex if t):
            try:
                t = T.read_texture(pkg, tex)
                m = t["mips"][min(1, len(t["mips"]) - 1)]
                im = T.decode(t["fmt"], m["w"], m["h"], T.read_mip(pkg, t, m, psf), t["srgb"]).convert("RGB")
                b = io.BytesIO(); im.save(b, "PNG"); pngs[tex] = (b.getvalue(), t["name"])
            except Exception:
                pass
    w = GLBWriter()
    n = len(skel["names"])
    Wg = []
    for m4 in skel["world"]:
        Mc = m4.T.copy(); out = np.eye(4)
        out[:3, :3] = C @ Mc[:3, :3] @ C; out[:3, 3] = C @ Mc[:3, 3] * SCALE
        Wg.append(out)
    Wg = aim_joints(skel["names"], skel["parents"], Wg)
    for i in range(n):
        par = skel["parents"][i]
        L = Wg[i] if i == 0 else np.linalg.inv(Wg[par]) @ Wg[i]
        u, _, vt = np.linalg.svd(L[:3, :3]); R = u @ vt
        if np.linalg.det(R) < 0:
            u[:, -1] *= -1; R = u @ vt
        q = _quat(R)
        w.j["nodes"].append({"name": skel["names"][i], "translation": L[:3, 3].tolist(), "rotation": q.tolist()})
    for i in range(1, n):
        w.j["nodes"][skel["parents"][i]].setdefault("children", []).append(i)
    Wn = [None] * n
    for i in range(n):
        nd = w.j["nodes"][i]; L = np.eye(4); L[:3, :3] = _rotm(nd["rotation"]); L[:3, 3] = nd["translation"]
        Wn[i] = L if i == 0 else Wn[skel["parents"][i]] @ L
    ibm = np.array([np.linalg.inv(m4).T.reshape(16) for m4 in Wn], np.float32)
    w.j["skins"].append({"joints": list(range(n)), "inverseBindMatrices": w.acc(ibm, 5126, "MAT4"), "skeleton": 0})
    w.j["scenes"][0]["nodes"].append(0)
    pos = (lod["pos"].astype(np.float64) @ C.T * SCALE).astype(np.float32)
    nrm = (lod["normal"] @ C.T); nrm = (nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)).astype(np.float32)
    attrs = {"POSITION": w.acc(pos, 5126, "VEC3", 34962, True), "NORMAL": w.acc(nrm, 5126, "VEC3", 34962),
             "TEXCOORD_0": w.acc(lod["uv"][:, 0].copy(), 5126, "VEC2", 34962),
             "JOINTS_0": w.acc(lod["bones"].astype(np.uint16), 5123, "VEC4", 34962),
             "WEIGHTS_0": w.acc((lod["weights"] / 255.0).astype(np.float32), 5126, "VEC4", 34962)}
    tex_index = {}
    for k in range(nslots):
        mat = {"name": slot_names[k], "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": 0.8}}
        tex = slot_tex[k]
        if tex in pngs:
            if tex not in tex_index:
                tex_index[tex] = w.image(pngs[tex][0], pngs[tex][1])
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_index[tex]}
        w.j["materials"].append(mat)
    prims = []
    for s in lod["sections"]:
        tri = lod["indices"][s["base"]:s["base"] + 3 * s["ntri"]].reshape(-1, 3)[:, ::-1]
        prims.append({"attributes": attrs, "material": s["mat"],
                      "indices": w.acc(np.ascontiguousarray(tri).reshape(-1).astype(np.uint32), 5125, "SCALAR", 34963)})
    w.j["meshes"].append({"name": mesh_name, "primitives": prims})
    w.j["nodes"].append({"name": mesh_name, "mesh": 0, "skin": 0})
    w.j["scenes"][0]["nodes"].append(len(w.j["nodes"]) - 1)
    w.save(out_path)
    log("wrote %s" % os.path.basename(out_path))


def _rotm(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _quat(R):
    tr = np.trace(R)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2; q = [(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1 + R[0, 0] - R[1, 1] - R[2, 2]) * 2; q = [0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s]
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1 + R[1, 1] - R[0, 0] - R[2, 2]) * 2; q = [(R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s]
    else:
        s = np.sqrt(1 + R[2, 2] - R[0, 0] - R[1, 1]) * 2; q = [(R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s]
    q = np.array(q); return q / np.linalg.norm(q)


# ============================================================================================ main job
def weapon_gear_assets(pkg, items, gear_assets):
    """Gear sets that are weapons. A character's presets list the animation templates its parts ride on
    (TemplateMeshes); besides the body template, weapons have their own (Liu Kang: LIU_GEAR_B_Nunchucks_TEMPLATE
    under LIU_GEAR.LIU_GEAR_B). A gear set is a weapon when such a template lives in that gear category's folder."""
    templates = set()
    for e in pkg.exports:
        if pkg.class_name(e["index"]) != "CAPCharacterPreset":
            continue
        _, ps = P.read_props(pkg, pkg.export_data(e["index"]), 0)
        t = P.prop(ps, "TemplateMeshes")
        if t:
            n = struct.unpack_from("<i", t["value"])[0]
            templates.update(r for r in struct.unpack_from("<%di" % n, t["value"], 4) if r > 0)

    def category(idx):          # path element right after the "<CODE>_GEAR" folder, e.g. LIU_GEAR_B
        parts = pkg.full_path(idx).split(".")
        for i, part in enumerate(parts[:-1]):
            if part.upper().endswith("_GEAR"):
                return parts[i + 1].upper()
        return None
    weapon_cats = {category(t) for t in templates} - {None}
    out = set()
    for a, _, meshes in items:
        if a in gear_assets and any(category(m) in weapon_cats for m in meshes):
            out.add(a)
    return out


def outfit_groups(pkg, body):
    """Everything the user may hide, grouped, with a suggested default. Mesh ids are full object paths (names can
    repeat across DLC folders). Kinds: 'outfit' (face/eyes/hair worn with this body - default hidden, a full
    replacement usually brings its own head), 'gear' (headgear, bracers... - default hidden, it would float on a
    custom body), 'weapon' (part of the moveset - default visible)."""
    items = cap_items(pkg)
    skin_sets = sorted({a for a, _, ms in items if body in ms})
    gear_sets = sorted({a for a, _, _ in items if a not in skin_sets})
    weapons = weapon_gear_assets(pkg, items, gear_sets)
    hideable = {}
    for e in pkg.exports:
        if pkg.class_name(e["index"]) == "SkeletalMesh":
            info = M.mesh_info(pkg, e["index"])
            hideable[e["index"]] = bool(info["lods"]) and all(l["streamed"] for l in info["lods"])

    def label(idx):
        path = pkg.full_path(idx)
        return pkg.obj_name(idx) + ("  [%s]" % ".".join(path.split(".")[:2]) if path.startswith("DLC.") else "")
    groups, seen = [], set()
    plan = [("Outfit parts worn with %s" % pkg.obj_name(body), "outfit", [it for it in items if body in it[2]])]
    for g in gear_sets:
        plan.append((g, "weapon" if g in weapons else "gear", [it for it in items if it[0] == g]))
    for title, kind, its in plan:
        meshes = []
        for _, _, ms in its:
            for m in ms:
                if m != body and m not in seen and hideable.get(m):
                    seen.add(m); meshes.append((pkg.full_path(m), label(m)))
        if meshes:
            groups.append(dict(name=title, kind=kind, meshes=sorted(meshes, key=lambda x: x[1]),
                               default_hidden=kind != "weapon"))
    return groups


def default_hidden(groups):
    return {mid for g in groups if g["default_hidden"] for mid, _ in g["meshes"]}


def resolve_selection(groups, show=(), hide=()):
    """Defaults adjusted by user tokens (group names like LIU_GearB, mesh names like LIU_HAIR_D, or full paths)."""
    def ids(token):
        t = token.lower(); out = set()
        for g in groups:
            if g["name"].lower() == t or (g["kind"] == "outfit" and t == "outfit"):
                out.update(mid for mid, _ in g["meshes"])
            for mid, lab in g["meshes"]:
                if t in (mid.lower(), mid.split(".")[-1].lower()):
                    out.add(mid)
        if not out:
            raise MK11Error("'%s' is not a hideable mesh or group of this character" % token)
        return out
    sel = default_hidden(groups)
    for t in show:
        sel -= ids(t)
    for t in hide:
        sel |= ids(t)
    return sel


def convert(game_dir, package_name, mesh_name, model_path, out_dir, textures=None, keep_textures=False,
            hide=None, log=print, defaults=None):
    """Build Asset\\<package>.xxx/.psf in out_dir. `textures`: {param: image path | 'solid:r,g,b,a' | 'keep'};
    missing params use the model's embedded base-colour image (ColorOverride) and the texture defaults
    (`defaults`, normally config/texture_defaults.json; a Mod Project passes the creator's saved copy)."""
    defaults = defaults if defaults is not None else load_defaults()
    src_xxx = os.path.join(game_dir, "Asset", package_name + ".xxx")
    src_psf = os.path.join(game_dir, "Asset", package_name + ".psf")
    for f in (src_xxx, src_psf):
        if not os.path.isfile(f):
            raise MK11Error("game file not found: %s" % f)
    asset_dir = os.path.join(out_dir, "Asset")
    os.makedirs(asset_dir, exist_ok=True)
    out_xxx = os.path.join(asset_dir, os.path.basename(src_xxx))
    out_psf = os.path.join(asset_dir, os.path.basename(src_psf))
    report = dict(package=package_name, mesh=mesh_name, model=os.path.abspath(model_path),
                  created=datetime.datetime.now().astimezone().isoformat())

    log("Reading %s" % os.path.basename(src_xxx))
    pkg = Package(src_xxx)
    body = pkg.find(mesh_name, "SkeletalMesh")
    info = M.mesh_info(pkg, body)
    if not info["lods"] or not all(l["streamed"] for l in info["lods"]):
        raise MK11Error("%s does not stream its LODs; not supported" % mesh_name)
    skel = M.read_skeleton(pkg, pkg.child(body, "Skeleton"))
    slots = material_slots(pkg, info)
    log("Target %s: %d bones, %d LOD(s), material slots %s" % (mesh_name, len(skel["names"]), len(info["lods"]),
                                                                [s[0] for s in slots]))

    # meshes that ship with the game: used to skip reference meshes left in the .glb
    orig_counts = set()
    with open(src_psf, "rb") as psf:
        for e in pkg.exports:
            if pkg.class_name(e["index"]) == "SkeletalMesh" and e["index"] != body:
                pass
        _, raw = read_lod_raw(pkg, info["lods"][0], psf)
        orig_body = M.read_lod(raw)
    orig_counts.add(orig_body["nverts"])

    # ---- outfit: what to hide (the caller's choice; None = suggested defaults)
    groups = outfit_groups(pkg, body)
    chosen = default_hidden(groups) if hide is None else set(hide)
    by_path = {pkg.full_path(e["index"]): e["index"] for e in pkg.exports if pkg.class_name(e["index"]) == "SkeletalMesh"}
    unknown = [h for h in chosen if h not in by_path]
    if unknown:
        raise MK11Error("not meshes of this package: %s" % unknown[:5])
    hide = {by_path[h] for h in chosen} - {body}
    for g in groups:
        ids = [mid for mid, _ in g["meshes"]]
        n = sum(1 for mid in ids if mid in chosen)
        log("  %-34s %-7s %d/%d hidden" % (g["name"], "(%s)" % g["kind"], n, len(ids)))
    report["hidden_selection"] = sorted(chosen)

    # ---- model
    log("Reading model %s" % os.path.basename(model_path))
    g, parts, slot_of = load_model(model_path, skel, [s[0] for s in slots], ignore_vertex_counts=orig_counts, log=log)
    lod = build_lod(parts, slot_of, skel, len(slots), log=log)
    nv = len(lod["pos"]); nt = len(lod["indices"]) // 3
    log("Built LOD: %d vertices, %d triangles, sections %s" % (nv, nt, [(s["mat"], s["ntri"]) for s in lod["sections"]]))
    chk = M.read_lod(lod["bytes"])            # self-check of the serializer before touching anything
    if not (np.array_equal(chk["indices"], lod["indices"].astype(np.int64)) and np.allclose(chk["pos"], lod["pos"])
            and np.array_equal(chk["weights"], lod["w8"].astype(np.int64))):
        raise MK11Error("internal check failed: written LOD does not read back identically")

    patcher = PsfPatcher(pkg, src_psf, out_psf, log=log)
    try:
        # ---- body: every LOD gets the new model (bodies ship with one LOD)
        for l in info["lods"]:
            rec = M.lod_record(pkg, l)
            patcher.replace(rec, lod["bytes"])
            M.set_lod_size(pkg, info, l, len(lod["bytes"]))
        o, ext, rad = M.get_bounds(info)
        lo = np.minimum(o - ext, lod["pos"].min(0)); hi = np.maximum(o + ext, lod["pos"].max(0))
        org = (lo + hi) / 2; ex = (hi - lo) / 2
        M.set_bounds(pkg, M.mesh_info(pkg, body), org.tolist(), ex.tolist(), float(np.linalg.norm(ex)))
        # ---- hide the rest of the outfit
        hidden, skipped = [], []
        with open(src_psf, "rb") as psf:
            for mi in sorted(hide):
                minfo = M.mesh_info(pkg, mi)
                if not minfo["lods"] or not all(x["streamed"] for x in minfo["lods"]):
                    skipped.append(pkg.obj_name(mi)); continue
                ok = True
                for l in minfo["lods"]:
                    rec, raw = read_lod_raw(pkg, l, psf)
                    new = M.hide_lod(raw)
                    if new is None:
                        ok = False; break
                    patcher.replace(rec, new)
                    M.set_lod_size(pkg, minfo, l, len(new))
                (hidden if ok else skipped).append(pkg.obj_name(mi))
        log("Hidden: %s" % ", ".join(hidden))
        if skipped:
            log("Could not hide (unsupported layout, left visible): %s" % ", ".join(skipped))
        report["hidden_meshes"] = hidden; report["not_hidden"] = skipped

        # ---- textures
        report["textures"] = {}
        if not keep_textures:
            textures = dict(textures or {})
            written = {}            # texture export -> (slot name, source key)
            for slot in sorted({s["mat"] for s in lod["sections"]}):
                sname, mat = slots[slot]
                if not mat:
                    log("  slot %d has no material; textures untouched" % slot); continue
                params = material_textures(pkg, mat)
                embedded = None
                for pt in parts:
                    if slot_of[pt["material"]] == slot and pt["material_index"] is not None:
                        embedded = g.material_image(pt["material_index"])
                        if embedded is not None:
                            break
                for param, src_default in defaults["parameters"].items():
                    if param not in params:
                        continue
                    source = textures.get(param)
                    if source is None and param == defaults.get("color_parameter") and embedded is not None:
                        source = embedded
                    if source is None:
                        source = src_default
                    if source == "keep":
                        log("  %s.%s -> kept original" % (sname, param)); continue
                    tex = T.read_texture(pkg, params[param])
                    srckey = source if isinstance(source, str) else hashlib.md5(source.tobytes()).hexdigest()
                    prev = written.get(params[param])
                    if prev is not None:                  # slots of one body often share the same textures
                        if prev[1] != srckey:
                            raise MK11Error("material slots %s and %s share the texture %s, but your model gives them "
                                            "different images. Put both on one texture atlas, or use one material."
                                            % (prev[0], sname, tex["name"]))
                        log("  %s.%s -> same texture as %s (already written)" % (sname, param, prev[0])); continue
                    written[params[param]] = (sname, srckey)
                    img, label = prepare_image(param, source, defaults, log)
                    log("  %s.%s -> %s (%s %dx%d, %d mips)" % (sname, param, label, T.FORMATS[tex["fmt"]], tex["w"], tex["h"], len(tex["mips"])))
                    datas = T.build_mips(tex, img, log=log)
                    T.write_mips(pkg, patcher, tex, datas)
                    report["textures"]["%s.%s" % (sname, param)] = dict(texture=tex["name"], source=label)
        patcher.close()
    except Exception:
        patcher.close()
        raise
    log("Saving package")
    pkg.save(out_xxx, log=log)

    # ---- verify the output
    log("Verifying output (re-reading the written files)")
    verify(src_xxx, out_xxx, out_psf, mesh_name, lod, report.get("hidden_meshes", []), report["textures"], log)
    color_tex = None
    out_pkg = Package(out_xxx)
    try:
        mi = out_pkg.find(mesh_name, "SkeletalMesh")
        slot0 = material_slots(out_pkg, M.mesh_info(out_pkg, mi))[lod["sections"][0]["mat"]][1]
        if slot0:
            color_tex = material_textures(out_pkg, slot0).get(defaults.get("color_parameter"))
    except MK11Error:
        pass
    write_preview(out_pkg, out_psf, mesh_name, os.path.join(out_dir, "preview_%s.glb" % mesh_name.split(".")[-1]), color_tex, log)
    report.update(vertices=nv, triangles=nt, output=dict(xxx=out_xxx, psf=out_psf),
                  model_sha256=sha256(model_path),
                  verification="output re-parsed and compared with the built data; in-game behaviour not verified")
    with open(os.path.join(out_dir, "build.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def verify(src_xxx, out_xxx, out_psf, mesh_name, lod, hidden, textures, log):
    src = Package(src_xxx); out = Package(out_xxx)
    if len(src.exports) != len(out.exports) or src.names != out.names:
        raise MK11Error("verify: package tables changed unexpectedly")
    # every segment decompresses and only intended bytes differ
    changed = 0
    for ps, po in zip(src.inner, out.inner):
        for a, b in zip(ps["subs"], po["subs"]):
            da = src.uread(a["uoff"], a["usize"]); db = out.uread(b["uoff"], b["usize"])
            if len(da) != len(db):
                raise MK11Error("verify: segment size changed")
            if da != db:
                changed += 1
    psf_size = os.path.getsize(out_psf)
    with open(out_psf, "rb") as psf:
        mi = out.find(mesh_name, "SkeletalMesh"); info = M.mesh_info(out, mi)
        for l in info["lods"]:
            rec, raw = read_lod_raw(out, l, psf)
            if rec["vals"][2] + rec["vals"][1] > psf_size or len(raw) != l["size"] or raw != lod["bytes"]:
                raise MK11Error("verify: body LOD in the output does not match the built data")
            back = M.read_lod(raw)
            if back["nverts"] != len(lod["pos"]):
                raise MK11Error("verify: vertex count mismatch")
        for name in hidden:
            hi = M.mesh_info(out, out.find(name, "SkeletalMesh"))
            for l in hi["lods"]:
                rec, raw = read_lod_raw(out, l, psf)
                if len(raw) != l["size"]:
                    raise MK11Error("verify: size field of hidden mesh %s not updated" % name)
                secs = struct.unpack_from("<I", raw)[0]
                ntris = [struct.unpack_from("<I", raw, 4 + 15 * k + 8)[0] for k in range(secs)]
                if any(n != 1 for n in ntris):
                    raise MK11Error("verify: %s is not hidden" % name)
        for key, t in textures.items():
            tex = T.read_texture(out, out.find(t["texture"], "Texture2D"))
            for m in tex["mips"]:
                T.read_mip(out, tex, m, psf)
            last = tex["mips"][-1]
            T.decode(tex["fmt"], max(4, last["w"]), max(4, last["h"]), T.read_mip(out, tex, last, psf))
    log("verify: OK (%d package segments changed; body LOD, %d hidden meshes and %d textures re-read from the output)"
        % (changed, len(hidden), len(textures)))
