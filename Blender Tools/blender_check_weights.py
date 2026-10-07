# SPDX-License-Identifier: GPL-2.0-or-later
# This script uses Blender's Python API, so it is licensed under the GNU GPL v2 or later (see LICENSES/GPL-2.0.txt),
# not under the PolyForm Noncommercial license that covers the rest of MK11 Character Studio.
"""MK11 weight fixer for Blender (Scripting tab -> Open -> Run Script).

1. Sets Deform ON only for the 84 bones LIU_SKIN_A's in-game skeleton skins to.
2. For every skinned mesh you made (Liu's originals are skipped by vertex count), each weight on an unsupported bone is TRANSFERRED
   (not deleted) to a supported bone:
     - walk up the bad bone's parents to the first supported ancestor,
     - among that ancestor and its supported descendants, pick the bone whose segment is closest to the vertex.
   Root bones (Reference, CenterOfMass, position_locator) have no supported ancestor, so the closest supported
   bone anywhere is used. The vertex's total weight is unchanged, so nothing is left unweighted.
3. Removes the emptied vertex groups and reports what moved where.
4. Gives UNWEIGHTED vertices weights (Blender's glTF exporter would otherwise park them on a fake 'neutral_bone').
   Automatic Weights often skips small loose pieces (buttons, rivets, buckles):
     - a loose piece with no weights at all copies the weights of the nearest weighted vertex on the rest of the
       model, so it moves rigidly with the skin it sits on;
     - stray unweighted vertices inside a weighted piece copy their nearest weighted neighbour in that piece.
Run it again afterwards: it should report OK. Then Weights > Limit Total (4) and Normalize All.
"""
import bpy
from mathutils import Vector
from mathutils.geometry import intersect_point_line
from mathutils.kdtree import KDTree

ALLOWED = set("""Hips Spine Spine1 Spine2 Spine3 Neck Neck1 Head
LeftShoulder LeftArm LeftHand LeftHandIndex1 LeftHandIndex2 LeftHandIndex3 LeftHandMiddle1 LeftHandMiddle2 LeftHandMiddle3
LeftHandRing1 LeftHandRing2 LeftHandRing3 LeftInHandPinky LeftHandPinky1 LeftHandPinky2 LeftHandPinky3
LeftHandThumb1 LeftHandThumb2 LeftHandThumb3 LeftHand_helper LeftForeArm_helper LeftLowerForeArm_helper LeftElbow_helper
LeftElbowPoint_helper LeftUpperForeArm_helper LeftArmRoll_helper LeftArmBicep_helper LeftDeltoid_helper
RightShoulder RightArm RightHand RightHandIndex1 RightHandIndex2 RightHandIndex3 RightHandMiddle1 RightHandMiddle2 RightHandMiddle3
RightHandRing1 RightHandRing2 RightHandRing3 RightInHandPinky RightHandPinky1 RightHandPinky2 RightHandPinky3
RightHandThumb1 RightHandThumb2 RightHandThumb3 RightHand_helper RightForeArm_helper RightLowerForeArm_helper RightElbow_helper
RightElbowPoint_helper RightUpperForeArm_helper RightArmRoll_helper RightArmBicep_helper RightDeltoid_helper
LeftPectoral_helper RightPectoral_helper LeftUpLeg LeftUpLegRoll LeftLeg LeftLegRoll LeftFoot LeftToeBase LeftFoot_helper
LeftKnee_helper RightUpLeg RightUpLegRoll RightLeg RightLegRoll RightFoot RightToeBase RightFoot_helper RightKnee_helper
LeftHips_helper RightHips_helper""".split())


def armature_of(ob):
    for m in ob.modifiers:
        if m.type == "ARMATURE" and m.object:
            return m.object
    return ob.parent if ob.parent and ob.parent.type == "ARMATURE" else None


def candidates_for(bone):
    """Supported ancestor + its supported descendants (or every supported bone for root bones)."""
    anc = bone.parent
    while anc is not None and anc.name not in ALLOWED:
        anc = anc.parent
    if anc is None:
        return None  # caller uses all supported bones
    return [anc] + [b for b in anc.children_recursive if b.name in ALLOWED]


def seg_dist(p, b):
    h, t = b.head_local, b.tail_local
    if (t - h).length < 1e-6:
        return (p - h).length
    q, f = intersect_point_line(p, h, t)
    f = min(max(f, 0.0), 1.0)
    return (p - (h + (t - h) * f)).length


