# Third-party components and credits

## Installed by the user (not bundled)

| Component | Used for | License |
|---|---|---|
| Python and Tcl/Tk | runtime, GUI | [Python license](https://docs.python.org/3/license.html), [Tcl/Tk license](https://www.tcl-lang.org/software/tcltk/license.html) |
| NumPy | mesh maths | [BSD-3-Clause and other permissive licenses](https://github.com/numpy/numpy/blob/main/LICENSE.txt) (see NumPy's license files) |
| Pillow | image reading/writing | [MIT-CMU](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| Blender (optional) | runs the two Blender scripts; started as a separate program | [GPL](https://www.blender.org/about/license/) |

`Setup Dependencies.cmd` installs NumPy and Pillow from PyPI into a local `.venv`, which is not part of this repository.

## Loaded from the user's own game installation (not bundled)

| File | What it is | Notes |
|---|---|---|
| `Binaries\Retail\oo2core_5_win64.dll` | Oodle compression library (RAD Game Tools / Epic Games), proprietary | Ships with the game; used to read and write the game's compressed package data. Not included here. |
| `Binaries\Retail\ispc_texcomp.dll` | Intel ISPC Texture Compressor (BC1/BC7 encoding) | Ships with the game. Intel publishes the [source under the MIT license](https://github.com/GameTechDev/ISPCTextureCompressor); the copy used is the game's. Not included here. |

The game's files, including these libraries, are covered by the game's own license terms. Whether using them outside
the game is permitted by those terms is your responsibility.

## Mod-loading tools mentioned in the documentation (not included)

The README and the generated `INSTALL.txt` mention two independent community projects that players commonly use to
make Mortal Kombat 11 load modified game files:

| Project | What it does | Where to get it |
|---|---|---|
| Ultimate ASI Loader (ThirteenAG) | Loads ASI plugins when a game starts | [github.com/ThirteenAG/Ultimate-ASI-Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) |
| ASIMK11 (thethiny) | Provides the MKSwap mod loader for MK11 | [github.com/thethiny/ASIMK11](https://github.com/thethiny/ASIMK11) |

- They are **not included** in this repository, and their binaries are not redistributed.
- **None of their code is used** in MK11 Character Studio, which doesn't install, configure, start or reproduce them.
- They have their own authors, licenses and terms. Users get them separately from the projects above.
- Their authors have not endorsed MK11 Character Studio, and MK11 Character Studio doesn't need or endorse ASIMK11's
  unlocker or cheat features.

## Credits and references

- **[MKX Character Studio](https://github.com/Giraffebutt/MKX-Character-Studio)** (PolyForm Noncommercial 1.0.0,
  same maintainer): the overall design, launcher scripts, GUI layout, documentation wording, and parts of the code
  (the .glb writer, texture mip handling, the command-line setup and package string reading) are adapted from it.
- **thethiny's `UPKFile.bt`** (010 Editor template in
  [Mortal-Kombat-11-Tools](https://github.com/thethiny/Mortal-Kombat-11-Tools), which publishes no license): read as
  a reference for the package header and compressed-segment layout. No template code was copied; `package.py` and
  `oodle.py` are an independent Python implementation of that layout.
- **glTF 2.0** (Khronos Group): the model exchange format, implemented from the public specification.
- **Tangent generation** (`app/mk11/convert.py`, `compute_tangents`) follows Eric Lengyel's published method for
  computing per-vertex tangent space, rewritten for NumPy.
- **Oodle interface** (`app/mk11/oodle.py`): the function signatures and option values used to call the game's
  Oodle library follow publicly documented community usage of that library.

## What this repository does not contain

No Mortal Kombat 11 game files, assets, executables, symbols, keys or decompiler output. The tool reads and writes the
game's file formats for compatibility; game data is read locally from the user's installation. Mortal Kombat and its
characters belong to their respective owners.

## Licensing of this project

- Everything except the files below: [PolyForm Noncommercial 1.0.0](LICENSE).
- `Blender Tools/blender_check_weights.py` and `app/blender/make_reference_blend.py`: GNU GPL v2 or later
  ([LICENSES/GPL-2.0.txt](LICENSES/GPL-2.0.txt)), because they use Blender's Python API.
