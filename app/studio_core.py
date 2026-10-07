"""Shared operations for the MK11 Character Studio GUI and CLI. The game folder is only ever read."""
import datetime
import json
import os
import shutil
import struct

import numpy as np

from mk11.oodle import MK11Error
from mk11.package import Package
from mk11 import mesh as M, texture as T, convert as CV
from mk11.gltf import GLB, C, SCALE

APP = "MK11 Character Studio"
VERSION = "1.0.0"
MIN_PROJECT_STUDIO_VERSION = "1.0.0"       # oldest studio that can build the Mod Projects this version writes
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SETTINGS_FILE = os.path.join(HERE, "studio_settings.json")
TEX_PARAMS = ["ColorOverride", "Normal", "RMA", "Id", "Tone"]
TEX_LABELS = {"ColorOverride": "Colour (ColorOverride)", "Normal": "Normal map", "RMA": "Roughness/Metal/AO (RMA)",
              "Id": "Palette ID map", "Tone": "Tone map"}
FROM_MODEL, DEFAULT, KEEP = "(from model)", "(neutral default)", "(keep original)"
WORKSPACE_FOLDERS = ["converted", "exports", "mod_projects", "created_mods"]
EXPORT_NOTE_FILE = "READ ME - personal reference only.txt"
DO_NOT_REDISTRIBUTE_FILE = "DO_NOT_REDISTRIBUTE_GENERATED_GAME_FILES.txt"
DO_NOT_REDISTRIBUTE = """IMPORTANT

The .xxx, .psf, and other Mortal Kombat 11 package files in this
directory were generated locally from your own game installation.

Do not upload or redistribute these generated game package files.

They may contain original Mortal Kombat 11 game data.

To share this mod, distribute the original Mod Project instead:

- mod.json
- custom model
- custom textures
- creator-owned assets

Other users should use Create Mod with their own Mortal Kombat 11
installation to generate the required game files locally.
"""

# display names for the character codes in package names (unknown codes are shown as the code)
CHARACTER_NAMES = {
    "BAR": "Baraka", "CAS": "Cassie Cage", "CET": "Cetrion", "CYR": "Cyrax", "DVO": "D'Vorah", "ERR": "Erron Black",
    "FRO": "Frost", "FUJ": "Fujin", "JAC": "Jacqui Briggs", "JAD": "Jade", "JAX": "Jax", "JOH": "Johnny Cage",
    "JOK": "The Joker", "KAB": "Kabal", "KAN": "Kano", "KIT": "Kitana", "KOL": "Kollector", "KOT": "Kotal Kahn",
    "KUN": "Kung Lao", "LIU": "Liu Kang", "MIL": "Mileena", "NIT": "Nightwolf", "NOO": "Noob Saibot", "RAI": "Raiden",
    "RAM": "Rambo", "RAN": "Rain", "ROB": "RoboCop", "SCO": "Scorpion", "SEK": "Sektor", "SHA": "Shao Kahn",
    "SHE": "Sheeva", "SHT": "Shang Tsung", "SIN": "Sindel", "SKA": "Skarlet", "SON": "Sonya Blade", "SPA": "Spawn",
    "SUB": "Sub-Zero", "TRM": "The Terminator"}


def character_name(package):
    code = package.split("_")[1].upper() if package.count("_") >= 2 else ""
    return CHARACTER_NAMES.get(code, "")


