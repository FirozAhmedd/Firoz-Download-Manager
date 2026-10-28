# -*- coding: utf-8 -*-
"""Firoz Download Manager - GUI. Run:  python firoz_download_manager.py"""
import ctypes
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fdm_engine as E

STATUS_TEXT = {"queued": "Queued", "connecting": "Connecting...", "downloading": "Downloading",
               "paused": "Paused", "completed": "Complete", "error": "Error"}
FILTERS = [("All Downloads", None),
           ("Downloading", {"connecting", "downloading"}),
           ("Queued", {"queued"}),
           ("Paused", {"paused"}),
           ("Completed", {"completed"}),
           ("Failed", {"error"})]
IS_WIN = sys.platform.startswith("win")


def app_dir():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def open_path(p, select=False):
    try:
        if IS_WIN:
            if select and Path(p).exists():
                subprocess.Popen(["explorer", f"/select,{p}"])
            else:
                os.startfile(str(p if Path(p).is_dir() else Path(p)))  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception as e:
        messagebox.showerror(E.APP_NAME, f"Could not open:\n{e}")


# --------------------------------------------------------------------------
class AddDialog(tk.Toplevel):
    def __init__(self, app, text="", referer="", cookie=""):
        super().__init__(app)
        self.app, self.referer, self.cookie = app, referer, cookie
        self.title("Add new download")
        self.resizable(False, False)
        self.transient(app)
        pad = dict(padx=10, pady=5)
        ttk.Label(self, text="Paste download link(s) - one per line:").grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)
        self.txt = tk.Text(self, width=70, height=6, wrap="none")
        self.txt.grid(row=1, column=0, columnspan=3, **pad)
        self.txt.insert("1.0", text)
        ttk.Label(self, text="Save to:").grid(row=2, column=0, sticky="w", **pad)
        self.folder = tk.StringVar(value=app.settings["download_dir"])
        ttk.Entry(self, textvariable=self.folder, width=56).grid(row=2, column=1, **pad)
        ttk.Button(self, text="Browse...", command=self.browse).grid(row=2, column=2, **pad)
        self.cat = tk.BooleanVar(value=app.settings["categorize"])
        ttk.Checkbutton(self, text="Sort into category folders (Video, Music, Compressed...)",
                        variable=self.cat).grid(row=3, column=0, columnspan=3, sticky="w", **pad)
        bar = ttk.Frame(self)
        bar.grid(row=4, column=0, columnspan=3, sticky="e", **pad)
        ttk.Button(bar, text="Download Now", command=lambda: self.ok(True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Add Only", command=lambda: self.ok(False)).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.deiconify()
        self.lift()
        self.focus_force()
        self.txt.focus_set()

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if d:
            self.folder.set(d)

    def ok(self, start):
        urls = [u.strip() for u in self.txt.get("1.0", "end").splitlines() if u.strip()]
        good = [u for u in urls if re.match(r"^https?://", u, re.I)]
        if not good:
            messagebox.showwarning(E.APP_NAME, "Please enter a valid http:// or https:// link.", parent=self)
            return
        folder = self.folder.get().strip() or self.app.settings["download_dir"]
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror(E.APP_NAME, f"Cannot use that folder:\n{e}", parent=self)
            return
        self.app.mgr.add(good, folder, self.cat.get(), start, self.referer, self.cookie)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, s = app, app.settings
        self.title("Settings")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        pad = dict(padx=10, pady=6)
        self.vars = {
            "download_dir": tk.StringVar(value=s["download_dir"]),
            "connections": tk.IntVar(value=s["connections"]),
            "max_active": tk.IntVar(value=s["max_active"]),
            "speed_limit_kb": tk.IntVar(value=s["speed_limit_kb"]),
            "clipboard": tk.BooleanVar(value=s["clipboard"]),
            "categorize": tk.BooleanVar(value=s["categorize"]),
        }
        ttk.Label(self, text="Default download folder:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(self, textvariable=self.vars["download_dir"], width=42).grid(row=0, column=1, **pad)
        ttk.Button(self, text="Browse...", command=self.browse).grid(row=0, column=2, **pad)
        rows = [("Connections per download (1-16):", "connections", 1, 16),
                ("Simultaneous downloads (1-10):", "max_active", 1, 10),
                ("Speed limit in KB/s (0 = unlimited):", "speed_limit_kb", 0, 1000000)]
        for i, (label, key, lo, hi) in enumerate(rows, start=1):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", **pad)
            ttk.Spinbox(self, from_=lo, to=hi, textvariable=self.vars[key], width=10).grid(
                row=i, column=1, sticky="w", **pad)
        ttk.Checkbutton(self, text="Watch the clipboard for download links",
                        variable=self.vars["clipboard"]).grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(self, text="Sort new downloads into category folders",
                        variable=self.vars["categorize"]).grid(row=5, column=0, columnspan=3, sticky="w", **pad)
        bar = ttk.Frame(self)
        bar.grid(row=6, column=0, columnspan=3, sticky="e", **pad)
        ttk.Button(bar, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.vars["download_dir"].get() or None)
        if d:
            self.vars["download_dir"].set(d)

    def save(self):
        try:
            new = {
                "download_dir": self.vars["download_dir"].get().strip() or self.app.settings["download_dir"],
                "connections": max(1, min(16, int(self.vars["connections"].get()))),
                "max_active": max(1, min(10, int(self.vars["max_active"].get()))),
                "speed_limit_kb": max(0, int(self.vars["speed_limit_kb"].get())),
                "clipboard": bool(self.vars["clipboard"].get()),
                "categorize": bool(self.vars["categorize"].get()),
            }
        except (tk.TclError, ValueError):
            messagebox.showwarning(E.APP_NAME, "Please enter valid numbers.", parent=self)
            return
        self.app.settings.update(new)
        self.app.settings.save()
        self.app.clip_var.set(new["clipboard"])
        self.destroy()


# --------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{E.APP_NAME}  {E.VERSION}")
        self.geometry("1020x580")
        self.minsize(780, 420)
        self.settings = E.Settings()
        self.mgr = E.Manager(self.settings)
        self.bridge = E.BrowserBridge(self.mgr)
        self.bridge.start()
        self.dialog = None
        self.filter = None
        self.was_busy = False
        try:
            self._last_clip = self.clipboard_get()
        except tk.TclError:
            self._last_clip = ""
        self.clip_var = tk.BooleanVar(value=self.settings["clipboard"])
        self.shutdown_var = tk.BooleanVar(value=False)
        self._style()
        self._menu()
        self._toolbar()
        self._body()
        self.status = ttk.Label(self, anchor="w", relief="sunken", padding=(8, 2))
        self.status.pack(side="bottom", fill="x")
        self.bind("<Control-n>", lambda e: self.add_dialog())
        self.bind("<Delete>", lambda e: self.remove_sel())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self.refresh)

    # ---- layout
    def _style(self):
        st = ttk.Style(self)
        st.theme_use("vista" if "vista" in st.theme_names() else "clam")
        st.configure("Treeview", rowheight=26)
        st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Add URL...   Ctrl+N", command=self.add_dialog)
        f.add_command(label="Open downloads folder",
                      command=lambda: open_path(self.settings["download_dir"]))
        f.add_separator()
        f.add_command(label="Exit", command=self.on_close)
        d = tk.Menu(m, tearoff=0)
        d.add_command(label="Resume", command=self.resume_sel)
        d.add_command(label="Pause", command=self.pause_sel)
        d.add_command(label="Pause all", command=self.mgr.pause_all)
        d.add_separator()
        d.add_command(label="Remove from list", command=self.remove_sel)
        d.add_command(label="Remove completed", command=self.remove_completed)
        o = tk.Menu(m, tearoff=0)
        o.add_command(label="Settings...", command=lambda: SettingsDialog(self))
        o.add_checkbutton(label="Watch clipboard for links", variable=self.clip_var,
                          command=self._clip_toggle)
        if IS_WIN:
            o.add_checkbutton(label="Shut down PC when all downloads finish", variable=self.shutdown_var)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Browser integration (Chrome / Edge / Brave)", command=self.help_extension)
        h.add_command(label="About", command=lambda: messagebox.showinfo(
            E.APP_NAME, f"{E.APP_NAME} {E.VERSION}\nFree multi-connection download manager."))
        for label, menu in (("File", f), ("Downloads", d), ("Options", o), ("Help", h)):
            m.add_cascade(label=label, menu=menu)
        self.config(menu=m)

    def _clip_toggle(self):
        self.settings["clipboard"] = self.clip_var.get()
        self.settings.save()

    def _toolbar(self):
        bar = ttk.Frame(self, padding=(6, 6))
        bar.pack(side="top", fill="x")
        for text, cmd in (("+ Add URL", self.add_dialog), ("Resume", self.resume_sel),
                          ("Pause", self.pause_sel), ("Pause All", self.mgr.pause_all),
                          ("Remove", self.remove_sel), ("Settings", lambda: SettingsDialog(self)),
                          ("Open Folder", self.open_folder_sel)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)

    def _body(self):
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)
        self.side = tk.Listbox(pane, exportselection=False, activestyle="none", width=18,
                               font=("Segoe UI", 10), borderwidth=0, highlightthickness=0)
        for name, _ in FILTERS:
            self.side.insert("end", "  " + name)
        self.side.selection_set(0)
        self.side.bind("<<ListboxSelect>>", self._on_filter)
        pane.add(self.side, weight=0)
        right = ttk.Frame(pane)
        pane.add(right, weight=1)
        cols = ("name", "size", "progress", "status", "speed", "eta", "added")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        for c, text, w in (("name", "File name", 260), ("size", "Size", 85), ("progress", "Progress", 150),
                           ("status", "Status", 130), ("speed", "Speed", 85), ("eta", "Time left", 70),
                           ("added", "Added", 120)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, minwidth=50, anchor="w" if c in ("name", "status") else "center")
        sb = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("error", foreground="#c62828")
        self.tree.tag_configure("completed", foreground="#2e7d32")
        self.tree.bind("<Double-1>", lambda e: self.open_sel())
        self.tree.bind("<Return>", lambda e: self.open_sel())
        self.tree.bind("<Button-3>", self._context)
        self.menu_ctx = tk.Menu(self, tearoff=0)
        for label, cmd in (("Open file", self.open_sel), ("Open folder", self.open_folder_sel),
                           (None, None), ("Resume", self.resume_sel), ("Pause", self.pause_sel),
                           (None, None), ("Copy link", self.copy_link),
                           ("Remove from list", self.remove_sel),
                           ("Delete file from disk", lambda: self.remove_sel(True))):
            if label:
                self.menu_ctx.add_command(label=label, command=cmd)
            else:
                self.menu_ctx.add_separator()

    def _on_filter(self, _e=None):
        sel = self.side.curselection()
        if sel:
            self.filter = FILTERS[sel[0]][1]
            self.refresh(once=True)

    def _context(self, e):
        row = self.tree.identify_row(e.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu_ctx.tk_popup(e.x_root, e.y_root)

    # ---- helpers
    def selected(self):
        ids = set(self.tree.selection())
        return [d for d in self.mgr.items if d.id in ids]

    def row(self, d):
        p = d.percent
        n = int(p // 10)
        prog = f"{'█' * n}{'░' * (10 - n)} {p:4.1f}%" if (d.size > 0 or d.status == "completed") else "--"
        st = STATUS_TEXT.get(d.status, d.status)
        if d.status == "error" and d.error:
            st = f"Error: {d.error}"[:60]
        name = d.filename or (d.url.rsplit("/", 1)[-1] or d.url)
        speed = f"{E.fmt_size(d.speed)}/s" if d.status == "downloading" and d.speed else ""
        return (name, E.fmt_size(d.size), prog, st, speed, E.fmt_time(d.eta) if d.eta else "",
                time.strftime("%Y-%m-%d %H:%M", time.localtime(d.added)))

    # ---- actions
    def add_dialog(self, text="", referer="", cookie=""):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        self.deiconify()
        self.dialog = AddDialog(self, text, referer, cookie)

    def resume_sel(self):
        for d in self.selected():
            self.mgr.resume(d)

    def pause_sel(self):
        for d in self.selected():
            d.pause()

    def remove_sel(self, delete_file=False):
        sel = self.selected()
        if not sel:
            return
        msg = (f"Delete {len(sel)} file(s) from disk and remove from the list?" if delete_file
               else f"Remove {len(sel)} download(s) from the list?\n(Finished files stay on disk.)")
        if messagebox.askyesno(E.APP_NAME, msg, parent=self):
            for d in sel:
                self.mgr.remove(d, delete_file)

    def remove_completed(self):
        for d in [d for d in self.mgr.items if d.status == "completed"]:
            self.mgr.remove(d)

    def open_sel(self):
        for d in self.selected()[:1]:
            if d.status == "completed" and d.path.exists():
                open_path(d.path)

    def open_folder_sel(self):
        sel = self.selected()
        if sel:
            d = sel[0]
            open_path(d.path if d.status == "completed" else d.folder, select=d.status == "completed")
        else:
            open_path(self.settings["download_dir"])

    def copy_link(self):
        sel = self.selected()
        if sel:
            self.clipboard_clear()
            self.clipboard_append("\n".join(d.url for d in sel))
            self._last_clip = self.clipboard_get()

    def help_extension(self):
        ext = app_dir() / "browser_extension"
        messagebox.showinfo(
            "Browser integration",
            "To let Chrome / Edge / Brave send downloads here:\n\n"
            "1. Open  chrome://extensions  (edge://extensions for Edge)\n"
            "2. Turn on 'Developer mode'\n"
            "3. Click 'Load unpacked' and choose the folder:\n"
            f"   {ext}\n\n"
            "While this program is running, browser downloads open the 'Add new download' window. "
            "When it is closed, the browser downloads normally.")
        if ext.exists():
            open_path(ext)

    # ---- periodic work
    def refresh(self, once=False):
        try:
            self.mgr.tick()
            items = [d for d in self.mgr.items if self.filter is None or d.status in self.filter]
            ids = [d.id for d in items]
            if list(self.tree.get_children()) != ids:
                sel = set(self.tree.selection())
                self.tree.delete(*self.tree.get_children())
                for d in items:
                    self.tree.insert("", "end", iid=d.id, values=self.row(d), tags=(d.status,))
                self.tree.selection_set([i for i in ids if i in sel])
            else:
                for d in items:
                    self.tree.item(d.id, values=self.row(d), tags=(d.status,))
            active = [d for d in self.mgr.items if d.status == "downloading"]
            total = sum(d.speed for d in active)
            limit = int(self.settings["speed_limit_kb"])
            self.status.config(text=f"  Total: {len(self.mgr.items)}    Active: {len(active)}    "
                                    f"Speed: {E.fmt_size(total)}/s    "
                                    f"Limit: {'none' if limit <= 0 else f'{limit} KB/s'}")
            self._shutdown_check()
            if not once:
                self._incoming()
        finally:
            if not once:
                self.after(500, self.refresh)

    def _dialog_open(self):
        return self.dialog is not None and self.dialog.winfo_exists()

    def _incoming(self):
        if self._dialog_open():
            return
        if not self.mgr.incoming.empty():
            it = self.mgr.incoming.get_nowait()
            self.add_dialog(it["url"], it["referer"], it["cookie"])
            return
        if self.clip_var.get():
            try:
                text = self.clipboard_get().strip()
            except tk.TclError:
                return
            if text != self._last_clip:
                self._last_clip = text
                if E.looks_downloadable(text):
                    self.add_dialog(text)

    def _shutdown_check(self):
        busy = self.mgr.busy
        if self.shutdown_var.get() and self.was_busy and not busy:
            self.shutdown_var.set(False)
            try:
                subprocess.Popen(["shutdown", "/s", "/t", "60"])
                if messagebox.askyesno(E.APP_NAME, "All downloads finished.\n"
                                                   "The PC will shut down in 60 seconds.\n\n"
                                                   "Cancel the shutdown?"):
                    subprocess.Popen(["shutdown", "/a"])
            except Exception:
                pass
        self.was_busy = busy

    def on_close(self):
        if self.mgr.busy and not messagebox.askyesno(
                E.APP_NAME, "Downloads are still running.\nPause them and exit?\n"
                            "(You can resume them next time.)"):
            return
        self.bridge.stop()
        self.mgr.shutdown()
        self.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
