"""UE3-style tagged properties as used by MK11.

Tag: Name(FName u32 idx + u32 number), Type(FName), i32 Size, i32 ArrayIndex, then
StructProperty: + struct FName (8); BoolProperty: + u32 value (Size 0). ByteProperty has NO enum FName in MK11.
A list ends with the Name 'None'.
"""
import struct


def read_props(pkg, d, o=0):
    N = pkg.names
    out = []
    while o + 8 <= len(d):
        ni = struct.unpack_from("<I", d, o)[0]
        if ni >= len(N):
            raise ValueError("bad property name index at %#x" % o)
        name = N[ni]; o += 8
        if name == "None":
            return o, out
        typ = N[struct.unpack_from("<I", d, o)[0]]; o += 8
        size, aidx = struct.unpack_from("<ii", d, o); o += 8
        extra = None
        if typ == "StructProperty":
            extra = N[struct.unpack_from("<I", d, o)[0]]; o += 8
        elif typ == "BoolProperty":
            extra = struct.unpack_from("<I", d, o)[0]; o += 4
        out.append(dict(name=name, type=typ, size=size, index=aidx, extra=extra, off=o, value=d[o:o + size]))
        o += size
    raise ValueError("property list not terminated")


def prop(props, name, index=0):
    return next((p for p in props if p["name"] == name and p["index"] == index), None)


def array_of_structs(pkg, value):
    """ArrayProperty whose elements are tagged structs: returns [(element_start, props)]."""
    n = struct.unpack_from("<i", value)[0]; o = 4; out = []
    for _ in range(n):
        start = o
        o, ps = read_props(pkg, value, o)
        out.append((start, ps))
    return out


def name_array(pkg, value):
    n = struct.unpack_from("<i", value)[0]
    return [pkg.names[struct.unpack_from("<I", value, 4 + 8 * k)[0]] for k in range(n)]
