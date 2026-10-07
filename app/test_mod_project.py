"""Tests for Mod Projects and Create Mod. Synthetic tests always run (generated models, images and manifests; no game
files). Tests marked GameData read your own installation (read only) when MK11_GAME is set.

    python -m unittest -v test_mod_project
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_core as core                       # noqa: E402
import mod_project as MP                         # noqa: E402
from mk11.gltf import GLBWriter, C, SCALE        # noqa: E402
from mk11.oodle import use_game                  # noqa: E402

GAME = os.environ.get("MK11_GAME", "")
HAVE_GAME = bool(GAME) and os.path.isfile(os.path.join(GAME, "Asset", "GEARASSETS_LIU_ScriptAssets.xxx"))
if HAVE_GAME:
    use_game(GAME)


# ------------------------------------------------------------------------------------------- synthetic content
def put(path, data):
    with open(path, "wb") as f:
        f.write(data)


def put_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def get(path):
    with open(path, "rb") as f:
        return f.read()


def get_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def png_bytes(seed=0, size=64):
    from PIL import Image
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size, size, 4), dtype=np.uint8); arr[..., 3] = 255
    b = io.BytesIO(); Image.fromarray(arr, "RGBA").save(b, "PNG"); return b.getvalue()


def make_glb(path, positions=None, generator="Synthetic test", joints=("Hips", "Spine"), image=None):
    """A small skinned mesh (a cylinder at hip height) - stands in for a creator's own Blender model."""
    if positions is None:
        seg, rings = 16, 6
        a = np.linspace(0, 2 * np.pi, seg, endpoint=False)
        ys = np.linspace(0.95, 1.15, rings)
        positions = np.array([[0.12 * np.cos(t), y, 0.12 * np.sin(t)] for y in ys for t in a], np.float32)
        tris = []
        for r in range(rings - 1):
            for s in range(seg):
                i0, i1 = r * seg + s, r * seg + (s + 1) % seg
                tris += [[i0, i1 + seg, i1], [i0, i0 + seg, i1 + seg]]
        idx = np.array(tris, np.uint32).ravel()
    else:
        positions = np.asarray(positions, np.float32)
        idx = np.arange(len(positions) - len(positions) % 3, dtype=np.uint32)
    n = len(positions)
    nrm = positions.copy(); nrm[:, 1] = 0; nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-6)
    uv = np.stack([np.arctan2(positions[:, 2], positions[:, 0]) / (2 * np.pi) + 0.5,
                   (positions[:, 1] - positions[:, 1].min()) / max(1e-6, np.ptp(positions[:, 1]))], 1).astype(np.float32)
    jt = np.zeros((n, 4), np.uint16); jt[:, 1] = 1
    t = ((positions[:, 1] - positions[:, 1].min()) / max(1e-6, np.ptp(positions[:, 1]))).astype(np.float32)
    wt = np.zeros((n, 4), np.float32); wt[:, 0] = 1 - t; wt[:, 1] = t
    w = GLBWriter()
    w.j["asset"]["generator"] = generator
    for k, name in enumerate(joints):
        w.j["nodes"].append({"name": name, "translation": [0, 1.0 if k == 0 else 0.1, 0]})
        if k:
            w.j["nodes"][k - 1]["children"] = [k]
    w.j["skins"].append({"joints": list(range(len(joints)))})
    attrs = {"POSITION": w.acc(positions, 5126, "VEC3", 34962, True), "NORMAL": w.acc(nrm, 5126, "VEC3", 34962),
             "TEXCOORD_0": w.acc(uv, 5126, "VEC2", 34962), "JOINTS_0": w.acc(jt, 5123, "VEC4", 34962),
             "WEIGHTS_0": w.acc(wt, 5126, "VEC4", 34962)}
    mat = {"name": "slot00_custom", "pbrMetallicRoughness": {}}
    if image is not None:
        mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": w.image(image, "custom_base_colour")}
    w.j["materials"].append(mat)
    w.j["meshes"].append({"name": "MyCharacter", "primitives": [{"attributes": attrs, "material": 0,
                                                                  "indices": w.acc(idx, 5125, "SCALAR", 34963)}]})
    w.j["nodes"].append({"name": "MyCharacter", "mesh": 0, "skin": 0})
    w.j["scenes"][0]["nodes"] = [0, len(w.j["nodes"]) - 1]
    w.save(path)
    return path


