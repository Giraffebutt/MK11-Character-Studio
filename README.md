# MK11 Character Studio

Put your own 3D character into Mortal Kombat 11 (PC). You make the model in Blender, and the studio turns it into a
replacement for a fighter's body (Liu Kang by default).

> **Unofficial fan-made tool**, not affiliated with Warner Bros. Games or NetherRealm Studios.
> No game files are included: you need your own copy of MK11.

## Setup

1. Install [Python](https://www.python.org/) 3.10 or newer.
2. Double-click **`Setup Dependencies.cmd`** (only needed once).
3. Double-click **`Launch Studio.cmd`**.

Tip: keep the folder somewhere short, like `D:\MK11 Character Studio`. Very long folder paths can make setup fail.

## Make a character

1. **Export tab:** export the character you want to replace, then open the `.blend` (or `.glb`) in Blender.
2. **Blender:** model your character over it in the same pose and size, and attach it to the armature without
   changing the bones. Run `Blender Tools\blender_check_weights.py` (Scripting tab > Open > Run Script), then
   Weights > Limit Total (4) and Weights > Normalize All.
   Export as **glTF Binary (.glb)** with: Selected Objects, Apply Modifiers on, Shape Keys off, Animations off,
   Bone Influences 4.
3. **Convert tab:** pick the same character and your `.glb`, click **Check model**, choose what to hide, then click
   **Convert**.
4. **Test it:** the game files are in `converted\<name>\Asset\` inside your mod folder. Open `preview_*.glb` in Blender
   to check the result first. To see it in the game, see [Loading mods in the game](#loading-mods-in-the-game).

By default the old face, hair and worn gear are hidden and weapons stay visible. Click a row in the list to change it.

## Share your mod

Every Convert also makes a **Mod Project** in `mod_projects\<name>\`. Zip that folder and share it.

Don't share the generated `.xxx` / `.psf` files: they contain the game's own data.

```
❌ DON'T SHARE
GEARASSETS_JOK_ScriptAssets.xxx
GEARASSETS_JOK_ScriptAssets.psf
exports and previews

✅ SHARE
MyJokerMod/
├── mod.json
├── README.txt
└── assets/
    ├── character.glb
    └── textures/
```

## Install someone else's mod

1. Unzip the mod.
2. Double-click **`Create Mod.cmd`** (or drag the mod's `mod.json` onto it).
3. Click **Create Mod**.

Create Mod builds the mod from *your* game files into `created_mods\<name>\Asset\`. Then load it as described in
[Loading mods in the game](#loading-mods-in-the-game). If a game file it needs is
missing, it tells you. It never downloads game files. It only accepts models, images and text files, checks them,
and never runs anything that comes with a mod.

## Loading mods in the game

MK11 Character Studio makes the modified game files, but Mortal Kombat 11 doesn't load modified files by itself.
The MK11 modding community usually uses two separate tools for that:

- [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) by ThirteenAG: loads ASI plugins when
  the game starts.
- [ASIMK11](https://github.com/thethiny/ASIMK11) by thethiny: provides **MKSwap**, the mod loader used by the MK11
  modding community.

These are independent projects. They aren't included with, made by or connected to MK11 Character Studio. For
installing, setting up and updating them, follow their own instructions.

Once MKSwap is set up, copy the files from your mod's `Asset\` folder into the MKSwap `Asset` folder, as ASIMK11's
instructions describe. To go back to the original character, remove them again.

- You only need ASIMK11's mod loader. Character Studio doesn't need, use or endorse its unlocker or cheat features,
  so leave those off.
- Play with modded files **offline only**.
- Neither Warner Bros. Games nor NetherRealm Studios supports these tools, and using them is at your own risk.

```
MK11 Character Studio                 Separate community tools
  makes modified .xxx/.psf files        Ultimate ASI Loader
  does NOT change the game's .exe         loads ASIMK11 when the game starts
  does NOT install or load plugins      ASIMK11 (MKSwap)
  does NOT turn off file checks           lets the game load the modified files
  does NOT unlock DLC or enable cheats
```

## Good to know

- Play with modded files **offline only**.
- The game normally rejects modified files. MK11 Character Studio doesn't change the game's program, turn off its
  file checks, or install any mod loader. Loading the files needs separate community tools: see
  [Loading mods in the game](#loading-mods-in-the-game).
- DLC characters you have installed work like any other character. The tool never unlocks or downloads DLC.
- Your model replaces an existing body and keeps its skeleton: up to 65,535 vertices and 4 bone weights per vertex.
- Colours look off? Edit `app\config\texture_defaults.json` and convert again.
- Exports and converted files are for your own use. Share Mod Projects instead.

## Small print

Mortal Kombat and its characters belong to their owners. Modding may go against the game's EULA and terms of
service, so use this tool at your own risk. It comes with no warranty. All of the code was written by an AI
(Anthropic's Claude) from the maintainer's instructions.

Credits: [THIRD_PARTY.md](THIRD_PARTY.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) ·
Command line: `app\studio_cli.py --help` · Tests: in `app`, run `python -m unittest test_studio test_mod_project`

## License

MK11 Character Studio is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE): you may use, modify
and redistribute it for noncommercial purposes. Only noncommercial use is licensed; see [LICENSE](LICENSE) for the
exact terms.

Exception: the two Blender scripts (`Blender Tools\blender_check_weights.py` and `app\blender\make_reference_blend.py`)
use Blender's Python API and are licensed under the GNU General Public License v2 or later
([LICENSES/GPL-2.0.txt](LICENSES/GPL-2.0.txt)), as marked in their headers.
