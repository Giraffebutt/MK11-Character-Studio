#!/usr/bin/env python3
"""
MK11 Character Studio - desktop front-end.

  1. Export an original Mortal Kombat 11 character mesh to .glb (+ textures) as a Blender reference.
  2. Check and convert your own rigged .glb (+ textures) into a replacement for that mesh.

Results are written to your chosen mod folder (the studio's own settings stay in its app folder);
the game folder is only ever read.
Double-click "Launch Studio.cmd".
"""
import os
import queue
import subprocess
import sys
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import studio_core as core                       # noqa: E402
from mk11.oodle import MK11Error                 # noqa: E402

ABOUT = ("MK11 Character Studio is an unofficial fan-made tool, not affiliated with Warner Bros. Games or "
         "NetherRealm Studios. Mortal Kombat and its characters belong to their owners.\n\n"
         "No game files are included. It reads your own game folder and writes to your mod folder. "
         "It never unlocks DLC.\n\n"
         "Share Mod Projects, not the generated game files. Play modded files offline. "
         "Modding may go against the game's terms, so use it at your own risk.\n\n"
         "Written by an AI from the maintainer's instructions.\n"
         "License: PolyForm Noncommercial 1.0.0 (Blender scripts: GPL-2.0-or-later).")


def run_gui(selftest=False, tab=0, smoke=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText

    class Studio(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(core.APP + " (unofficial community tool)")
            self.geometry("1120x900"); self.minsize(980, 820)
            self.protocol("WM_DELETE_WINDOW", self.close)
            self.q = queue.Queue(); self.busy = False; self.buttons = []
            self.s = core.load_settings()
            self.ws = tk.StringVar(value=self.s.get("workspace", os.path.join(os.path.expanduser("~"), "Downloads", "Custom MK11 Workspace")))
            self.game = tk.StringVar(value=self.s.get("game_dir") or core.detect_game_dir())
            self.blender = tk.StringVar(value=core.find_blender(self.s))
            self.mesh_entries = {}            # combobox widget name -> {display text: mesh entry}
            self.last_result = None
            self.build()
            self.after(100, self.poll)
            self.after(200, self.refresh_packages)

        # ---------------------------------------------------------------- layout
        def build(self):
            pad = dict(padx=6, pady=4)
            mb = tk.Menu(self); hm = tk.Menu(mb, tearoff=0)
            hm.add_command(label="How to use", command=self.show_help)
            hm.add_command(label="About", command=lambda: messagebox.showinfo("About", ABOUT))
            mb.add_cascade(label="Help", menu=hm); self.config(menu=mb)

            top = ttk.LabelFrame(self, text="Folders"); top.pack(fill="x", **pad)
            ttk.Label(top, text="Mod folder (your work + results):").grid(row=0, column=0, sticky="w", **pad)
            ttk.Entry(top, textvariable=self.ws, state="readonly").grid(row=0, column=1, sticky="ew", **pad)
            self.btn(top, "Choose...", self.choose_ws).grid(row=0, column=2, **pad)
            self.btn(top, "Open", lambda: self.open_dir(self.ws.get())).grid(row=0, column=3, **pad)
            ttk.Label(top, text="Mortal Kombat 11 folder (read only):").grid(row=1, column=0, sticky="w", **pad)
            ttk.Entry(top, textvariable=self.game, state="readonly").grid(row=1, column=1, sticky="ew", **pad)
            self.btn(top, "Choose...", self.choose_game).grid(row=1, column=2, **pad)
            self.btn(top, "Blender Tools", lambda: self.open_dir(os.path.join(core.ROOT, "Blender Tools"))).grid(row=1, column=3, **pad)
            ttk.Label(top, text="Blender (optional, for .blend export):").grid(row=2, column=0, sticky="w", **pad)
            ttk.Entry(top, textvariable=self.blender, state="readonly").grid(row=2, column=1, sticky="ew", **pad)
            self.btn(top, "Choose...", self.choose_blender).grid(row=2, column=2, **pad)
            top.columnconfigure(1, weight=1)
            ttk.Label(self, text="Replaces one character body mesh (default: Liu Kang's LIU_SKIN_A) with your model; you choose which "
                      "original meshes (face, hair, gear...) to hide. Your model must be rigged to that character's skeleton.", wraplength=1060).pack(fill="x", padx=12, pady=2)

            nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, **pad); self.nb = nb
            self.build_export(nb); self.build_convert(nb)

            bottom = ttk.Frame(self); bottom.pack(fill="both", **pad)
            self.prog = ttk.Progressbar(bottom, mode="indeterminate"); self.prog.pack(fill="x")
            self.logbox = ScrolledText(bottom, height=11, state="disabled", font=("Consolas", 9)); self.logbox.pack(fill="both", expand=True)
            for tag, col in (("FAIL", "#c0392b"), ("WARN", "#b9770e"), ("PASS", "#1e8449"), ("HEAD", "#1f4e79")):
                self.logbox.tag_config(tag, foreground=col)

        def btn(self, parent, text, cmd):
            b = ttk.Button(parent, text=text, command=cmd); self.buttons.append(b); return b

        def build_export(self, nb):
            f = ttk.Frame(nb); nb.add(f, text="  1. Export original character  ")
            pad = dict(padx=8, pady=6)
            ttk.Label(f, text="Exports an original mesh as a rigged .glb (and a ready-to-use .blend) into <mod folder>\\exports\\ - import it into\n"
                              "Blender as the reference to build and rig your character on. For your own use only: don't share exported files."
                              ).grid(row=0, column=0, columnspan=4, sticky="w", **pad)
            ttk.Label(f, text="Character package:").grid(row=1, column=0, sticky="w", **pad)
            self.e_pkg = tk.StringVar()
            self.e_pkg_cb = ttk.Combobox(f, textvariable=self.e_pkg, state="readonly", width=44)
            self.e_pkg_cb.grid(row=1, column=1, sticky="w", **pad)
            self.e_pkg_cb.bind("<<ComboboxSelected>>", lambda e: (self.e_pkg_kind.set(core.package_label(self.e_pkg.get()).split("-", 1)[-1].strip()),
                                                                self.load_meshes(self.e_pkg.get(), self.e_mesh_cb, self.e_mesh, label=self.e_role)))
            self.e_pkg_kind = tk.StringVar()
            ttk.Label(f, textvariable=self.e_pkg_kind, foreground="#555").grid(row=1, column=2, columnspan=2, sticky="w", **pad)
            ttk.Label(f, text="Mesh:").grid(row=2, column=0, sticky="w", **pad)
            self.e_mesh = tk.StringVar()
            self.e_mesh_cb = ttk.Combobox(f, textvariable=self.e_mesh, state="readonly", width=90, height=25)
            self.e_mesh_cb.grid(row=2, column=1, columnspan=3, sticky="w", **pad)
            self.e_role = tk.StringVar()
            self.e_mesh_cb.bind("<<ComboboxSelected>>", lambda e: self.show_role(self.e_mesh_cb, self.e_mesh, self.e_role))
            ttk.Label(f, textvariable=self.e_role, foreground="#1f4e79", wraplength=900).grid(row=3, column=1, columnspan=3, sticky="w", padx=8)
            self.e_tex = tk.BooleanVar(value=False); self.e_blend = tk.BooleanVar(value=True)
            ttk.Checkbutton(f, text="Also export its textures (PNG files + colour texture in the .glb) - only if you need them to paint over",
                            variable=self.e_tex).grid(row=4, column=1, sticky="w", **pad)
            ttk.Checkbutton(f, text="Also build a ready-to-use .blend (bones point at their children, no big spheres, only usable bones set to Deform)",
                            variable=self.e_blend).grid(row=5, column=1, columnspan=3, sticky="w", **pad)
            self.btn(f, "Export", self.do_export).grid(row=6, column=1, sticky="w", **pad)
            self.btn(f, "Open exports folder", lambda: self.open_dir(os.path.join(self.ws.get(), "exports"))).grid(row=6, column=2, sticky="w", **pad)
            ttk.Label(f, foreground="#555", text="Mesh roles come from the game's own data: outfit presets, gear sets, animation templates and folders. "
                      "Bodies are what you replace; gore pieces live in the CHAR_ packages.", wraplength=1000).grid(row=7, column=0, columnspan=4, sticky="w", **pad)

        def build_convert(self, nb):
            f = ttk.Frame(nb); nb.add(f, text="  2. Convert custom character  ")
            pad = dict(padx=8, pady=5)
            ttk.Label(f, text="Base character:").grid(row=0, column=0, sticky="w", **pad)
            self.c_pkg = tk.StringVar()
            self.c_pkg_cb = ttk.Combobox(f, textvariable=self.c_pkg, state="readonly", width=40)
            self.c_pkg_cb.grid(row=0, column=1, sticky="w", **pad)
            self.c_pkg_cb.bind("<<ComboboxSelected>>", lambda e: self.load_meshes(self.c_pkg.get(), self.c_mesh_cb, self.c_mesh, then=self.load_target, label=self.c_role))
            ttk.Label(f, text="Mesh to replace:").grid(row=0, column=2, sticky="e", **pad)
            self.c_mesh = tk.StringVar()
            self.c_mesh_cb = ttk.Combobox(f, textvariable=self.c_mesh, state="readonly", width=58, height=25)
            self.c_mesh_cb.grid(row=0, column=3, sticky="w", **pad)
            self.c_role = tk.StringVar()
            self.c_mesh_cb.bind("<<ComboboxSelected>>", lambda e: (self.show_role(self.c_mesh_cb, self.c_mesh, self.c_role), self.load_target()))

            ttk.Label(f, textvariable=self.c_role, foreground="#1f4e79").grid(row=1, column=0, columnspan=4, sticky="w", padx=8)
            ttk.Label(f, text="Your character (.glb):").grid(row=2, column=0, sticky="w", **pad)
            self.c_model = tk.StringVar(value=self.s.get("model", ""))
            ttk.Entry(f, textvariable=self.c_model, width=86).grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
            mf = ttk.Frame(f); mf.grid(row=2, column=3, sticky="w", **pad)
            self.btn(mf, "Browse...", self.browse_model).pack(side="left")
            self.btn(mf, "Check model", self.do_check).pack(side="left", padx=6)

            tp = ttk.LabelFrame(f, text="Textures (what replaces each texture of the target's material)")
            tp.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=6)
            self.tex = {}
            for i, param in enumerate(core.TEX_PARAMS):
                ttk.Label(tp, text=core.TEX_LABELS[param] + ":").grid(row=i, column=0, sticky="w", padx=8, pady=3)
                choices = ([core.FROM_MODEL] if param == "ColorOverride" else []) + [core.DEFAULT, core.KEEP]
                v = tk.StringVar(value=choices[0])
                cb = ttk.Combobox(tp, textvariable=v, values=choices, width=78)
                cb.grid(row=i, column=1, sticky="ew", padx=8, pady=3)
                self.btn(tp, "Image...", lambda p=param: self.browse_tex(p)).grid(row=i, column=2, padx=8, pady=3)
                self.tex[param] = (v, cb, choices)
            ttk.Label(tp, foreground="#555", text="'from model' = the base-colour image embedded in your .glb.  'neutral default' = values in "
                      "app\\config\\texture_defaults.json.  You can also type solid:R,G,B,A").grid(row=len(core.TEX_PARAMS), column=0, columnspan=3, sticky="w", padx=8, pady=3)
            tp.columnconfigure(1, weight=1)

            op = ttk.LabelFrame(f, text="Hide original meshes (click a row to switch it; click a group to switch all of it)")
            op.grid(row=4, column=0, columnspan=4, sticky="nsew", padx=8, pady=6)
            self.tree = ttk.Treeview(op, columns=("state", "kind"), height=7, selectmode="none")
            self.tree.heading("#0", text="Mesh"); self.tree.heading("state", text="In game"); self.tree.heading("kind", text="Type")
            self.tree.column("#0", width=430); self.tree.column("state", width=150, anchor="center"); self.tree.column("kind", width=260)
            sb = ttk.Scrollbar(op, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set)
            self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=4); sb.grid(row=0, column=1, sticky="ns", pady=4)
            self.tree.tag_configure("hidden", foreground="#a33"); self.tree.tag_configure("shown", foreground="#1e6b35")
            self.tree.bind("<Button-1>", self.tree_click)
            bf = ttk.Frame(op); bf.grid(row=0, column=2, sticky="n", padx=8, pady=4)
            for text, fn in (("Defaults", self.hide_defaults), ("Hide all", lambda: self.hide_set(True)),
                             ("Show all", lambda: self.hide_set(False))):
                self.btn(bf, text, fn).pack(fill="x", pady=2)
            ttk.Label(bf, foreground="#555", wraplength=150, text="Defaults: weapons stay visible; the face/hair worn "
                      "with the body and worn gear are hidden.").pack(fill="x", pady=6)
            op.columnconfigure(0, weight=1)
            self.groups, self.hidden, self.row_mesh = [], set(), {}

            of = ttk.Frame(f); of.grid(row=5, column=0, columnspan=4, sticky="ew", padx=8, pady=8)
            ttk.Label(of, text="Output name:").pack(side="left")
            self.c_name = tk.StringVar(value=self.s.get("name", ""))
            ttk.Entry(of, textvariable=self.c_name, width=30).pack(side="left", padx=6)
            self.btn(of, "Convert", self.do_convert).pack(side="left", padx=12)
            self.btn(of, "Open result folder", lambda: self.open_dir(self.last_result or os.path.join(self.ws.get(), "converted"))).pack(side="left")
            ttk.Label(of, foreground="#555", text="  Results: <mod folder>\\converted\\<name>\\Asset\\").pack(side="left")
            mp = ttk.LabelFrame(f, text="Sharing: each Convert also makes a Mod Project. Share that, not the game files")
            mp.grid(row=6, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 6))
            ttk.Label(mp, text="Author:").pack(side="left", padx=(8, 2))
            self.c_author = tk.StringVar(value=self.s.get("author", ""))
            ttk.Entry(mp, textvariable=self.c_author, width=20).pack(side="left", padx=4)
            ttk.Label(mp, text="Description:").pack(side="left", padx=(8, 2))
            self.c_desc = tk.StringVar(value=self.s.get("description", ""))
            ttk.Entry(mp, textvariable=self.c_desc, width=46).pack(side="left", padx=4, fill="x", expand=True)
            self.btn(mp, "Open Mod Projects", lambda: self.open_dir(os.path.join(self.ws.get(), "mod_projects"))).pack(side="left", padx=8, pady=4)
            f.columnconfigure(1, weight=1); f.rowconfigure(4, weight=1)

        # ---------------------------------------------------------------- data
        def refresh_packages(self):
            pk = core.list_packages(self.game.get())                              # convertible (character parts)
            self.e_pkg_cb["values"] = core.list_packages(self.game.get(), ("GEARASSETS", "CHAR"))
            self.c_pkg_cb["values"] = pk
            want = self.s.get("package") if self.s.get("package") in pk else next((p for p in pk if "_LIU_" in p), pk[0] if pk else "")
            if want:
                self.e_pkg.set(want); self.c_pkg.set(want)
                self.e_pkg_kind.set(core.package_label(want).split("-", 1)[-1].strip())
                self.load_meshes(want, self.e_mesh_cb, self.e_mesh, label=self.e_role)
                self.load_meshes(want, self.c_mesh_cb, self.c_mesh, then=self.load_target, prefer=self.s.get("mesh"), label=self.c_role)
            elif not self.game.get():
                self.log("Choose your Mortal Kombat 11 folder to begin.", "WARN")

        def load_meshes(self, package, cb, var, then=None, prefer=None, label=None):
            key = str(cb)
            self._mesh_req = reqs = getattr(self, "_mesh_req", {})
            reqs[key] = req = reqs.get(key, 0) + 1                             # ignore results of older requests

            def work():
                entries, default = core.list_meshes(self.game.get(), package)
                pick = next((e for e in entries if prefer and prefer in (e["path"], e["name"])), default)
                return entries, pick

            def done(res):
                if req != reqs.get(key):
                    return
                entries, sel = res
                disp = {core.mesh_display(e): e for e in entries}
                self.mesh_entries[str(cb)] = disp
                cb["values"] = list(disp)
                var.set(core.mesh_display(sel) if sel else "")
                if label is not None:
                    self.show_role(cb, var, label)
                if then:
                    then()
            self.run(work, done, quiet=True)

        def mesh_entry(self, cb, var):
            return self.mesh_entries.get(str(cb), {}).get(var.get())

        def mesh_path(self, cb, var):
            e = self.mesh_entry(cb, var)
            return e["path"] if e else var.get()

        def show_role(self, cb, var, label):
            e = self.mesh_entry(cb, var)
            if not e:
                return label.set("")
            text = "%s is: %s." % (e["name"], e["label"])
            if cb is getattr(self, "c_mesh_cb", None) and e["role"] != "body":
                text += "  Note: this isn't a character body - the converter is built for replacing bodies."
            label.set(text)

        def load_target(self):
            pkg, mesh = self.c_pkg.get(), self.mesh_path(self.c_mesh_cb, self.c_mesh)
            if not pkg or not mesh:
                return
            self._target_req = req = getattr(self, "_target_req", 0) + 1     # ignore results of older requests
            self.groups = []

            def work():
                return core.outfit_groups(self.game.get(), pkg, mesh), core.material_slots(self.game.get(), pkg, mesh)

            def done(res):
                if req != self._target_req:
                    return
                self.groups, slots = res
                saved = self.s.get("hidden", {}).get(self.target_key())
                valid = {mid for g in self.groups for mid, _ in g["meshes"]}
                self.hidden = (set(saved) & valid) if saved is not None else core.CV.default_hidden(self.groups)
                self.fill_tree()
                self.log("Target %s / %s - material slots: %s. Name Blender materials like these (or slot00_..slot%02d_)."
                         % (pkg, mesh.split(".")[-1], ", ".join(slots), len(slots) - 1))
            self.run(work, done, quiet=True)

        # ---------------------------------------------------------------- hide list
        KIND = {"outfit": "face / eyes / hair of this body", "gear": "worn gear", "weapon": "weapon (moveset)"}

        def target_key(self):
            return "%s|%s" % (self.c_pkg.get(), self.mesh_path(self.c_mesh_cb, self.c_mesh).split(".")[-1])

        def fill_tree(self):
            open_state = {self.tree.item(i, "text"): self.tree.item(i, "open") for i in self.tree.get_children()}
            self.tree.delete(*self.tree.get_children()); self.row_mesh = {}
            for gi, g in enumerate(self.groups):
                ids = [mid for mid, _ in g["meshes"]]
                n = sum(mid in self.hidden for mid in ids)
                state = "all hidden" if n == len(ids) else "all visible" if n == 0 else "%d of %d hidden" % (n, len(ids))
                gid = self.tree.insert("", "end", iid="g%d" % gi, text=g["name"], values=(state, self.KIND[g["kind"]]),
                                       open=open_state.get(g["name"], False), tags=("hidden" if n == len(ids) else "shown" if n == 0 else "",))
                for mi, (mid, lab) in enumerate(g["meshes"]):
                    h = mid in self.hidden
                    iid = self.tree.insert(gid, "end", iid="g%dm%d" % (gi, mi), text=lab,
                                           values=("hidden" if h else "visible", ""), tags=("hidden" if h else "shown",))
                    self.row_mesh[iid] = mid

        def tree_click(self, ev):
            if self.tree.identify_region(ev.x, ev.y) == "tree" and self.tree.identify_element(ev.x, ev.y).lower().endswith("indicator"):
                return                                    # expand/collapse arrow: let Treeview handle it
            row = self.tree.identify_row(ev.y)
            if not row or self.busy:
                return "break"
            if row in self.row_mesh:
                self.hidden ^= {self.row_mesh[row]}
            else:
                ids = {self.row_mesh[c] for c in self.tree.get_children(row)}
                if ids <= self.hidden:
                    self.hidden -= ids
                else:
                    self.hidden |= ids
            self.fill_tree(); self.save()
            return "break"

        def hide_defaults(self):
            self.hidden = core.CV.default_hidden(self.groups); self.fill_tree(); self.save()

        def hide_set(self, hide):
            self.hidden = {mid for g in self.groups for mid, _ in g["meshes"]} if hide else set()
            self.fill_tree(); self.save()

        # ---------------------------------------------------------------- actions
        def choose_ws(self):
            d = filedialog.askdirectory(title="Choose your mod folder (outside the game folder)", initialdir=self.ws.get() or None)
            if d:
                self.ws.set(os.path.normpath(d)); self.save()

        def choose_game(self):
            d = filedialog.askdirectory(title="Choose the Mortal Kombat 11 folder", initialdir=self.game.get() or None)
            if d:
                self.game.set(os.path.normpath(d)); self.save(); self.refresh_packages()

        def choose_blender(self):
            p = filedialog.askopenfilename(title="blender.exe", filetypes=[("Blender", "blender.exe")])
            if p:
                self.blender.set(os.path.normpath(p)); self.save()

        def browse_model(self):
            p = filedialog.askopenfilename(title="Your exported character", filetypes=[("glTF binary", "*.glb")],
                                           initialdir=os.path.dirname(self.c_model.get()) or self.ws.get() or None)
            if p:
                self.c_model.set(os.path.normpath(p))
                if not self.c_name.get():
                    self.c_name.set(os.path.splitext(os.path.basename(p))[0])
                self.save()

        def browse_tex(self, param):
            p = filedialog.askopenfilename(title=core.TEX_LABELS[param], filetypes=[("Images", "*.png *.tga *.jpg *.jpeg *.bmp *.tif *.tiff")],
                                           initialdir=os.path.dirname(self.c_model.get()) or None)
            if p:
                v, cb, choices = self.tex[param]
                v.set(os.path.normpath(p))

        def texture_choices(self):
            out, keep_all = {}, True
            for param, (v, _, _) in self.tex.items():
                val = v.get().strip()
                if val == core.KEEP:
                    out[param] = "keep"; continue
                keep_all = False
                if val in (core.FROM_MODEL, core.DEFAULT, ""):
                    if val == core.DEFAULT and param == "ColorOverride":
                        out[param] = core.CV.load_defaults()["parameters"][param]
                    continue
                if not val.startswith("solid:") and not os.path.isfile(val):
                    raise MK11Error("texture file not found: %s" % val)
                out[param] = val
            return out, keep_all

        def do_export(self):
            ws, game, pkg, tex = self.ws.get(), self.game.get(), self.e_pkg.get(), self.e_tex.get()
            mesh = self.mesh_path(self.e_mesh_cb, self.e_mesh)
            blend, bl = self.e_blend.get(), self.blender.get()
            if blend and not bl:
                self.log("No Blender chosen - exporting the .glb only (choose blender.exe in Folders for the .blend).", "WARN")
            self.save()
            self.run(lambda: core.export_original(ws, game, pkg, mesh, tex, blend=blend and bool(bl), blender=bl, log=self.qlog),
                     lambda out: self.log("Exported to %s" % out, "PASS"))

        def do_check(self):
            game, pkg, model = self.game.get(), self.c_pkg.get(), self.c_model.get()
            mesh = self.mesh_path(self.c_mesh_cb, self.c_mesh)
            if not os.path.isfile(model):
                return messagebox.showwarning(core.APP, "Choose your .glb first.")
            self.save()

            def done(res):
                self.log("Compatibility check: %s vs %s" % (os.path.basename(model), mesh.split(".")[-1]), "HEAD")
                for lvl in ("FAIL", "WARN", "PASS", "INFO"):
                    for l, m in res:
                        if l == lvl:
                            self.log("[%s] %s" % (l, m), l if l != "INFO" else None)
                s = core.summary(res)
                self.log("RESULT: " + s, "PASS" if s.startswith("COMPATIBLE") else "FAIL")
            self.run(lambda: core.check_model(game, pkg, mesh, model), done)

        def do_convert(self):
            ws, game, pkg, model = self.ws.get(), self.game.get(), self.c_pkg.get(), self.c_model.get()
            mesh = self.mesh_path(self.c_mesh_cb, self.c_mesh)
            name = self.c_name.get().strip() or os.path.splitext(os.path.basename(model))[0]
            try:
                textures, keep_all = self.texture_choices()
            except MK11Error as e:
                return messagebox.showerror(core.APP, str(e))
            hide = set(self.hidden)
            if not self.groups:
                return messagebox.showwarning(core.APP, "Still loading the character's mesh list - try again in a moment.")
            self.save()

            author, desc = self.c_author.get().strip(), self.c_desc.get().strip()

            def done(out):
                self.last_result = out
                self.log("Converted: %s" % os.path.join(out, "Asset"), "PASS")
                self.log("Check preview_%s.glb in Blender and read INSTALL.txt. The generated game files are for your own "
                         "installation only - to share the mod, share its Mod Project (mod_projects folder)." % mesh.split(".")[-1],
                         "WARN")
            self.run(lambda: core.convert_job(ws, game, pkg, mesh, model, name, textures=textures, keep_textures=keep_all,
                                              hide=hide, log=self.qlog, author=author, description=desc), done)

        # ---------------------------------------------------------------- plumbing
        def save(self):
            self.s.update(workspace=self.ws.get(), game_dir=self.game.get(), model=self.c_model.get(),
                          name=self.c_name.get(), package=self.c_pkg.get(), author=self.c_author.get(),
                          description=self.c_desc.get(),
                          mesh=self.mesh_path(self.c_mesh_cb, self.c_mesh), blender=self.blender.get())
            self.s.pop("keep_gear", None)
            if self.groups:                       # remember the hide list per target character/mesh
                self.s.setdefault("hidden", {})[self.target_key()] = sorted(self.hidden)
            core.save_settings(self.s)

        def run(self, work, done=None, quiet=False):
            """Run `work` on a worker thread. Jobs (quiet=False) are exclusive and lock the buttons; quiet lookups
            (mesh lists, gear sets) run alongside them without touching the busy state."""
            if not quiet:
                if self.busy:
                    return
                self.busy = True
                self.prog.start(12)
                for b in self.buttons:
                    b.state(["disabled"])

            def th():
                try:
                    res = work(); self.q.put(("done", (done, res, quiet)))
                except MK11Error as e:
                    self.q.put(("error", (str(e), quiet)))
                except Exception:
                    self.q.put(("error", (traceback.format_exc(), quiet)))
            threading.Thread(target=th, daemon=True).start()

        def qlog(self, m):
            self.q.put(("log", str(m)))

        def poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "log":
                        self.log(val)
                    else:
                        quiet = val[-1]
                        if not quiet:
                            self.busy = False; self.prog.stop()
                            for b in self.buttons:
                                b.state(["!disabled"])
                        if kind == "done":
                            fn, res, _ = val
                            if fn:
                                fn(res)
                        else:
                            msg = val[0]
                            self.log("ERROR: " + msg, "FAIL")
                            messagebox.showerror(core.APP, msg.splitlines()[-1] if len(msg) > 400 else msg)
            except queue.Empty:
                pass
            self.after(100, self.poll)

        def log(self, msg, tag=None):
            self.logbox.configure(state="normal")
            self.logbox.insert("end", msg + "\n", (tag,) if tag else ())
            self.logbox.see("end"); self.logbox.configure(state="disabled")

        def open_dir(self, d):
            if d and os.path.isdir(d):
                os.startfile(d)
            else:
                messagebox.showinfo(core.APP, "Folder doesn't exist yet:\n%s" % d)

        def show_help(self):
            messagebox.showinfo("How to use", HELP)

        def close(self):
            self.save(); self.destroy()

    app = Studio()
    if smoke:                     # test hook: drive the real buttons (check, then convert) and record the outcome
        model, ws, result_file = smoke
        steps = iter(["check", "clicks", "convert", "end"])
        notes = []

        def click(iid, part="text"):
            app.tree.see(iid); app.update()
            x, y, w, h = app.tree.bbox(iid, "#0")
            xs = [xx for xx in range(x, x + w, 2) if app.tree.identify_element(xx, y + h // 2).lower().endswith(
                "indicator" if part == "indicator" else "text")]
            hit = app.tree.identify_row(y + h // 2); n0 = len(app.hidden)
            app.tree.event_generate("<Button-1>", x=xs[0] + 1, y=y + h // 2); app.update()
            notes.append("  click on %s (%s) landed on row %s; hidden %d -> %d" % (iid, part, hit, n0, len(app.hidden)))

        def step():
            if app.busy or not app.c_mesh.get() or not app.groups:
                return app.after(300, step)
            s = next(steps)
            if s == "check":
                app.ws.set(ws); app.c_model.set(model); app.c_name.set("gui_smoke")
                app.hide_defaults(); notes.append("start: %d hidden (defaults)" % len(app.hidden)); app.do_check()
            elif s == "clicks":
                gb = next("g%d" % i for i, g in enumerate(app.groups) if g["kind"] == "weapon")
                ids = set(app.row_mesh[c] for c in app.tree.get_children(gb))
                click(gb); notes.append("weapon group click 1 -> hidden: %s" % (ids <= app.hidden))
                click(gb); notes.append("weapon group click 2 -> visible: %s" % (not (ids & app.hidden)))
                before = set(app.hidden); click("g0", "indicator")
                notes.append("arrow click -> expanded: %s, selection unchanged: %s" % (bool(app.tree.item("g0", "open")), before == app.hidden))
                face = next(c for c in app.tree.get_children("g0") if app.tree.item(c, "text") == "LIU_FACE_A")
                click(face); notes.append("LIU_FACE_A click -> visible: %s" % (app.row_mesh[face] not in app.hidden))
                for n in notes:
                    app.log("SMOKE " + n)
            elif s == "convert":
                app.do_convert()
            else:
                txt = app.logbox.get("1.0", "end")
                with open(result_file, "w", encoding="utf-8") as f:
                    f.write(txt)
                return app.destroy()
            app.after(1500, step)
        app.after(3000, step)
    if tab:
        app.nb.select(tab)
    if selftest:
        app.after(1500, app.destroy)
    app.mainloop()


HELP = """1. Export tab: export the original mesh you are replacing (a .glb, plus a ready-made .blend if Blender is set).
2. In Blender: build your character over it in the same A-pose and scale. Rig it to that armature without
   changing bones; weight only body bones (run Blender Tools\\blender_check_weights.py), max 4 weights per vertex.
   Export .glb: Selected Objects, Apply Modifiers ON, Shape Keys OFF, Animations OFF, Bone Influences 4, no Draco.
3. Convert tab: choose the same character, your .glb, press Check model, pick what to hide, then Convert.
4. Results are in <mod folder>\\converted\\<name>. Open preview_*.glb in Blender to check; see INSTALL.txt.
5. Sharing: share <mod folder>\\mod_projects\\<name> (your model, textures and mod.json). Never share the generated
   .xxx/.psf files. Others build the mod from their own game with "Create Mod.cmd"."""

if __name__ == "__main__":
    run_gui(selftest="--selftest" in sys.argv,
            smoke=tuple(sys.argv[sys.argv.index("--smoke") + 1:][:3]) if "--smoke" in sys.argv else None,
            tab=int(sys.argv[sys.argv.index("--tab") + 1]) - 1 if "--tab" in sys.argv else 0)