def write_project(root, model_bytes=None, texture_bytes=None, **override):
    """A hand-made Mod Project folder (as a downloaded one would arrive)."""
    os.makedirs(os.path.join(root, "assets", "textures"), exist_ok=True)
    mp = os.path.join(root, "assets", "character.glb")
    if model_bytes is None:
        make_glb(mp)
    else:
        put(mp, model_bytes)
    tp = os.path.join(root, "assets", "textures", "body.png")
    put(tp, texture_bytes if texture_bytes is not None else png_bytes(1))
    files = {rel: {"sha256": MP.sha256(os.path.join(root, *rel.split("/"))),
                   "size": os.path.getsize(os.path.join(root, *rel.split("/")))}
             for rel in ("assets/character.glb", "assets/textures/body.png")}
    m = {"format_version": 1, "minimum_studio_version": "1.0.0", "created_with": "test", "name": "Test Mod",
         "author": "Tester", "description": "synthetic",
         "base": {"character": "LIU", "character_name": "Liu Kang", "package": "GEARASSETS_LIU_ScriptAssets",
                  "game_files": ["GEARASSETS_LIU_ScriptAssets.xxx", "GEARASSETS_LIU_ScriptAssets.psf"],
                  "target_mesh": "Disk.Characters.CHAR.LIU.LIU_SKIN.LIU_SKIN_A.Meshes.LIU_SKIN_A"},
         "model": "assets/character.glb",
         "textures": {"mode": "replace",
                      "assignments": {"Normal": {"source": "file", "path": "assets/textures/body.png"},
                                      "RMA": {"source": "solid", "rgba": [0, 0, 240, 0]}, "Tone": {"source": "keep"}},
                      "defaults": {"color_parameter": "ColorOverride",
                                   "parameters": {"ColorOverride": "solid:128,128,128,0"},
                                   "rules": {"Normal": {"flip_green": True, "alpha": 192}}}},
         "hidden_meshes": ["Disk.Characters.CHAR.LIU.LIU_HAIR.LIU_HAIR_D.Meshes.LIU_HAIR_D"],
         "output_name": "Test Mod", "files": files}
    for k, v in override.items():
        m[k] = v
    with open(os.path.join(root, "mod.json"), "w", encoding="utf-8") as f:
        json.dump(m, f)
    return m


