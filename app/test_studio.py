"""Tests. The default run is synthetic: no game files are needed or included.

    python -m unittest -v test_studio

Optional local checks against your own installation (read only; nothing from the game is stored in the repository):

    set MK11_GAME=<your Mortal Kombat 11 folder>
    python -m unittest -v test_studio
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mk11 import mesh as M, convert as CV, texture as T   # noqa: E402
from mk11.oodle import read_segment, build_segment, compress, decompress, use_game   # noqa: E402

GAME = os.environ.get("MK11_GAME", "")
PKG = os.path.join(GAME, "Asset", "GEARASSETS_LIU_ScriptAssets.xxx") if GAME else ""
HAVE_GAME = bool(GAME) and os.path.isfile(PKG)
if HAVE_GAME:
    use_game(GAME)


class Synthetic(unittest.TestCase):
    def test_influence_pack_roundtrip(self):
        rng = np.random.default_rng(1)
        local = rng.integers(0, 1024, (500, 4))
        w = rng.integers(0, 86, (500, 3)); w = np.concatenate([w, 255 - w.sum(1, keepdims=True)], 1).astype(np.uint8)
        l2, w2 = M.unpack_influences(M.pack_influences(local, w))
        self.assertTrue(np.array_equal(l2, local)); self.assertTrue(np.array_equal(w2, w))

    def test_tangent_pack_roundtrip(self):
        rng = np.random.default_rng(2)
        n = rng.normal(size=(300, 3)); n /= np.linalg.norm(n, axis=1, keepdims=True)
        t = np.cross(n, [0.3, 0.5, 0.8]); t /= np.linalg.norm(t, axis=1, keepdims=True)
        s = np.where(rng.random(300) < 0.5, -1.0, 1.0)
        n2, t2, s2 = M.unpack_tangent_basis(M.pack_tangent_basis(n, t, s))
        self.assertLess(np.abs(n2 - n).max(), 2.0 / 511); self.assertLess(np.abs(t2 - t).max(), 2.0 / 511)
        self.assertTrue(np.array_equal(s2, s))

    def test_quantize_weights(self):
        rng = np.random.default_rng(3)
        w = rng.random((1000, 4)); w[:, 2:] *= rng.random((1000, 1)) < 0.5
        q, order = CV.quantize_weights(w)
        self.assertTrue(np.all(q.astype(int).sum(1) == 255))
        self.assertTrue(np.all(np.diff(q.astype(int), axis=1) <= 0))       # descending

    @unittest.skipIf(HAVE_GAME, "a game folder is set")
    def test_no_bundled_dlls(self):
        """Without the user's game folder, the game's DLLs are not found anywhere else."""
        from mk11.oodle import MK11Error, game_dll_path
        with self.assertRaises(MK11Error):
            game_dll_path("oo2core_5_win64.dll")

    @unittest.skipUnless(HAVE_GAME, "set MK11_GAME: needs the game's oo2core DLL")
    def test_segment_roundtrip(self):
        import io
        raw = os.urandom(1000) * 300 + bytes(70000)
        self.assertEqual(decompress(compress(raw), len(raw)), raw)
        data, size = read_segment(io.BytesIO(build_segment(raw)), 0)
        self.assertEqual(data, raw)

    @unittest.skipUnless(HAVE_GAME, "set MK11_GAME: needs the game's ispc_texcomp DLL")
    def test_bc7_quality(self):
        y, x = np.mgrid[0:256, 0:256]
        img = np.stack([x, y, (x + y) // 2, np.full_like(x, 255)], -1).astype(np.uint8)
        dec = np.asarray(T.decode(22, 256, 256, T.encode("BC7", img))).astype(float)
        psnr = 10 * np.log10(255 ** 2 / ((dec[..., :3] - img[..., :3]) ** 2).mean())
        self.assertGreater(psnr, 40)


@unittest.skipUnless(HAVE_GAME, "set MK11_GAME to run checks against your own installation")
class GameData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mk11.package import Package
        cls.Package = Package
        cls.p = Package(PKG)
        cls.psf = open(PKG[:-4] + ".psf", "rb")

    @classmethod
    def tearDownClass(cls):
        cls.psf.close()

    def test_package_save_identical(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.xxx"); self.p.save(out, log=lambda *a: None)
            with open(out, "rb") as a, open(PKG, "rb") as b:
                self.assertEqual(a.read(), b.read())

    def test_lod_writer_byte_identical(self):
        """Every standard-layout LOD in Liu Kang's package must be reproduced byte-for-byte."""
        n = 0
        for e in self.p.exports:
            if self.p.class_name(e["index"]) != "SkeletalMesh":
                continue
            info = M.mesh_info(self.p, e["index"])
            for lod in info["lods"]:
                if not lod["streamed"]:
                    continue
                _, raw = CV.read_lod_raw(self.p, lod, self.psf)
                try:
                    L = M.read_lod(raw)
                except Exception:
                    continue
                cb = [(c["base"], c["nverts"], c["bonemap"].tolist()) for c in L["chunks"]]
                out = M.write_lod(L["sections"], L["indices"], L["pos"], L["normal"], L["tangent"], L["sign"], cb,
                                  L["local"], L["weights"].astype(np.uint8), L["uv"], len(L["required"]),
                                  L["colors"], L["extra"], L["active"].tolist())
                self.assertEqual(out, raw, self.p.obj_name(e["index"])); n += 1
        self.assertGreaterEqual(n, 70)

    def test_tangents_match_nrs(self):
        info = M.mesh_info(self.p, self.p.find("LIU_SKIN_A", "SkeletalMesh"))
        _, raw = CV.read_lod_raw(self.p, info["lods"][0], self.psf); L = M.read_lod(raw)
        n = L["normal"] / np.linalg.norm(L["normal"], axis=1, keepdims=True)
        t, s = CV.compute_tangents(L["pos"].astype(float), n, L["uv"][:, 0].astype(float), L["indices"].reshape(-1, 3))
        self.assertGreater(np.median((t * L["tangent"]).sum(1)), 0.999)
        self.assertGreater((s == L["sign"]).mean(), 0.998)

    def test_hide_keeps_vertices(self):
        info = M.mesh_info(self.p, self.p.find("LIU_FACE_A", "SkeletalMesh"))
        for lod in info["lods"]:
            _, raw = CV.read_lod_raw(self.p, lod, self.psf)
            a, b = M.read_lod(raw), M.read_lod(M.hide_lod(raw))
            self.assertEqual(a["nverts"], b["nverts"])
            self.assertTrue(all(s["ntri"] == 1 for s in b["sections"]))
            self.assertTrue(np.array_equal(a["pos"], b["pos"]))

    def test_hide_defaults(self):
        """Weapons are never hidden by default; face/eyes/hair of the body and worn gear are."""
        body = self.p.find("LIU_SKIN_A", "SkeletalMesh")
        groups = CV.outfit_groups(self.p, body)
        kinds = {g["name"]: g["kind"] for g in groups}
        self.assertEqual(kinds.get("LIU_GearB"), "weapon")
        hidden = CV.default_hidden(groups)
        names = {h.split(".")[-1] for h in hidden}
        self.assertFalse(any("GEAR_B" in n for n in names))
        self.assertTrue({"LIU_FACE_A", "LIU_FACE_A_EyesMouth", "LIU_HAIR_D"} <= names)
        self.assertTrue(any("GEAR_A" in n for n in names) and any("GEAR_C" in n for n in names))
        # user choices: keep hair, hide weapons, nothing-but-one
        sel = CV.resolve_selection(groups, show=["LIU_HAIR_D"], hide=["LIU_GearB"])
        sn = {h.split(".")[-1] for h in sel}
        self.assertNotIn("LIU_HAIR_D", sn); self.assertTrue(any("GEAR_B" in n for n in sn))
        none = [dict(g, default_hidden=False) for g in groups]
        self.assertEqual({h.split(".")[-1] for h in CV.resolve_selection(none, hide=["LIU_HAIR_D"])}, {"LIU_HAIR_D"})
        with self.assertRaises(Exception):
            CV.resolve_selection(groups, show=["NOT_A_MESH"])

    def test_export_joints_point_at_children(self):
        """Exported joints are aimed so Blender draws each bone toward its child (glTF has no bone tails)."""
        import json, struct as st
        from mk11.gltf import GLB
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.glb")
            CV.write_preview(self.p, PKG[:-4] + ".psf", "LIU_SKIN_A", out, None, log=lambda *a: None)
            g = GLB(out); W = g.node_world(); nodes = g.j["nodes"]
            ok = tot = 0
            for i, n in enumerate(nodes):
                kids = [c for c in n.get("children", []) if "mesh" not in nodes[c] and not nodes[c]["name"].startswith("C_")
                        and "helper" not in nodes[c]["name"].lower() and "Roll" not in nodes[c]["name"]]
                if len(kids) != 1:
                    continue
                v = W[kids[0]][:3, 3] - W[i][:3, 3]
                if np.linalg.norm(v) < 1e-4:
                    continue
                y = W[i][:3, 1] / np.linalg.norm(W[i][:3, 1]); tot += 1
                ok += float(np.dot(y, v / np.linalg.norm(v))) > 0.99
            self.assertGreater(tot, 30); self.assertEqual(ok, tot)

    def test_mesh_roles(self):
        import studio_core as core
        roles = {e["name"]: e["role"] for e in core.classify_meshes(self.p) if not e["dlc"]}
        self.assertEqual(roles["LIU_SKIN_A"], "body"); self.assertEqual(roles["LIU_FACE_A"], "face")
        self.assertEqual(roles["LIU_HAIR_D"], "hair"); self.assertEqual(roles["LIU_FACE_A_EyesMouth"], "eyes")
        self.assertEqual(roles["LIU_GEAR_B_001"], "weapon"); self.assertEqual(roles["LIU_GEAR_A_001"], "gear")
        self.assertEqual(roles["LIUrv_Story"], "story")
        gore = core.classify_meshes(self.Package(os.path.join(GAME, "Asset", "CHAR_LIU_ScriptAssets.xxx")))
        self.assertTrue(gore and all(e["role"] == "gore" for e in gore))

    def test_export_scope(self):
        """Export: no textures anywhere (PNG or inside the .glb) unless asked; a personal-use note; DLC meshes present
        in the installation export exactly like base-game meshes."""
        import studio_core as core
        from mk11.gltf import GLB
        quiet = lambda *a: None
        pkg = "GEARASSETS_LIU_ScriptAssets"
        with tempfile.TemporaryDirectory() as ws:
            out = core.export_original(ws, GAME, pkg, "LIU_SKIN_A", blend=False, log=quiet)
            files = os.listdir(out)
            self.assertFalse(any(f.lower().endswith(".png") for f in files), files)
            self.assertIn("READ ME - personal reference only.txt", files)
            self.assertFalse(GLB(os.path.join(out, "LIU_SKIN_A.glb")).j.get("images"), "textures embedded although not requested")
            out = core.export_original(ws, GAME, pkg, "LIU_SKIN_A", textures=True, blend=False, log=quiet)
            self.assertTrue(GLB(os.path.join(out, "LIU_SKIN_A.glb")).j.get("images"))
            self.assertTrue(any(f.lower().endswith(".png") for f in os.listdir(out)))
            entries, _ = core.list_meshes(GAME, pkg)
            dlc = next(e for e in entries if e["dlc"])
            out = core.export_original(ws, GAME, pkg, dlc["path"], blend=False, log=quiet)
            self.assertTrue(os.path.basename(out).startswith(dlc["name"] + "_"))
            self.assertTrue(os.path.isfile(os.path.join(out, os.path.basename(out) + ".glb")))

    def test_outfit_lookup(self):
        items = CV.cap_items(self.p)
        body = self.p.find("LIU_SKIN_A", "SkeletalMesh")
        names = {self.p.obj_name(m) for _, _, ms in items if body in ms for m in ms}
        self.assertTrue({"LIU_FACE_A", "LIU_FACE_A_EyesMouth", "LIU_HAIR_D"} <= names)


if __name__ == "__main__":
    unittest.main()
