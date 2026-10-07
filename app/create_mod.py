#!/usr/bin/env python3
"""
Create Mod - builds a shared MK11 Character Studio Mod Project on your own computer.

A Mod Project contains only its creator's model, textures and mod.json. Create Mod reads the original game files the
mod needs from YOUR Mortal Kombat 11 installation and generates the finished game files locally, with the same
converter MK11 Character Studio uses. It never downloads, provides or unlocks game files.
Double-click "Create Mod.cmd".
"""
import os
import queue
import sys
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import studio_core as core                       # noqa: E402
import mod_project as MP                         # noqa: E402
from mk11.oodle import MK11Error                 # noqa: E402

TITLE = "Create Mod - %s %s (unofficial community tool)" % (core.APP, core.VERSION)


def run_gui(smoke=None, initial=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText

    class CreateMod(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(TITLE)
            self.geometry("900x760"); self.minsize(780, 640)
            self.q = queue.Queue(); self.busy = False; self.buttons = []
            self.project = None; self.output = None; self.smoke_notes = []; self.dialog_open = False
            self.s = core.load_settings()
            self.game = tk.StringVar(value=self.s.get("game_dir") or core.detect_game_dir())
            self.ws = tk.StringVar(value=self.s.get("create_mod_output") or self.s.get("workspace") or
                                   os.path.join(os.path.expanduser("~"), "Downloads", "Custom MK11 Workspace"))
            self.proj_path = tk.StringVar()
            self.info = {k: tk.StringVar() for k in ("name", "author", "description", "base", "files", "model", "textures")}
            self.build()
            self.after(100, self.poll)

        def build(self):
            pad = dict(padx=6, pady=4)
            pf = ttk.LabelFrame(self, text="Mod Project"); pf.pack(fill="x", **pad)
            ttk.Entry(pf, textvariable=self.proj_path, state="readonly").pack(side="left", fill="x", expand=True, **pad)
            self.btn(pf, "Select mod.json...", self.choose_project).pack(side="left", **pad)

            inf = ttk.LabelFrame(self, text="This mod"); inf.pack(fill="x", **pad)
            rows = [("Mod name", "name"), ("Author", "author"), ("Description", "description"), ("Base character", "base"),
                    ("Required original game files", "files"), ("Custom model", "model"), ("Custom textures", "textures")]
            for r, (label, key) in enumerate(rows):
                ttk.Label(inf, text=label + ":").grid(row=r, column=0, sticky="nw", **pad)
                ttk.Label(inf, textvariable=self.info[key], wraplength=620, justify="left").grid(row=r, column=1, sticky="w", **pad)
            inf.columnconfigure(1, weight=1)

            ff = ttk.LabelFrame(self, text="Folders"); ff.pack(fill="x", **pad)
            ttk.Label(ff, text="Mortal Kombat 11 installation (read only):").grid(row=0, column=0, sticky="w", **pad)
            ttk.Entry(ff, textvariable=self.game, state="readonly").grid(row=0, column=1, sticky="ew", **pad)
            self.btn(ff, "Choose...", self.choose_game).grid(row=0, column=2, **pad)
            ttk.Label(ff, text="Output folder (your mod folder):").grid(row=1, column=0, sticky="w", **pad)
            ttk.Entry(ff, textvariable=self.ws, state="readonly").grid(row=1, column=1, sticky="ew", **pad)
            self.btn(ff, "Choose...", self.choose_ws).grid(row=1, column=2, **pad)
            ttk.Label(ff, foreground="#555", text="The mod is built from your own game files into <output folder>\\created_mods\\<mod>\\. "
                      "Create Mod never downloads game files.",
                      wraplength=820).grid(row=2, column=0, columnspan=3, sticky="w", **pad)
            ff.columnconfigure(1, weight=1)

            bf = ttk.Frame(self); bf.pack(fill="x", **pad)
            self.create_btn = self.btn(bf, "Create Mod", self.do_create); self.create_btn.pack(side="left", padx=6)
            self.open_btn = ttk.Button(bf, text="Open Output Folder", command=self.open_output, state="disabled")
            self.open_btn.pack(side="left", padx=6)
            self.prog = ttk.Progressbar(bf, mode="determinate", value=0); self.prog.pack(side="left", fill="x", expand=True, padx=6)

            self.logbox = ScrolledText(self, height=12, state="disabled", font=("Consolas", 9))
            self.logbox.pack(fill="both", expand=True, **pad)
            for tag, col in (("FAIL", "#c0392b"), ("WARN", "#b9770e"), ("PASS", "#1e8449")):
                self.logbox.tag_config(tag, foreground=col)
            self.log("Select a Mod Project's mod.json to begin.")

        def btn(self, parent, text, cmd):
            b = ttk.Button(parent, text=text, command=cmd); self.buttons.append(b); return b

        # ------------------------------------------------------------ project
        def choose_project(self):
            p = filedialog.askopenfilename(title="Select the mod's mod.json", filetypes=[("Mod Project", "mod.json")])
            if p:
                self.load(p)

        def load(self, path):
            self.proj_path.set(os.path.normpath(path)); self.project = None
            for v in self.info.values():
                v.set("")
            self.run(lambda: MP.load_project(path), self.loaded)

        def loaded(self, proj):
            self.project = proj
            self.output = None; self.open_btn.state(["disabled"])
            m = proj.m; b = m["base"]
            self.info["name"].set(m["name"])
            self.info["author"].set(m.get("author") or "-")
            self.info["description"].set(m.get("description") or "-")
            self.info["base"].set("%s (%s)" % (b.get("character_name") or core.character_name(b["package"]) or b["character"],
                                               b["package"]))
            self.info["model"].set(os.path.basename(m["model"]))
            tf = proj.texture_files()
            self.info["textures"].set(", ".join(os.path.basename(t) for t in tf) or "none (uses the model's own colours and "
                                      "neutral defaults)")
            self.refresh_files()
            self.log("Mod Project OK: %s (format %d, files verified)" % (m["name"], m["format_version"]), "PASS")

        def refresh_files(self):
            if not self.project:
                return []
            req = MP.required_files(self.project, self.game.get())
            self.info["files"].set("\n".join("%s  -  %s" % (fn, "found in your installation" if ok else "NOT FOUND")
                                             for fn, _, ok in req))
            return [fn for fn, _, ok in req if not ok]

        # ------------------------------------------------------------ build
        def do_create(self):
            if not self.project:
                self.log("Select a Mod Project (its mod.json) first.", "WARN")
                return None if smoke else messagebox.showwarning("Create Mod", "Select a Mod Project (its mod.json) first.")
            missing = self.refresh_files()
            if missing:
                msg = MP.MISSING_GAME_FILE.format(files="\n".join(missing))
                self.log(msg.replace("\n\n", "\n"), "FAIL")
                if not smoke:
                    messagebox.showerror("Create Mod", msg)
                return
            summary = MP.summary(self.project, self.game.get())
            if not smoke and not messagebox.askokcancel("Create Mod", summary + "\n\nCreate the mod now?"):
                return
            self.save()
            proj, game, ws = self.project, self.game.get(), self.ws.get()
            self.log("Building %s ..." % self.project.name)
            self.run(lambda: MP.build_project(proj, game, ws, log=self.qlog), self.built)

        def built(self, out):
            self.output = out
            self.log("Finished game files: %s" % os.path.join(out, "Asset"), "PASS")
            self.show_warning()

        def show_warning(self):
            w = tk.Toplevel(self); w.title("Mod created"); w.transient(self); w.resizable(False, False)
            self.dialog_open = True
            head, body = MP.REDISTRIBUTION_WARNING.split("\n\n", 1)
            ttk.Label(w, text=head, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 6))
            ttk.Label(w, text=body, wraplength=560, justify="left").pack(anchor="w", padx=18)

            def ok():
                w.grab_release(); w.destroy(); self.dialog_open = False
                self.open_btn.state(["!disabled"])
                self.log("Remember: share the Mod Project, never the generated game files.", "WARN")
            ttk.Button(w, text="OK", command=ok).pack(pady=14)
            w.grab_set()
            if smoke:
                self.smoke_notes.append("WARNING DIALOG: " + MP.REDISTRIBUTION_WARNING.replace("\n", " | "))
                self.after(int(os.environ.get("CREATE_MOD_SMOKE_DIALOG_MS", "800")), ok)

        def open_output(self):
            if self.output and os.path.isdir(self.output):
                os.startfile(self.output)

        # ------------------------------------------------------------ folders / plumbing
        def choose_game(self):
            d = filedialog.askdirectory(title="Choose your Mortal Kombat 11 folder", initialdir=self.game.get() or None)
            if d:
                self.game.set(os.path.normpath(d)); self.save(); self.refresh_files()

        def choose_ws(self):
            d = filedialog.askdirectory(title="Choose an output folder (outside the game folder)", initialdir=self.ws.get() or None)
            if d:
                self.ws.set(os.path.normpath(d)); self.save()

        def save(self):
            s = core.load_settings()
            s.update(game_dir=self.game.get(), create_mod_output=self.ws.get())
            core.save_settings(s)

        def run(self, work, done=None):
            if self.busy:
                return
            self.busy = True; self.prog.config(mode="indeterminate"); self.prog.start(12)
            for b in self.buttons:
                b.state(["disabled"])

            def th():
                try:
                    self.q.put(("done", (done, work())))
                except MK11Error as e:
                    self.q.put(("error", str(e)))
                except Exception:
                    self.q.put(("error", traceback.format_exc()))
            threading.Thread(target=th, daemon=True).start()

        def qlog(self, m):
            self.q.put(("log", str(m)))

        def poll(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "log":
                        self.log(val); continue
                    self.busy = False; self.prog.stop(); self.prog.config(mode="determinate", value=0)
                    for b in self.buttons:
                        b.state(["!disabled"])
                    if kind == "done":
                        fn, res = val
                        if fn:
                            fn(res)
                    else:
                        self.log("ERROR: " + val, "FAIL")
                        if not smoke:
                            messagebox.showerror("Create Mod", val.splitlines()[-1] if len(val) > 1500 else val)
            except queue.Empty:
                pass
            self.after(100, self.poll)

        def log(self, msg, tag=None):
            self.logbox.configure(state="normal")
            self.logbox.insert("end", msg + "\n", (tag,) if tag else ())
            self.logbox.see("end"); self.logbox.configure(state="disabled")

    app = CreateMod()
    if smoke:                      # test hook: select project -> Create Mod -> acknowledge warning -> record
        project, game, ws, result_file = smoke
        steps = iter(["load", "create", "end"])

        def step():
            if app.busy or app.dialog_open:
                return app.after(300, step)
            s = next(steps)
            if s == "load":
                app.game.set(game); app.ws.set(ws); app.load(project)
            elif s == "create":
                app.do_create()
            else:
                finish(); return
            app.after(1000, step)

        def finish():
            txt = app.logbox.get("1.0", "end") + "\n".join(app.smoke_notes) + \
                "\nOPEN_OUTPUT_ENABLED: %s\n" % ("disabled" not in app.open_btn.state())
            with open(result_file, "w", encoding="utf-8") as f:
                f.write(txt)
            app.destroy()
        app.after(500, step)
    elif initial:                  # a mod.json dropped on "Create Mod.cmd" (still fully validated)
        app.after(300, lambda: app.load(initial))
    app.mainloop()


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        run_gui(smoke=tuple(sys.argv[sys.argv.index("--smoke") + 1:][:4]))
    else:
        run_gui(initial=next((a for a in sys.argv[1:] if a.lower().endswith("mod.json")), None))