def rewrite(root, m):
    with open(os.path.join(root, "mod.json"), "w", encoding="utf-8") as f:
        json.dump(m, f)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.root = os.path.join(self.tmp, "MyMod")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def assertRefused(self, contains=""):
        with self.assertRaises(MP.ModProjectError) as cm:
            MP.load_project(self.root)
        self.assertIn(contains, str(cm.exception))
        return str(cm.exception)

    def test_valid_project_loads(self):
        write_project(self.root)
        p = MP.load_project(os.path.join(self.root, "mod.json"))
        self.assertEqual(p.name, "Test Mod")
        self.assertEqual(p.texture_files(), ["assets/textures/body.png"])
        self.assertIn("Liu Kang", MP.summary(p))

    def test_unsupported_format_version(self):
        write_project(self.root, format_version=2)
        msg = self.assertRefused()
        self.assertEqual(msg, "This mod uses Mod Project format version 2.\n\nYour version of MK11 Character Studio "
                              "supports version 1.\n\nPlease update MK11 Character Studio.")

    def test_newer_studio_required(self):
        write_project(self.root, minimum_studio_version="9.0.0")
        self.assertRefused("requires MK11 Character Studio 9.0.0 or newer")

    def test_path_traversal_and_absolute_paths(self):
        bad = ["../character.glb", "assets/../../character.glb", "C:/Windows/character.glb", "/character.glb",
               "\\\\server\\share\\character.glb", "assets\\character.glb", "%TEMP%/character.glb", "~/character.glb",
               "https://example.com/character.glb", "assets//character.glb", "CON.glb", "assets/./character.glb",
               "assets/character.glb:stream"]
        for b in bad:
            m = write_project(self.root)
            m["model"] = b; m["files"] = {b: m["files"]["assets/character.glb"],
                                          "assets/textures/body.png": m["files"]["assets/textures/body.png"]}
            rewrite(self.root, m)
            with self.assertRaises(MP.ModProjectError, msg=b):
                MP.load_project(self.root)
        m = write_project(self.root)
        m["textures"]["assignments"]["Normal"]["path"] = "../../outside.png"
        rewrite(self.root, m)
        self.assertRefused("not allowed")

    def test_junction_escape(self):
        import _winapi
        outside = os.path.join(self.tmp, "outside"); os.makedirs(outside)
        make_glb(os.path.join(outside, "character.glb"))
        m = write_project(self.root)
        _winapi.CreateJunction(outside, os.path.join(self.root, "assets", "linked"))
        m["model"] = "assets/linked/character.glb"
        m["files"] = {"assets/linked/character.glb": {"sha256": MP.sha256(os.path.join(outside, "character.glb")),
                                                      "size": os.path.getsize(os.path.join(outside, "character.glb"))},
                      "assets/textures/body.png": m["files"]["assets/textures/body.png"]}
        rewrite(self.root, m)
        self.assertRefused("link or junction")

    def test_corrupted_assets(self):
        write_project(self.root)
        with open(os.path.join(self.root, "assets", "textures", "body.png"), "r+b") as f:
            f.seek(100); f.write(b"\x00\x01\x02")
        self.assertRefused("damaged or was changed")
        shutil.rmtree(self.root)
        write_project(self.root, model_bytes=b"glTF" + b"\x02\0\0\0" + b"\xff" * 64)     # broken .glb, correct hash
        self.assertRefused("not a valid .glb")
        shutil.rmtree(self.root)
        write_project(self.root, texture_bytes=b"\x89PNG\r\n\x1a\nbroken")
        self.assertRefused("not a readable image")

    def test_forbidden_files_refused(self):
        cases = {
            "assets/GEARASSETS_LIU_ScriptAssets.xxx": MP.PKG_TAG + b"\0" * 64,          # finished package
            "assets/hidden.png": MP.PKG_TAG + b"\0" * 64,                                # package renamed as image
            "notes.txt": b"hello " + MP.SEGMENT_SIG + b"\0" * 32,                        # package data inside text
            "tool.exe": b"MZ" + b"\0" * 100,
            "readme_too.txt": b"MZ" + b"\0" * 58 + struct.pack("<I", 64) + b"PE\0\0" + b"\0" * 32,  # program as .txt
            "assets/pack.png": b"PK\x03\x04" + b"\0" * 40,                               # zip renamed as image
            "assets/tex.dds": b"DDS " + b"\0" * 124,
            "install.ps1": b"Remove-Item C:\\ -Recurse",
            "plugin.py": b"import os",
        }
        for rel, data in cases.items():
            shutil.rmtree(self.root, ignore_errors=True)
            write_project(self.root)
            p = os.path.join(self.root, *rel.split("/"))
            put(p, data)
            with self.assertRaises(MP.ModProjectError, msg=rel) as cm:
                MP.load_project(self.root)
            self.assertIn("not allowed", str(cm.exception), rel)

    def test_text_starting_with_mz_is_fine(self):
        write_project(self.root)
        put_text(os.path.join(self.root, "notes.txt"), "MZ fan edition - credits")
        MP.load_project(self.root)

    def test_manifest_schema(self):
        tweaks = [lambda m: m.update(evil="x"),                                      # unknown setting
                  lambda m: m["base"].update(package="..\\..\\Windows\\notepad"),
                  lambda m: m["base"].update(target_mesh="Disk/../../x"),
                  lambda m: m["base"].update(game_files=["notepad.exe", "x.psf"]),
                  lambda m: m.update(output_name="..\\..\\Windows"),
                  lambda m: m["textures"]["assignments"].update(Normal={"source": "url", "path": "http://x/y.png"}),
                  lambda m: m["textures"]["assignments"].update(Shader={"source": "keep"}),
                  lambda m: m["textures"]["defaults"]["parameters"].update(Normal="file:C:/x.png"),
                  lambda m: m.update(hidden_meshes=["a", "a"]),
                  lambda m: m.update(name="x\x07"),
                  lambda m: m.update(minimum_studio_version=1),
                  lambda m: m.update(format_version="1"),
                  lambda m: m.update(base=["GEARASSETS_LIU_ScriptAssets"]),
                  lambda m: m.update(textures=None),
                  lambda m: m.update(files={})]
        for i, tw in enumerate(tweaks):
            m = write_project(self.root); tw(m); rewrite(self.root, m)
            with self.assertRaises(MP.ModProjectError, msg="tweak %d" % i):
                MP.load_project(self.root)
        write_project(self.root)
        txt = get_text(os.path.join(self.root, "mod.json"))
        put_text(os.path.join(self.root, "mod.json"), txt.replace('{"format_version": 1,', '{"format_version": 1, "format_version": 1,'))
        self.assertRefused("appears twice")

    def test_wrong_types_never_crash(self):
        """Every setting replaced by every wrong type: a clear ModProjectError (or a valid load), never a crash."""
        base = write_project(self.root)

        def paths(obj, pre=()):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield pre + (k,)
                    yield from paths(v, pre + (k,))
        for path in list(paths(base)):
            for bad in (None, 0, -1, 1.5, True, "", "x", [], ["x"], {}, {"x": 1}):
                m = json.loads(json.dumps(base)); o = m
                for k in path[:-1]:
                    o = o[k]
                o[path[-1]] = bad
                rewrite(self.root, m)
                try:
                    MP.load_project(self.root)
                except MP.ModProjectError:
                    pass
                except Exception as e:                     # pragma: no cover - this is the failure being tested
                    self.fail("%s = %r crashed: %r" % (".".join(path), bad, e))

    def test_missing_game_file_message(self):
        write_project(self.root)
        p = MP.load_project(self.root)
        game = os.path.join(self.tmp, "MK11"); os.makedirs(os.path.join(game, "Asset"))
        with self.assertRaises(MP.ModProjectError) as cm:
            MP.build_project(p, game, os.path.join(self.tmp, "ws"))
        self.assertEqual(str(cm.exception),
                         "Required Mortal Kombat 11 file not found:\n\nGEARASSETS_LIU_ScriptAssets.xxx\n"
                         "GEARASSETS_LIU_ScriptAssets.psf\n\nCreate Mod requires this file from your own Mortal Kombat 11 "
                         "installation and cannot provide missing game files.")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ws", "created_mods")))

    def test_dlc_package_is_an_ordinary_reference(self):
        m = write_project(self.root)
        m["base"] = {"character": "SPA", "character_name": "Spawn", "package": "GEARASSETS_SPA_ScriptAssets",
                     "game_files": ["GEARASSETS_SPA_ScriptAssets.xxx", "GEARASSETS_SPA_ScriptAssets.psf"],
                     "target_mesh": "DLC.Spawn.Characters.CHAR.SPA.SPA_SKIN.SPA_SKIN_A.Meshes.SPA_SKIN_A"}
        rewrite(self.root, m)
        p = MP.load_project(self.root)                      # no DLC-specific refusal
        game = os.path.join(self.tmp, "MK11"); os.makedirs(os.path.join(game, "Asset"))
        self.assertEqual([f for f, _, ok in MP.required_files(p, game)],
                         ["GEARASSETS_SPA_ScriptAssets.xxx", "GEARASSETS_SPA_ScriptAssets.psf"])
        with self.assertRaises(MP.ModProjectError) as cm:   # not installed -> simply reported missing
            MP.build_project(p, game, os.path.join(self.tmp, "ws"))
        self.assertIn("Required Mortal Kombat 11 file not found", str(cm.exception))

    def test_output_never_inside_project(self):
        write_project(self.root)
        p = MP.load_project(self.root)
        game = os.path.join(self.tmp, "MK11"); os.makedirs(os.path.join(game, "Asset"))
        for ext in (".xxx", ".psf"):
            put(os.path.join(game, "Asset", "GEARASSETS_LIU_ScriptAssets" + ext), b"x")
        with self.assertRaises(MP.ModProjectError) as cm:
            MP.build_project(p, game, self.root)
        self.assertIn("outside the Mod Project", str(cm.exception))

    def test_safe_names(self):
        self.assertEqual(MP.safe_name("My Model (v2)!.GLB"), "My Model (v2).glb")
        self.assertEqual(MP.safe_name("../../evil.png"), "evil.png")
        self.assertEqual(MP.check_rel_path("assets/textures/a b.png"), ["assets", "textures", "a b.png"])


