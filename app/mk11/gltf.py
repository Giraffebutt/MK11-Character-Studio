"""Minimal glTF 2.0 binary (.glb) reader/writer for the converter and its previews.

Coordinate convention (shared with the reference export): glTF metres, +Y up  <->  MK11/UE centimetres, Z up,
left-handed. p_gltf = C @ p_game * 0.01, with C a mirror (det -1); hence p_game_row = p_gltf_row @ C / 0.01.
"""
import io
import json
import struct

import numpy as np

from .oodle import MK11Error

C = np.array([[0, -1, 0], [0, 0, 1], [1, 0, 0]], np.float64)
SCALE = 0.01
CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def to_game(p):
    return np.asarray(p, np.float64) @ C / SCALE


def dir_to_game(v):
    return np.asarray(v, np.float64) @ C


class GLB:
    def __init__(self, path):
        with open(path, "rb") as f:
            b = f.read()
        magic, ver, total = struct.unpack_from("<III", b)
        if magic != 0x46546C67 or ver != 2:
            raise MK11Error("%s is not a glTF 2.0 binary (.glb)" % path)
        jl, jt = struct.unpack_from("<II", b, 12)
        self.j = json.loads(b[20:20 + jl])
        o = 20 + jl
        self.bin = b""
        if o < len(b):
            bl, bt = struct.unpack_from("<II", b, o)
            self.bin = b[o + 8:o + 8 + bl]
        req = set(self.j.get("extensionsRequired", []))
        if req:
            raise MK11Error("unsupported glTF extensions required: %s (turn off Draco/compression on export)" % sorted(req))

    def accessor(self, i):
        a = self.j["accessors"][i]
        if "bufferView" not in a:
            return np.zeros((a["count"], NC[a["type"]]), CT[a["componentType"]])
        bv = self.j["bufferViews"][a["bufferView"]]
        dt = np.dtype(CT[a["componentType"]]); n = NC[a["type"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 0); isz = dt.itemsize * n
        if stride and stride != isz:
            raw = np.frombuffer(self.bin, np.uint8, stride * a["count"], off).reshape(a["count"], stride)[:, :isz]
            arr = raw.copy().view(dt).reshape(a["count"], n)
        else:
            arr = np.frombuffer(self.bin, dt, a["count"] * n, off).reshape(a["count"], n)
        if a.get("normalized"):
            arr = arr.astype(np.float32) / np.iinfo(dt).max
        return arr

    def node_world(self):
        nodes = self.j["nodes"]
        parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
        W = {}

        def local(n):
            if "matrix" in n:
                return np.array(n["matrix"], np.float64).reshape(4, 4).T
            x, y, z, w = n.get("rotation", [0, 0, 0, 1])
            R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            M = np.eye(4); M[:3, :3] = R * np.array(n.get("scale", [1, 1, 1])); M[:3, 3] = n.get("translation", [0, 0, 0])
            return M

        def get(i):
            if i not in W:
                W[i] = (get(parent[i]) if i in parent else np.eye(4)) @ local(nodes[i])
            return W[i]
        for i in range(len(nodes)):
            get(i)
        return W

    def image_bytes(self, image_index):
        im = self.j["images"][image_index]
        if "bufferView" not in im:
            raise MK11Error("only images embedded in the .glb are supported")
        bv = self.j["bufferViews"][im["bufferView"]]
        o = bv.get("byteOffset", 0)
        return self.bin[o:o + bv["byteLength"]]

    def material_image(self, material_index, slot="baseColorTexture"):
        """Embedded image of a material's base colour (or 'normalTexture') or None."""
        m = self.j["materials"][material_index]
        tex = (m.get("pbrMetallicRoughness", {}).get(slot) if slot == "baseColorTexture" else m.get(slot))
        if not tex:
            return None
        src = self.j["textures"][tex["index"]].get("source")
        if src is None:
            return None
        from PIL import Image
        return Image.open(io.BytesIO(self.image_bytes(src)))


class GLBWriter:
    def __init__(self):
        self.bin = bytearray()
        self.j = {"asset": {"version": "2.0", "generator": "MK11 Character Studio"}, "scene": 0,
                  "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "skins": [], "materials": [],
                  "accessors": [], "bufferViews": [], "buffers": [], "images": [], "textures": [], "samplers": []}

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin += data
        self.j["bufferViews"].append(bv)
        return len(self.j["bufferViews"]) - 1

    def acc(self, arr, ctype, typ, target=None, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = {"bufferView": self.view(arr.tobytes(), target), "componentType": ctype, "count": int(arr.shape[0]),
             "type": typ}
        if minmax:
            a["min"] = arr.min(0).tolist(); a["max"] = arr.max(0).tolist()
        self.j["accessors"].append(a)
        return len(self.j["accessors"]) - 1

    def image(self, png_bytes, name):
        self.j["images"].append({"bufferView": self.view(png_bytes), "mimeType": "image/png", "name": name})
        if not self.j["samplers"]:
            self.j["samplers"].append({})
        self.j["textures"].append({"source": len(self.j["images"]) - 1, "sampler": 0})
        return len(self.j["textures"]) - 1

    def save(self, path):
        for k in ("images", "textures", "samplers", "skins", "materials"):
            if not self.j[k]:
                del self.j[k]
        while len(self.bin) % 4:
            self.bin.append(0)
        self.j["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.j, separators=(",", ":")).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(self.bin)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942)); f.write(self.bin)