# ------------------------------------------------------------------------------------------ settings / folders
def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def steam_libraries():
    """Steam library folders of this PC (from the Steam install in the registry and its libraryfolders.vdf)."""
    import re
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            roots.append(os.path.normpath(winreg.QueryValueEx(k, name)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    libs = []
    for r in roots:
        libs.append(r)
        try:
            with open(os.path.join(r, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
                libs += [os.path.normpath(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
    return list(dict.fromkeys(libs))


def detect_game_dir():
    cands = [os.path.join(l, "steamapps", "common", "Mortal Kombat 11") for l in steam_libraries()]
    return next((c for c in cands if os.path.isdir(os.path.join(c, "Asset"))), "")


def inside(path, parent):
    try:
        a, b = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(parent))
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def check_folders(workspace, game_dir):
    if not game_dir or not os.path.isdir(os.path.join(game_dir, "Asset")):
        raise MK11Error("Choose your Mortal Kombat 11 folder (the one containing Asset and Binaries).")
    if not workspace:
        raise MK11Error("Choose a mod folder (workspace) first.")
    if inside(workspace, game_dir) or inside(workspace, ROOT):
        raise MK11Error("The mod folder must be outside the game folder and outside the studio folder.")
    os.makedirs(workspace, exist_ok=True)
    for f in WORKSPACE_FOLDERS:
        os.makedirs(os.path.join(workspace, f), exist_ok=True)


# ------------------------------------------------------------------------------------------ game lookups
PACKAGE_KINDS = {"GEARASSETS": "character parts (bodies, heads, hair, gear, weapons)",
                 "CHAR": "gore / dismemberment pieces and props"}


def list_packages(game_dir, kinds=("GEARASSETS",)):
    """Character packages that stream their meshes from a .psf. kinds: GEARASSETS (convertible) and/or CHAR."""
    a = os.path.join(game_dir, "Asset")
    if not os.path.isdir(a):
        return []
    out = []
    for f in sorted(os.listdir(a), key=str.upper):
        up = f.upper()
        if not up.endswith("_SCRIPTASSETS.XXX") or not os.path.isfile(os.path.join(a, f[:-4] + ".psf")):
            continue
        if any(up.startswith(k + "_") for k in kinds) and up.count("_") == 2:
            out.append(f[:-4])
    return out


def package_label(package):
    kind = package.split("_")[0].upper()
    return "%s   -   %s" % (package, PACKAGE_KINDS.get(kind, ""))


def pkg_path(game_dir, package):
    return os.path.join(game_dir, "Asset", package + ".xxx"), os.path.join(game_dir, "Asset", package + ".psf")


# what each mesh is for (shown next to every mesh in the studio)
ROLES = [  # key, label, sort order
    ("body", "CHARACTER BODY (playable skin)"),
    ("face", "head / face"),
    ("eyes", "eyes & teeth"),
    ("hair", "hair"),
    ("outfit", "outfit part"),
    ("weapon", "weapon (part of the moveset)"),
    ("gear", "worn gear (headgear, bracers...)"),
    ("template", "animation template - invisible, drives the skeleton"),
    ("story", "story-mode version (one combined mesh, not used in fights)"),
    ("spare", "spare full body - not used by fight outfits"),
    ("gore", "GORE - dismemberment / fatality piece"),
    ("prop", "prop (intro / fatality / victory object)"),
    ("camera", "cinematic camera rig"),
    ("other", "other"),
]
ROLE_LABEL = dict(ROLES)
ROLE_ORDER = {k: i for i, (k, _) in enumerate(ROLES)}


def classify_meshes(p):
    """[{path, name, role, label, dlc}] for every streamed (exportable) skeletal mesh in a package. Roles come from
    the game's own data: outfit presets (CAP items), gear sets, presets' animation templates, and folder layout."""
    meshes = []
    for e in p.exports:
        if p.class_name(e["index"]) == "SkeletalMesh":
            info = M.mesh_info(p, e["index"])
            if info["lods"] and all(l["streamed"] for l in info["lods"]) and p.child(e["index"], "Skeleton"):
                meshes.append(e["index"])
    role = {}
    items = CV.cap_items(p)
    if items:
        templates = set()
        for e in p.exports:
            if p.class_name(e["index"]) == "CAPCharacterPreset":
                import mk11.props as Pp
                _, ps = Pp.read_props(p, p.export_data(e["index"]), 0)
                t = Pp.prop(ps, "TemplateMeshes")
                if t:
                    n = struct.unpack_from("<i", t["value"])[0]
                    templates.update(r for r in struct.unpack_from("<%di" % n, t["value"], 4) if r > 0)
        skin_sets = {a for a, _, _ in items if "SKIN" in a.upper()}
        gear_sets = sorted({a for a, _, _ in items if a not in skin_sets})
        weapons = CV.weapon_gear_assets(p, items, gear_sets)
        for a, _, ms in items:
            for m in ms:
                if m in role:
                    continue
                n = p.obj_name(m).upper()
                if a in skin_sets:
                    role[m] = ("body" if "_SKIN_" in n else "eyes" if "EYESMOUTH" in n else "face" if "_FACE_" in n
                               else "hair" if "_HAIR_" in n else "outfit")
                else:
                    role[m] = "weapon" if a in weapons else "gear"
        for t in templates:
            role.setdefault(t, "template")
    def gear_folder(path):      # e.g. LIU_GEAR_B for ...LIU_GEAR.LIU_GEAR_B.LIU_GEAR_B_001...
        parts = path.upper().split(".")
        return next((parts[i + 1] for i, s in enumerate(parts[:-1]) if s.endswith("_GEAR")), None)
    folder_role = {}            # unreferenced copies (e.g. DLC duplicates) inherit their gear folder's role
    for m, r in role.items():
        if r in ("weapon", "gear"):
            folder_role.setdefault(gear_folder(p.full_path(m)), r)
    out = []
    for m in meshes:
        path = p.full_path(m); name = p.obj_name(m); up = path.upper()
        r = role.get(m) or folder_role.get(gear_folder(path)) if gear_folder(path) else role.get(m)
        if r is None:
            r = ("gore" if ".DISM." in up else "prop" if "_PROPS." in up or ".PROPS." in up else
                 "camera" if "VCAM" in up else "story" if "STORY" in name.upper() else
                 "spare" if "_TEMPLATE." in up else "other")
        dlc = ".".join(path.split(".")[:2]) if path.startswith("DLC.") else ""
        out.append(dict(path=path, name=name, role=r, label=ROLE_LABEL[r], dlc=dlc))
    out.sort(key=lambda x: (ROLE_ORDER[x["role"]], x["name"], x["dlc"]))
    return out


def mesh_display(entry):
    return "%-24s %s  -  %s" % (entry["name"], ("[%s]" % entry["dlc"]) if entry["dlc"] else "", entry["label"])


def list_meshes(game_dir, package):
    """Meshes with their roles, character bodies first. Returns (entries, default_entry_or_None)."""
    entries = classify_meshes(Package(pkg_path(game_dir, package)[0]))
    code = package.split("_")[1] if "_" in package else ""
    default = next((e for e in entries if e["name"] == "%s_SKIN_A" % code and not e["dlc"]), entries[0] if entries else None)
    return entries, default


def outfit_groups(game_dir, package, mesh):
    """Hideable meshes of the character, grouped (outfit parts / gear sets / weapons) with suggested defaults."""
    p = Package(pkg_path(game_dir, package)[0])
    return CV.outfit_groups(p, p.find(mesh, "SkeletalMesh"))


def material_slots(game_dir, package, mesh):
    p = Package(pkg_path(game_dir, package)[0])
    mi = p.find(mesh, "SkeletalMesh")
    return [s[0] for s in CV.material_slots(p, M.mesh_info(p, mi))]


# ------------------------------------------------------------------------------------------ export original
def find_blender(settings=None):
    """blender.exe from settings, Steam, or the usual install folders ('' if not found)."""
    import glob
    cands = [(settings or {}).get("blender", "")]
    cands += [os.path.join(l, "steamapps", "common", "Blender", "blender.exe") for l in steam_libraries()]
    cands += sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"), reverse=True)
    return next((c for c in cands if c and os.path.isfile(c)), "")


def make_blend(blender, glb, blend, deform_bones, log=print):
    """Build a ready-to-use .blend from an exported .glb with the user's Blender (run in the background)."""
    import subprocess, tempfile
    script = os.path.join(HERE, "blender", "make_reference_blend.py")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(sorted(deform_bones), f); lst = f.name
    try:
        r = subprocess.run([blender, "-b", "--factory-startup", "--python", script, "--", glb, blend, lst],
                           capture_output=True, text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        os.remove(lst)
    report = [l[7:] for l in r.stdout.splitlines() if l.startswith("MK11BL ")]
    if r.returncode != 0 or not os.path.isfile(blend) or not any(l.startswith("saved") for l in report):
        tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
        raise MK11Error("Blender could not build the .blend:\n" + "\n".join(tail))
    for l in report:
        log("  blend: " + l)


EXPORT_NOTE = """For your own use only
====================

These files come from your own Mortal Kombat 11 game folder, so you can build your character on top of them.
They belong to Warner Bros. Games / NetherRealm Studios. Don't share or upload them.
"""


def export_original(workspace, game_dir, package, mesh, textures=False, blend=True, blender="", log=print):
    """Export one original mesh, as found in the user's local installation (base game or DLC alike), as a rigged
    Blender reference. Textures (PNG files and the colour texture inside the .glb) only when asked."""
    check_folders(workspace, game_dir)
    xxx, psf = pkg_path(game_dir, package)
    short = mesh.split(".")[-1]
    p = Package(xxx)
    mi = p.find(mesh, "SkeletalMesh")
    if "." not in mesh:                     # a bare name can repeat across DLC folders: prefer the base-game copy
        mi = next((e["index"] for e in p.exports if p.obj_name(e["index"]) == mesh and p.class_name(e["index"]) == "SkeletalMesh"
                   and not p.full_path(e["index"]).startswith("DLC.")), mi)
    info = M.mesh_info(p, mi)
    path = p.full_path(mi)
    if path.startswith("DLC."):
        short += "_" + path.split(".")[1]                 # keep same-named meshes from different DLC folders apart
    out = os.path.join(workspace, "exports", short)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, EXPORT_NOTE_FILE), "w", encoding="utf-8") as f:
        f.write(EXPORT_NOTE)
    entry = next((e for e in classify_meshes(p) if e["path"] == path), None)
    if entry:
        log("%s is: %s" % (entry["name"], entry["label"]))
    slots = CV.material_slots(p, info)
    color = None
    if textures and slots and slots[0][1]:
        color = CV.material_textures(p, slots[0][1]).get("ColorOverride")
    log("Exporting %s from %s" % (short, package))
    glb = os.path.join(out, short + ".glb")
    CV.write_preview(p, psf, path, glb, color, log, textures=textures)
    lines = ["%s: %s" % (short, entry["label"] if entry else ""), "",
             "Material slots (name your Blender materials like this, or slot00_ ... slot%02d_):" % (len(slots) - 1)]
    for i, (sname, mat) in enumerate(slots):
        lines.append("  slot%02d  %s" % (i, sname))
        if mat:
            for param, tex in sorted(CV.material_textures(p, mat).items()):
                lines.append("          %-36s %s" % (param, p.obj_name(tex)))
    if textures:
        with open(psf, "rb") as f:
            done = set()
            for sname, mat in slots:
                if not mat:
                    continue
                for param, tex in CV.material_textures(p, mat).items():
                    if param not in TEX_PARAMS or tex in done:
                        continue
                    done.add(tex)
                    t = T.read_texture(p, tex)
                    m = t["mips"][0]
                    im = T.decode(t["fmt"], m["w"], m["h"], T.read_mip(p, t, m, f), t["srgb"])
                    path = os.path.join(out, "%s.png" % t["name"])
                    im.save(path); log("  texture %s (%s) -> %s" % (param, sname, os.path.basename(path)))
    with open(os.path.join(out, "slots_and_textures.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    made_blend = False
    if blend:
        bl = blender or find_blender()
        if not bl:
            log("No Blender found, so no .blend was made (choose blender.exe in the studio to enable it).")
        else:
            # Deform = bones the original mesh is actually skinned to, minus cloth joints: what auto-weights should use.
            with open(psf, "rb") as f:
                lods = [M.read_lod(CV.read_lod_raw(p, l, f)[1]) for l in info["lods"]]
            lod = max(lods, key=lambda x: x["nverts"])
            names = M.read_skeleton(p, p.child(mi, "Skeleton"))["names"]
            used = {names[b] for b in np.unique(lod["bones"][lod["weights"] > 0])}
            deform = {b for b in used if not b.startswith("C_")}
            log("Building %s.blend with Blender..." % short)
            make_blend(bl, glb, os.path.join(out, short + ".blend"), deform, log)
            made_blend = True
    if made_blend:
        log("Done. Open %s.blend in Blender (or import %s.glb: File > Import > glTF, untick 'Bone Shape')." % (short, short))
    else:
        log("Done. Import %s.glb in Blender: File > Import > glTF, untick 'Bone Shape'." % short)
    return out


# ------------------------------------------------------------------------------------------ check model
def check_model(game_dir, package, mesh, model_path):
    """Compatibility report: list of (level, message), level in FAIL/WARN/PASS/INFO."""
    res = []
    r = lambda lvl, msg: res.append((lvl, msg))
    p = Package(pkg_path(game_dir, package)[0])
    mi = p.find(mesh, "SkeletalMesh")
    skel = M.read_skeleton(p, p.child(mi, "Skeleton"))
    names = skel["names"]; gw = {n: skel["world"][i] for i, n in enumerate(names)}
    nslots = len(CV.material_slots(p, M.mesh_info(p, mi)))
    try:
        g = GLB(model_path)
    except MK11Error as e:
        return [("FAIL", str(e))]
    j = g.j
    if not j.get("skins"):
        return [("FAIL", "no armature/skin in the file")]
    if j.get("animations"):
        r("WARN", "contains animations (ignored)")
    W = g.node_world()
    for si, skin in enumerate(j["skins"]):
        jn = [j["nodes"][k].get("name", "") for k in skin["joints"]]
        missing = [n for n in names if n not in jn]
        r("PASS" if not missing else "WARN", "armature: %d bones; %d of %s's %d bones present" % (len(jn), len(names) - len(missing), mesh, len(names)))
        if "inverseBindMatrices" in skin:
            ibm = g.accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
            dev = []
            for k, n in enumerate(jn):
                if n in gw:
                    Mw = gw[n].T
                    exp_t = C @ Mw[:3, 3] * SCALE
                    dev.append((np.linalg.norm(np.linalg.inv(ibm[k])[:3, 3] - exp_t) / SCALE, n))
            if dev:
                d, n = max(dev)
                r("PASS" if d < 0.5 else ("WARN" if d < 2 else "FAIL"), "bind pose vs original: max joint offset %.3f cm (%s)" % (d, n))
    total_v = 0; mats = set()
    for ni, node in enumerate(j["nodes"]):
        if "mesh" not in node:
            continue
        name = node.get("name", "mesh")
        if "skin" not in node:
            r("FAIL", "'%s' is not skinned to the armature" % name); continue
        jn = [j["nodes"][k].get("name", "") for k in j["skins"][node["skin"]]["joints"]]
        P, J, Wt, ntri = [], [], [], 0
        for pr in j["meshes"][node["mesh"]]["primitives"]:
            a = pr["attributes"]
            if "JOINTS_0" not in a:
                r("FAIL", "'%s' has a part without bone weights" % name); continue
            if "JOINTS_1" in a:
                r("FAIL", "'%s' has more than 4 weights per vertex (export with Bone Influences = 4)" % name)
            if not any(k.startswith("TEXCOORD_") for k in a):
                r("FAIL", "'%s' has no UV map" % name)
            P.append(g.accessor(a["POSITION"])); J.append(g.accessor(a["JOINTS_0"]).astype(int))
            Wt.append(g.accessor(a["WEIGHTS_0"]).astype(float))
            ntri += (len(g.accessor(pr["indices"])) if "indices" in pr else len(P[-1])) // 3
            mats.add(j["materials"][pr["material"]].get("name", "") if "material" in pr else "")
        if not P:
            continue
        P = np.concatenate(P); J = np.concatenate(J); Wt = np.concatenate(Wt)
        total_v += len(P)
        if not np.allclose(W[ni], np.eye(4), atol=1e-4):
            r("WARN", "'%s' has an object transform - apply transforms on the mesh in Blender" % name)
        # Blender's glTF exporter parks vertices that have no bone weights on a made-up joint called 'neutral_bone'
        nb = [k for k, n in enumerate(jn) if n == "neutral_bone"]
        on_nb = ((np.isin(J, nb)) & (Wt * 255 >= 0.5)).any(1) if nb else np.zeros(len(P), bool)
        zero = int((Wt.sum(1) < 1e-6).sum()) + int(on_nb.sum())
        r("PASS" if not zero else "FAIL", "'%s': %d vertices, %d triangles, %d without bone weights" % (name, len(P), ntri, zero))
        if on_nb.any():
            G = (P[on_nb].astype(float) @ C) / SCALE
            r("INFO", "  %d of them were exported on Blender's stand-in 'neutral_bone' (it means 'no weights', it's not "
                      "part of your rig). Usually small loose pieces Automatic Weights skipped - here around %.0f cm above "
                      "the ground. Fix: run 'Blender Tools\\blender_check_weights.py' (it weights them like the skin they "
                      "sit on) or assign them to a bone by hand." % (int(on_nb.sum()), np.median(G[:, 2])))
        used = {}
        for b, w in zip(J.ravel(), Wt.ravel()):
            if w * 255 >= 0.5 and jn[b] != "neutral_bone":
                used[jn[b]] = used.get(jn[b], 0) + 1
        notbody = sorted(n for n in used if n not in gw)
        cloth = sorted(n for n in used if n.startswith("C_"))
        root = sorted(n for n in used if n in CV.ROOT_BONES)
        unusual = sorted(n for n in used if n in CV.NRS_UNUSED)
        r("PASS" if not notbody else "FAIL", "'%s': bones not on the game body: %s" % (name, notbody[:10] or "none"))
        r("PASS" if not (root or cloth) else "FAIL", "'%s': root/cloth bones weighted: %s" % (name, (root + cloth)[:10] or "none"))
        if unusual:
            r("WARN", "'%s': weights on NRS-unused arm bones (should work): %s" % (name, unusual))
        if root or cloth or notbody:
            r("INFO", "fix: run 'Blender Tools\\blender_check_weights.py' in Blender, then re-export")
    if total_v:
        r("PASS" if total_v < 65536 else "FAIL", "total vertices %d (limit 65,535)" % total_v)
    r("PASS" if len(mats) <= nslots else "FAIL", "materials: %d (target has %d slots)" % (len(mats), nslots))
    if j.get("images"):
        r("INFO", "embedded textures: %d (the base colour image is used automatically)" % len(j["images"]))
    return res


def summary(res):
    nf = sum(l == "FAIL" for l, _ in res); nw = sum(l == "WARN" for l, _ in res)
    return ("COMPATIBLE" if nf == 0 else "NOT YET COMPATIBLE") + " (%d fail, %d warn)" % (nf, nw)


# ------------------------------------------------------------------------------------------ convert
INSTALL = """About this build
================

Asset\\ holds the modified game files (.xxx and .psf). Your game folder wasn't changed.
Open preview_*.glb in Blender to check how your character came out.

- The game normally refuses modified files ("Game data is corrupted"). This tool doesn't get around that.
- Play with modded files offline only. Modding may go against the game's terms; it's your call.
- Keep these files to yourself: they're mostly the game's own data. To share your mod, share its Mod Project
  instead (your model, textures and mod.json). Others build it with Create Mod from their own game.
"""


def convert_job(workspace, game_dir, package, mesh, model, name, textures=None, keep_textures=False,
                hide=None, log=print, defaults=None, author="", description="", make_project=True,
                subdir="converted"):
    """Convert into <workspace>\\<subdir>\\<name>\\ via a staging folder; earlier builds are kept.
    `hide`: full paths of meshes to hide (None = suggested defaults: outfit parts + worn gear, never weapons).
    `defaults`: texture defaults (None = config/texture_defaults.json).
    make_project: also write the shareable Mod Project (<workspace>\\mod_projects\\<name>\\) - the creator's own
    files + mod.json, never the finished game files. Create Mod calls this with make_project=False."""
    check_folders(workspace, game_dir)
    if not os.path.isfile(model) or not model.lower().endswith(".glb"):
        raise MK11Error("Choose your exported .glb file.")
    name = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "build"
    if subdir not in ("converted", "created_mods"):
        raise MK11Error("unknown output folder %s" % subdir)
    defaults = defaults if defaults is not None else CV.load_defaults()
    final = os.path.join(workspace, subdir, name)
    stage = final + ".building"
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    lines = []

    def tee(m):
        lines.append(str(m)); log(m)
    try:
        report = CV.convert(game_dir, package, mesh, model, stage, textures=textures, keep_textures=keep_textures,
                            hide=hide, log=tee, defaults=defaults)
    except Exception:
        with open(os.path.join(stage, "conversion_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        raise
    with open(os.path.join(stage, "conversion_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(stage, "INSTALL.txt"), "w", encoding="utf-8") as f:
        f.write(INSTALL)
    with open(os.path.join(stage, DO_NOT_REDISTRIBUTE_FILE), "w", encoding="utf-8") as f:
        f.write(DO_NOT_REDISTRIBUTE)
    if os.path.exists(final):
        old = final + ".old-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        os.rename(final, old); log("previous build kept as %s" % os.path.basename(old))
    os.rename(stage, final)
    log("Done: %s" % final)
    log("Generated game files are for your own installation only - don't share them (see %s)." % DO_NOT_REDISTRIBUTE_FILE)
    if make_project:
        import mod_project
        try:
            proj = mod_project.create_project(workspace, game_dir, package, mesh, model, name, textures, keep_textures,
                                              report["hidden_selection"], defaults, author, description, log)
            log("Mod Project for sharing (your files + mod.json, no game files): %s" % proj)
        except mod_project.ModProjectError as e:
            log("WARNING: Mod Project %s" % e)
    return final