@unittest.skipUnless(HAVE_GAME, "set MK11_GAME to run checks against your own installation")
class GameData(unittest.TestCase):
    PKG = "GEARASSETS_LIU_ScriptAssets"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.creator, self.recipient = os.path.join(self.tmp, "creator"), os.path.join(self.tmp, "recipient")
        self.assets = os.path.join(self.tmp, "my files"); os.makedirs(self.assets)
        self.quiet = lambda *a: None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def roundtrip(self, package, mesh, textures, keep=False):
        model = make_glb(os.path.join(self.assets, "my character.glb"), image=png_bytes(7))
        out_c = core.convert_job(self.creator, GAME, package, mesh, model, "My Mod", textures=textures, keep_textures=keep,
                                 log=self.quiet, author="Tester", description="test mod")
        proj = MP.project_dir(self.creator, "My Mod")
        files = sorted(MP.scan_project(proj))
        self.assertFalse([f for f in files if f.lower().endswith((".xxx", ".psf"))], files)
        for f in files:                                  # zero game packages / game data anywhere in the project
            self.assertFalse(MP.contains_game_data(os.path.join(proj, *f.split("/"))), f)
        p = MP.load_project(os.path.join(proj, "mod.json"))
        out_r = MP.build_project(p, GAME, self.recipient, log=self.quiet)
        for ext in (".xxx", ".psf"):                     # local generation of finished files is supported ...
            a, b = os.path.join(out_c, "Asset", package + ext), os.path.join(out_r, "Asset", package + ext)
            self.assertTrue(os.path.isfile(b))
            self.assertEqual(MP.sha256(a), MP.sha256(b), "rebuild differs: " + ext)   # ... and identical
        for out in (out_c, out_r):
            self.assertTrue(os.path.isfile(os.path.join(out, core.DO_NOT_REDISTRIBUTE_FILE)))
        return proj, files, p

    def test_roundtrip_identical_with_textures(self):
        tex = os.path.join(self.assets, "my normal.png"); put(tex, png_bytes(3, 256))
        proj, files, p = self.roundtrip(self.PKG, "LIU_SKIN_A", {"Normal": tex, "RMA": "solid:0,0,240,0", "Tone": "keep"})
        self.assertEqual(files, ["README.txt", "assets/my character.glb", "assets/textures/my normal.png", "mod.json"])
        a = p.m["textures"]["assignments"]
        self.assertEqual(a["Normal"], {"source": "file", "path": "assets/textures/my normal.png"})
        self.assertEqual(a["RMA"], {"source": "solid", "rgba": [0, 0, 240, 0]})
        self.assertEqual(a["Tone"], {"source": "keep"})
        self.assertEqual(p.m["base"]["target_mesh"], "Disk.Characters.CHAR.LIU.LIU_SKIN.LIU_SKIN_A.Meshes.LIU_SKIN_A")
        readme = get_text(os.path.join(proj, "README.txt"))
        self.assertIn("Do not redistribute the generated .xxx/.psf files.", readme)

    def test_dlc_character_roundtrip(self):
        dlc = "GEARASSETS_SPA_ScriptAssets"
        if not os.path.isfile(os.path.join(GAME, "Asset", dlc + ".xxx")):
            self.skipTest("Spawn package not in this installation")
        proj, files, p = self.roundtrip(dlc, "SPA_SKIN_A", None, keep=True)
        self.assertEqual(p.m["base"]["package"], dlc)
        self.assertTrue(p.m["base"]["target_mesh"].startswith("DLC.Spawn."))

    def test_original_game_data_refused(self):
        exp = core.export_original(self.creator, GAME, self.PKG, "LIU_SKIN_A", textures=True, blend=False, log=self.quiet)
        orig_glb = os.path.join(exp, "LIU_SKIN_A.glb")
        orig_png = next(os.path.join(exp, f) for f in os.listdir(exp) if f.endswith("_Color.png"))
        mine = make_glb(os.path.join(self.assets, "mine.glb"))
        defaults = core.CV.load_defaults()

        def refused(model, textures, needle):
            with self.assertRaises(MP.ModProjectError, msg=needle) as cm:
                MP.create_project(self.creator, GAME, self.PKG, "LIU_SKIN_A", model, "X", textures, False, [], defaults)
            self.assertIn(needle, str(cm.exception))
        refused(orig_glb, {}, "comes from the exports folder")
        copy_glb = os.path.join(self.assets, "renamed.glb"); shutil.copyfile(orig_glb, copy_glb)
        refused(copy_glb, {}, "written by MK11 Character Studio")
        # an original mesh re-exported by another tool: same vertex positions, different generator
        from mk11.package import Package
        from mk11 import mesh as M, convert as CV
        pk = Package(core.pkg_path(GAME, self.PKG)[0])
        info = M.mesh_info(pk, pk.find("LIU_FACE_A", "SkeletalMesh"))
        with open(core.pkg_path(GAME, self.PKG)[1], "rb") as f:
            pos = M.read_lod(CV.read_lod_raw(pk, info["lods"][0], f)[1])["pos"].astype(np.float64)
        reexport = make_glb(os.path.join(self.assets, "face.glb"), positions=(pos @ C.T) * SCALE, generator="Khronos glTF Blender I/O")
        refused(reexport, {}, "matches an original Mortal Kombat 11 mesh")
        same_name = os.path.join(self.assets, os.path.basename(orig_png)); shutil.copyfile(orig_png, same_name)
        refused(mine, {"ColorOverride": same_name}, "named like the original game texture")
        renamed = os.path.join(self.assets, "skin.png"); shutil.copyfile(orig_png, renamed)
        refused(mine, {"ColorOverride": renamed}, "looks like the original game texture")
        from PIL import Image
        disguised = os.path.join(self.assets, "my_paint.png")
        Image.open(orig_png).convert("RGB").resize((512, 512)).save(disguised)
        refused(mine, {"ColorOverride": disguised}, "looks like the original game texture")
        refused(mine, {"Normal": os.path.join(GAME, "Asset", self.PKG + ".xxx")}, "your Mortal Kombat 11 folder")
        # recipient side: a hand-made project carrying an original mesh is not built
        root = os.path.join(self.tmp, "bad project"); write_project(root, model_bytes=get(reexport))
        with self.assertRaises(MP.ModProjectError) as cm:
            MP.build_project(MP.load_project(root), GAME, self.recipient, log=self.quiet)
        self.assertIn("contains original Mortal Kombat 11 data", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.recipient, "created_mods", "Test Mod")))

    def test_custom_model_accepted(self):
        mine = make_glb(os.path.join(self.assets, "mine.glb"), image=png_bytes(9))
        tex = os.path.join(self.assets, "paint.png"); put(tex, png_bytes(11, 512))
        probs = MP.originality_problems(mine, [tex], MP.GameIndex(GAME, self.PKG))
        self.assertEqual(probs, [])


if __name__ == "__main__":
    unittest.main()