def fix_unweighted(ob, to_arm):
    """Give weights to vertices that have none on the body bones. Returns [(description, count, main bone)]."""
    me = ob.data
    gname = {vg.index: vg.name for vg in ob.vertex_groups}

    def weights(v):
        return [(gname[g.group], g.weight) for g in v.groups if g.weight > 0 and gname.get(g.group) in ALLOWED]
    W = [weights(v) for v in me.vertices]
    empty = [i for i, w in enumerate(W) if not w]
    if not empty:
        return []
    if len(empty) == len(W):
        print(ob.name, "- NOT WEIGHTED AT ALL: parent it to the armature with Automatic Weights first")
        return []
    # connected pieces (union-find over edges)
    parent = list(range(len(me.vertices)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for e in me.edges:
        a, b = find(e.vertices[0]), find(e.vertices[1])
        if a != b:
            parent[a] = b
    piece = [find(i) for i in range(len(W))]
    co = [to_arm @ v.co for v in me.vertices]
    weighted = [i for i, w in enumerate(W) if w]
    kd_all = KDTree(len(weighted))
    for i in weighted:
        kd_all.insert(co[i], i)
    kd_all.balance()
    by_piece = {}
    for i in weighted:
        by_piece.setdefault(piece[i], []).append(i)
    kd_piece = {}
    groups = {}
    report = {}
    empty_pieces = {}
    for i in empty:
        empty_pieces.setdefault(piece[i], []).append(i)
    for pc, verts in empty_pieces.items():
        if pc in by_piece:                       # stray vertices inside a weighted piece: nearest neighbour in it
            if pc not in kd_piece:
                t = KDTree(len(by_piece[pc]))
                for i in by_piece[pc]:
                    t.insert(co[i], i)
                t.balance(); kd_piece[pc] = t
            pairs = [(i, kd_piece[pc].find(co[i])[1]) for i in verts]
            kind = "stray unweighted vertices"
        else:                                    # whole loose piece: one source, so it moves rigidly
            best = min((kd_all.find(co[i]) + (i,) for i in verts), key=lambda x: x[2])
            pairs = [(i, best[1]) for i in verts]
            kind = "loose piece with no weights (%d verts)" % len(verts)
        for i, src in pairs:
            for name, w in W[src]:
                vg = groups.get(name) or ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
                groups[name] = vg
                vg.add([i], w, "REPLACE")
        main = max(W[pairs[0][1]], key=lambda x: x[1])[0]
        key = (kind if kind.startswith("stray") else "loose piece(s) with no weights", main)
        report[key] = report.get(key, 0) + len(verts)
    return [(k, n, b) for (k, b), n in sorted(report.items(), key=lambda x: -x[1])]


for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
    for b in arm.data.bones:
        b.use_deform = b.name in ALLOWED
    print(arm.name, "- deform bones now:", sum(b.use_deform for b in arm.data.bones))

# Liu Kang's original reference meshes are recognised by their exact vertex counts (names don't matter).
LIU_ORIGINAL_VERTS = {56266, 26791, 3560, 31452}

for ob in [o for o in bpy.data.objects if o.type == "MESH" and o.vertex_groups]:
    if len(ob.data.vertices) in LIU_ORIGINAL_VERTS:
        print(ob.name, "- skipped (Liu Kang's original reference mesh)"); continue
    arm = armature_of(ob)
    if arm is None:
        print(ob.name, "- SKIPPED: not attached to an armature"); continue
    bones = arm.data.bones
    all_ok = [b for b in bones if b.name in ALLOWED]
    to_arm = arm.matrix_world.inverted() @ ob.matrix_world
    bad_groups = [vg for vg in ob.vertex_groups if vg.name not in ALLOWED]
    bad_idx = {vg.index: vg for vg in bad_groups}
    moved = {}          # (from, to) -> vertex count
    cand_cache = {}
    adds = []           # (target name, vertex index, weight)
    for v in ob.data.vertices:
        p = to_arm @ v.co
        for g in v.groups:
            vg = bad_idx.get(g.group)
            if vg is None or g.weight <= 0:
                continue
            src = bones.get(vg.name)
            if vg.name not in cand_cache:
                cand_cache[vg.name] = (candidates_for(src) if src else None) or all_ok
            tgt = min(cand_cache[vg.name], key=lambda b: seg_dist(p, b))
            adds.append((tgt.name, v.index, g.weight))
            moved[(vg.name, tgt.name)] = moved.get((vg.name, tgt.name), 0) + 1
    for name, vi, w in adds:
        tg = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
        tg.add([vi], w, "ADD")
    for vg in bad_groups:
        ob.vertex_groups.remove(vg)
    fixed = fix_unweighted(ob, to_arm)
    if moved:
        print(ob.name, "- TRANSFERRED weights from %d unsupported bones:" % len({a for a, _ in moved}))
        for (a, b), n in sorted(moved.items(), key=lambda x: -x[1]):
            print("     %-28s -> %-26s %5d verts" % (a, b, n))
    if fixed:
        print(ob.name, "- WEIGHTED %d vertices that had no bone weights:" % sum(n for _, n, _ in fixed))
        for kind, n, bone in fixed:
            print("     %-46s %5d verts -> now follows %s" % (kind, n, bone))
    if moved or fixed:
        print("   -> now run Weights > Limit Total (4) and Weights > Normalize All, then re-run this script")
    else:
        print(ob.name, "- OK: every vertex is weighted, only to the 84 body bones")
