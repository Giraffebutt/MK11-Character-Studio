"""MK11 Character Studio - command line (same jobs as the GUI).

    python studio_cli.py check   MODEL.glb
    python studio_cli.py list                       (what can be hidden, and the defaults)
    python studio_cli.py convert MODEL.glb --workspace "C:\\...\\Custom MK11 Workspace" [--name NAME] [options]
    python studio_cli.py export  --workspace DIR [--package GEARASSETS_LIU_ScriptAssets] [--mesh LIU_SKIN_A] [--textures]
    python studio_cli.py create-mod PATH\\TO\\mod.json --workspace DIR      (build a shared Mod Project locally)

convert also writes a shareable Mod Project (<workspace>\\mod_projects\\<name>\\: your model, your textures and
mod.json - never game files). Share that, not the generated .xxx/.psf files.

Hiding: by default the face/eyes/hair worn with the body and worn gear are hidden; weapons are never hidden.
Change it with --show / --hide, each taking a mesh name (LIU_HAIR_D), a gear set (LIU_GearA), 'outfit',
or a full path from 'list'. Repeatable. --hide-nothing starts from an empty selection instead of the defaults.
"""
import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_core as core                     # noqa: E402
from mk11 import convert as CV                 # noqa: E402
from mk11.oodle import MK11Error               # noqa: E402

TEX = {"color": "ColorOverride", "normal": "Normal", "rma": "RMA", "id": "Id", "tone": "Tone"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default=core.detect_game_dir(), help="Mortal Kombat 11 folder (read only)")
    ap.add_argument("--package", default="GEARASSETS_LIU_ScriptAssets")
    ap.add_argument("--mesh", default="LIU_SKIN_A")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="compatibility check of a .glb"); c.add_argument("model")
    sub.add_parser("list", help="list hideable meshes and their defaults")
    sub.add_parser("meshes", help="list the package's meshes and what each is for")
    e = sub.add_parser("export", help="export an original mesh (+ textures) as a Blender reference")
    e.add_argument("--workspace", required=True)
    e.add_argument("--textures", action="store_true", help="also export its textures as PNG (for your own use only)")
    e.add_argument("--no-blend", action="store_true", help="don't build the ready-to-use .blend (needs Blender)")
    v = sub.add_parser("convert", help="convert a .glb into a replacement for the mesh")
    v.add_argument("model"); v.add_argument("--workspace", required=True); v.add_argument("--name")
    for k, p in TEX.items():
        v.add_argument("--" + k, metavar="IMAGE", help="%s: image file, solid:R,G,B,A or keep" % p)
    v.add_argument("--keep-textures", action="store_true")
    v.add_argument("--show", action="append", default=[], metavar="NAME", help="keep visible (mesh, gear set or 'outfit')")
    v.add_argument("--hide", action="append", default=[], metavar="NAME", help="hide (mesh, gear set or 'outfit')")
    v.add_argument("--hide-nothing", action="store_true", help="start from nothing hidden instead of the defaults")
    v.add_argument("--author", default="", help="author shown in the Mod Project")
    v.add_argument("--description", default="", help="description shown in the Mod Project")
    v.add_argument("--no-project", action="store_true", help="don't write the shareable Mod Project")
    cm = sub.add_parser("create-mod", help="build a Mod Project's game files locally from your own installation")
    cm.add_argument("project", help="the mod's mod.json (or its folder)"); cm.add_argument("--workspace", required=True)
    a = ap.parse_args()
    try:
        if a.cmd == "check":
            res = core.check_model(a.game, a.package, a.mesh, a.model)
            for lvl in ("FAIL", "WARN", "PASS", "INFO"):
                for l, m in res:
                    if l == lvl:
                        print("[%s] %s" % (l, m))
            print("RESULT:", core.summary(res))
            return 0 if core.summary(res).startswith("COMPATIBLE") else 1
        if a.cmd == "meshes":
            entries, _ = core.list_meshes(a.game, a.package)
            for e in entries:
                print(core.mesh_display(e))
            return 0
        if a.cmd == "list":
            for g in core.outfit_groups(a.game, a.package, a.mesh):
                print("%s  (%s, default %s)" % (g["name"], g["kind"], "hidden" if g["default_hidden"] else "visible"))
                for mid, lab in g["meshes"]:
                    print("    %s" % lab)
            return 0
        if a.cmd == "export":
            core.export_original(a.workspace, a.game, a.package, a.mesh, a.textures, blend=not a.no_blend)
            return 0
        if a.cmd == "create-mod":
            import mod_project as MP
            proj = MP.load_project(a.project)
            print(MP.summary(proj, a.game) + "\n")
            out = MP.build_project(proj, a.game, a.workspace)
            print("\n" + MP.REDISTRIBUTION_WARNING + "\n\nOutput folder: %s" % out)
            return 0
        groups = core.outfit_groups(a.game, a.package, a.mesh)
        if a.hide_nothing:
            groups = [dict(g, default_hidden=False) for g in groups]
        hide = CV.resolve_selection(groups, a.show, a.hide)
        tex = {TEX[k]: getattr(a, k) for k in TEX if getattr(a, k)}
        core.convert_job(a.workspace, a.game, a.package, a.mesh, a.model,
                         a.name or os.path.splitext(os.path.basename(a.model))[0], textures=tex,
                         keep_textures=a.keep_textures, hide=hide, author=a.author, description=a.description,
                         make_project=not a.no_project)
        return 0
    except MK11Error as ex:
        print("ERROR: %s" % ex, file=sys.stderr); return 1
    except Exception:
        traceback.print_exc(); return 2


if __name__ == "__main__":
    sys.exit(main())
