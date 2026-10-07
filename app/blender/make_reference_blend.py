# SPDX-License-Identifier: GPL-2.0-or-later
# This script uses Blender's Python API, so it is licensed under the GNU GPL v2 or later (see LICENSES/GPL-2.0.txt),
# not under the PolyForm Noncommercial license that covers the rest of MK11 Character Studio.
"""Run by MK11 Character Studio inside Blender (background): turn an exported reference .glb into a ready-to-use .blend.

    blender -b --factory-startup --python make_reference_blend.py -- IN.glb OUT.blend deform_bones.json

- no icosphere bone shapes; octahedral bones drawn in front of the mesh
- bones point at their child (the exporter aims the joints; Blender's default importer heuristic keeps that)
- bones that would be absurdly long (root / locator bones sitting on the floor, e.g. 'Reference') are shortened
- Deform ON only for the bones the original mesh is skinned to (minus cloth joints) - what Automatic Weights should use
- cloth joints (C_*) blue, other non-deforming bones grey
"""
import json
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
glb, out, deform_file = args[:3]
deform = set(json.load(open(deform_file, encoding="utf-8")))

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb, disable_bone_shape=True)
for o in list(bpy.data.objects):          # leftovers of bone-shape import in some versions
    if o.type == "MESH" and not o.modifiers and not o.data.materials and len(o.data.vertices) < 100:
        bpy.data.objects.remove(o)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
mesh = next((o for o in bpy.data.objects if o.type == "MESH"), None)

arm.data.display_type = "OCTAHEDRAL"
arm.show_in_front = True

# shorten bones longer than 25% of the character's height (root/locators), keeping their direction
height = max(1e-3, max(b.head_local.z for b in arm.data.bones) - min(b.head_local.z for b in arm.data.bones))
bpy.context.view_layer.objects.active = arm
for o in bpy.context.selected_objects:
    o.select_set(False)
arm.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
short = 0
for eb in arm.data.edit_bones:
    if eb.length > 0.25 * height:
        eb.length = 0.05 * height; short += 1
bpy.ops.object.mode_set(mode="OBJECT")

nd = 0
for b in arm.data.bones:
    b.use_deform = b.name in deform
    nd += b.use_deform
    if b.name.startswith("C_"):
        b.color.palette = "THEME04"           # blue: cloth joints (driven by cloth physics)
    elif not b.use_deform:
        b.color.palette = "THEME13"           # grey: helpers/roots the original mesh doesn't use

bpy.ops.wm.save_as_mainfile(filepath=out)
print("MK11BL %d bones (%d set to Deform, cloth joints blue, unused grey), %d long root bones shortened" % (
    len(arm.data.bones), nd, short))
print("MK11BL saved %s" % out)
