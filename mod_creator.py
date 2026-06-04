import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import re

# ── Palette (mirrors the main app) ─────────────────────────────────────────
BG         = "#0d0f14"
PANEL      = "#13161e"
CARD       = "#1a1e2a"
CARD_HOVER = "#1f2538"
ACCENT     = "#00e5ff"
ACCENT2    = "#7b2fff"
SUCCESS    = "#00e676"
WARNING    = "#ffab00"
DANGER     = "#ff3d71"
TEXT       = "#e8eaf6"
MUTED      = "#5c6080"
BORDER     = "#252a3a"

FONT_TITLE = ("Courier New", 22, "bold")
FONT_SUB   = ("Courier New", 10)
FONT_LABEL = ("Courier New", 9, "bold")
FONT_CARD  = ("Courier New", 11, "bold")
FONT_SMALL = ("Courier New", 8)
FONT_BTN   = ("Courier New", 9, "bold")
FONT_MONO  = ("Courier New", 9)
FONT_INPUT = ("Courier New", 10)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def lerp_color(c1, c2, t):
    r = int(c1[0] + (c2[0]-c1[0])*t)
    g = int(c1[1] + (c2[1]-c1[1])*t)
    b = int(c1[2] + (c2[2]-c1[2])*t)
    return f"#{r:02x}{g:02x}{b:02x}"

import time


def styled_entry(parent, textvariable=None, width=None, placeholder=None, fg=TEXT):
    kw = dict(bg=CARD, fg=fg, insertbackground=ACCENT,
              relief="flat", font=FONT_INPUT,
              highlightthickness=1, highlightbackground=BORDER,
              highlightcolor=ACCENT)
    if textvariable:
        kw["textvariable"] = textvariable
    if width:
        kw["width"] = width
    e = tk.Entry(parent, **kw)
    if placeholder:
        e._placeholder = placeholder
        e._has_placeholder = True
        e.insert(0, placeholder)
        e.config(fg=MUTED)
        def on_focus_in(evt, entry=e):
            if entry._has_placeholder:
                entry.delete(0, "end")
                entry.config(fg=TEXT)
                entry._has_placeholder = False
        def on_focus_out(evt, entry=e):
            if not entry.get():
                entry.insert(0, entry._placeholder)
                entry.config(fg=MUTED)
                entry._has_placeholder = True
        e.bind("<FocusIn>",  on_focus_in)
        e.bind("<FocusOut>", on_focus_out)
    return e


def action_button(parent, text, color, command, width=None):
    kw = {}
    if width:
        kw["width"] = width
    f = tk.Frame(parent, bg=color, cursor="hand2", **kw)
    lbl = tk.Label(f, text=text, font=FONT_BTN, fg=BG, bg=color,
                   pady=7, padx=12, anchor="center")
    lbl.pack(fill="both", expand=True)
    def lighten(c, amt=30):
        r, g, b = hex_to_rgb(c)
        return f"#{min(r+amt,255):02x}{min(g+amt,255):02x}{min(b+amt,255):02x}"
    for w in (f, lbl):
        w.bind("<ButtonPress-1>", lambda e, c=command: c())
        w.bind("<Enter>",  lambda e, b=f, l=lbl, c=color: (b.config(bg=lighten(c)), l.config(bg=lighten(c))))
        w.bind("<Leave>",  lambda e, b=f, l=lbl, c=color: (b.config(bg=c), l.config(bg=c)))
    return f


# ── String pair row ─────────────────────────────────────────────────────────

class StringPairRow(tk.Frame):
    _index_counter = 0

    def __init__(self, parent, on_remove, index, **kw):
        super().__init__(parent, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER, **kw)
        self._on_remove = on_remove
        self._index = index

        # row number badge
        badge = tk.Label(self, text=f"{index:02d}", font=FONT_SMALL,
                         fg=ACCENT2, bg=CARD, width=3)
        badge.pack(side="left", padx=(10, 6), pady=10)

        inner = tk.Frame(self, bg=CARD)
        inner.pack(side="left", fill="both", expand=True, pady=8, padx=(0, 6))

        # Original string
        tk.Label(inner, text="ORIGINAL STRING", font=FONT_LABEL,
                 fg=MUTED, bg=CARD, anchor="w").pack(fill="x")
        self.orig_entry = styled_entry(inner, placeholder="Exact string as it appears in-game…")
        self.orig_entry.pack(fill="x", pady=(2, 6))

        # New string
        tk.Label(inner, text="REPLACEMENT STRING", font=FONT_LABEL,
                 fg=MUTED, bg=CARD, anchor="w").pack(fill="x")
        self.new_entry = styled_entry(inner, placeholder="Leave blank to replace with empty string…", fg=MUTED)
        self.new_entry.pack(fill="x", pady=(2, 0))

        # Remove button
        rm = tk.Label(self, text="✕", font=("Courier New", 12, "bold"),
                      fg=MUTED, bg=CARD, cursor="hand2", padx=10)
        rm.pack(side="right", pady=10)
        rm.bind("<Enter>",         lambda e: rm.config(fg=DANGER))
        rm.bind("<Leave>",         lambda e: rm.config(fg=MUTED))
        rm.bind("<ButtonPress-1>", lambda e: self._on_remove(self))

    def get_values(self):
        orig = self.orig_entry.get()
        if hasattr(self.orig_entry, '_has_placeholder') and self.orig_entry._has_placeholder:
            orig = ""
        new  = self.new_entry.get()
        if hasattr(self.new_entry, '_has_placeholder') and self.new_entry._has_placeholder:
            new = ""
        return orig, new


# ── Mod section (one named mod with N string pairs) ──────────────────────────

class ModSection(tk.Frame):
    def __init__(self, parent, on_remove_section, index, **kw):
        super().__init__(parent, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER, **kw)
        self._on_remove_section = on_remove_section
        self._index = index
        self._string_rows = []
        self._build()

    def _build(self):
        # Section header
        hdr = tk.Frame(self, bg="#0f1219", highlightthickness=0)
        hdr.pack(fill="x")

        left = tk.Frame(hdr, bg="#0f1219")
        left.pack(side="left", fill="x", expand=True, padx=14, pady=10)

        tk.Label(left, text=f"MOD #{self._index}", font=FONT_LABEL,
                 fg=ACCENT, bg="#0f1219", anchor="w").pack(side="left")

        rm_lbl = tk.Label(hdr, text="✕  REMOVE MOD", font=FONT_SMALL,
                          fg=MUTED, bg="#0f1219", cursor="hand2", padx=14)
        rm_lbl.pack(side="right", pady=10)
        rm_lbl.bind("<Enter>",         lambda e: rm_lbl.config(fg=DANGER))
        rm_lbl.bind("<Leave>",         lambda e: rm_lbl.config(fg=MUTED))
        rm_lbl.bind("<ButtonPress-1>", lambda e: self._on_remove_section(self))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=PANEL)
        body.pack(fill="x", padx=14, pady=10)

        # Mod name
        tk.Label(body, text="MOD NAME", font=FONT_LABEL,
                 fg=MUTED, bg=PANEL, anchor="w").pack(fill="x")
        self.name_entry = styled_entry(body, placeholder="e.g.  infinite_ammo  or  unlock_all_levels")
        self.name_entry.pack(fill="x", pady=(2, 4))

        # Description (optional)
        tk.Label(body, text="DESCRIPTION  (optional)", font=FONT_LABEL,
                 fg=MUTED, bg=PANEL, anchor="w").pack(fill="x")
        self.desc_entry = styled_entry(body, placeholder="Short description shown in the patcher UI…")
        self.desc_entry.pack(fill="x", pady=(2, 10))

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(0, 10))

        # String pairs container
        self._pairs_frame = tk.Frame(body, bg=PANEL)
        self._pairs_frame.pack(fill="x")

        # Add first pair by default
        self._add_string_pair()

        # Add string button
        add_row = tk.Frame(body, bg=PANEL)
        add_row.pack(fill="x", pady=(8, 0))
        add_btn = action_button(add_row, "+ ADD STRING PAIR", CARD, self._add_string_pair)
        add_btn.pack(side="left")
        tk.Label(add_row, text="Add another original→new string replacement",
                 font=FONT_SMALL, fg=MUTED, bg=PANEL, padx=10).pack(side="left")

    def _add_string_pair(self):
        idx = len(self._string_rows) + 1
        row = StringPairRow(self._pairs_frame, self._remove_string_pair, idx)
        row.pack(fill="x", pady=3)
        self._string_rows.append(row)

    def _remove_string_pair(self, row):
        if len(self._string_rows) <= 1:
            messagebox.showwarning("Can't Remove",
                                   "Each mod needs at least one string pair.")
            return
        row.pack_forget()
        row.destroy()
        self._string_rows.remove(row)

    def get_mod_name(self):
        val = self.name_entry.get()
        if hasattr(self.name_entry, '_has_placeholder') and self.name_entry._has_placeholder:
            return ""
        return val.strip()

    def get_description(self):
        val = self.desc_entry.get()
        if hasattr(self.desc_entry, '_has_placeholder') and self.desc_entry._has_placeholder:
            return ""
        return val.strip()

    def get_strings(self):
        pairs = []
        for row in self._string_rows:
            orig, new = row.get_values()
            pairs.append((orig, new))
        return pairs


# ── Main app ────────────────────────────────────────────────────────────────

class ModCreatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mod Creator — Metadata Patcher Toolkit")
        self.configure(bg=BG)
        self.geometry("760x820")
        self.minsize(640, 580)
        self.resizable(True, True)

        self._mod_sections = []
        self._pulse_id = None
        self._section_counter = 0

        self._build_ui()
        self._pulse_title()

    def _build_ui(self):
        self._build_header()
        self._build_game_info()
        self._build_mods_area()
        self._build_footer()

    def _build_header(self):
        hdr = tk.Frame(self, bg=PANEL)
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=ACCENT, height=3).pack(fill="x")

        inner = tk.Frame(hdr, bg=PANEL)
        inner.pack(fill="x", padx=28, pady=(14, 12))

        self.title_lbl = tk.Label(inner, text="Mod Creator",
                                   font=FONT_TITLE, fg=ACCENT, bg=PANEL)
        self.title_lbl.pack(anchor="w")
        tk.Label(inner, text="Metadata Patcher Toolkit  ·  JSON Mod File Generator",
                 font=FONT_SUB, fg=MUTED, bg=PANEL).pack(anchor="w")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

    def _build_game_info(self):
        section = tk.Frame(self, bg=PANEL, highlightthickness=1,
                           highlightbackground=BORDER)
        section.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(section, text="GAME PACKAGE", font=FONT_LABEL,
                 fg=MUTED, bg=PANEL, anchor="w",
                 padx=14, pady=10).pack(fill="x")
        tk.Frame(section, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(section, bg=PANEL)
        body.pack(fill="x", padx=14, pady=12)

        # Warning banner
        warn = tk.Frame(body, bg="#1a1000", highlightthickness=1,
                        highlightbackground=WARNING)
        warn.pack(fill="x", pady=(0, 12))
        tk.Label(warn,
                 text="⚠  The package name must exactly match what the patcher app shows.\n"
                      "    It follows the format:  com.CompanyName.AppName\n"
                      "    Wrong name = mod file won't load. Copy it directly from the app.",
                 font=FONT_SMALL, fg=WARNING, bg="#1a1000",
                 anchor="w", padx=12, pady=8, justify="left").pack(fill="x")

        tk.Label(body, text="PACKAGE NAME", font=FONT_LABEL,
                 fg=MUTED, bg=PANEL, anchor="w").pack(fill="x")
        self.pkg_entry = styled_entry(body, placeholder="com.CompanyName.AppName")
        self.pkg_entry.pack(fill="x", pady=(2, 0))

    def _build_mods_area(self):
        # Scrollable area for mod sections
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        header_row = tk.Frame(outer, bg=BG)
        header_row.pack(fill="x", pady=(0, 8))

        tk.Label(header_row, text="MODS", font=FONT_LABEL,
                 fg=MUTED, bg=BG).pack(side="left")

        add_mod_btn = action_button(header_row, "+ ADD MOD", ACCENT2, self._add_mod_section)
        add_mod_btn.pack(side="right")

        # Canvas + scrollbar
        canvas_frame = tk.Frame(outer, bg=BG)
        canvas_frame.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(canvas_frame, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(canvas_frame, orient="vertical",
                                  command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._mods_inner = tk.Frame(self._canvas, bg=BG)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._mods_inner, anchor="nw"
        )
        self._mods_inner.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width))
        self.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Add first mod by default
        self._add_mod_section()

    def _build_footer(self):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        foot = tk.Frame(self, bg=PANEL)
        foot.pack(fill="x", padx=20, pady=10)

        save_btn = action_button(foot, "⬇  SAVE MOD FILE", SUCCESS, self._save)
        save_btn.pack(side="right", padx=(8, 0))

        clear_btn = action_button(foot, "↺  CLEAR ALL", MUTED, self._clear_all)
        clear_btn.pack(side="right")

        self._status_lbl = tk.Label(foot, text="Ready", font=FONT_SMALL,
                                     fg=MUTED, bg=PANEL, anchor="w")
        self._status_lbl.pack(side="left", fill="x", expand=True)

    # ── Mod section management ──────────────────────────────────────────────

    def _add_mod_section(self):
        self._section_counter += 1
        sec = ModSection(self._mods_inner, self._remove_mod_section,
                         self._section_counter)
        sec.pack(fill="x", pady=(0, 10))
        self._mod_sections.append(sec)
        self._canvas.after(50, lambda: self._canvas.yview_moveto(1.0))
        self._set_status(f"{len(self._mod_sections)} mod(s) defined", MUTED)

    def _remove_mod_section(self, sec):
        if len(self._mod_sections) <= 1:
            messagebox.showwarning("Can't Remove",
                                   "You need at least one mod section.")
            return
        sec.pack_forget()
        sec.destroy()
        self._mod_sections.remove(sec)
        self._set_status(f"{len(self._mod_sections)} mod(s) defined", MUTED)

    # ── Save logic ──────────────────────────────────────────────────────────

    def _get_package(self):
        val = self.pkg_entry.get()
        if hasattr(self.pkg_entry, '_has_placeholder') and self.pkg_entry._has_placeholder:
            return ""
        return val.strip()

    def _save(self):
        # Validate package name
        pkg = self._get_package()
        if not pkg:
            messagebox.showerror("Missing Package", "Enter the game's package name.")
            self.pkg_entry.focus_set()
            return

        pkg_pattern = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$')
        if not pkg_pattern.match(pkg):
            messagebox.showerror(
                "Invalid Package Name",
                f"'{pkg}' doesn't look like a valid package name.\n\n"
                "Expected format:  com.CompanyName.AppName\n"
                "Check the main patcher app for the exact name."
            )
            return

        # Build JSON
        output = {}
        errors = []

        for i, sec in enumerate(self._mod_sections, 1):
            mod_name = sec.get_mod_name()
            if not mod_name:
                errors.append(f"Mod #{i}: name is empty.")
                continue
            if mod_name in output:
                errors.append(f"Mod #{i}: name '{mod_name}' is already used.")
                continue

            strings = sec.get_strings()
            valid_strings = []
            for j, (orig, new) in enumerate(strings, 1):
                if not orig:
                    errors.append(f"Mod #{i} '{mod_name}', string #{j}: original is empty.")
                    continue
                valid_strings.append({"original": orig, "new": new})

            if not valid_strings:
                errors.append(f"Mod #{i} '{mod_name}': no valid string pairs.")
                continue

            desc = sec.get_description() or "No Description Provided"
            entry = {"description": desc, "strings": valid_strings}

            output[mod_name] = entry

        if errors:
            messagebox.showerror(
                "Validation Errors",
                "Fix these issues before saving:\n\n" + "\n".join(f"• {e}" for e in errors)
            )
            return

        if not output:
            messagebox.showerror("No Mods", "No valid mods to save.")
            return

        # Derive default filename from package name
        game_short = pkg.split(".")[-1]
        default_name = f"{game_short}.json"

        path = filedialog.asksaveasfilename(
            title="Save Mod File",
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON mod file", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=4, ensure_ascii=False)
        except OSError as e:
            messagebox.showerror("Save Failed", str(e))
            return

        mod_count = len(output)
        string_count = sum(len(v["strings"]) for v in output.values())
        self._set_status(
            f"Saved: {os.path.basename(path)}  ({mod_count} mod(s), {string_count} string(s))",
            SUCCESS
        )
        messagebox.showinfo(
            "Saved ✓",
            f"Mod file saved successfully!\n\n"
            f"File:    {os.path.basename(path)}\n"
            f"Package: {pkg}\n"
            f"Mods:    {mod_count}\n"
            f"Strings: {string_count}\n\n"
            f"Drop this file into the  mods/  folder\n"
            f"next to the patcher app."
        )

    def _clear_all(self):
        if not messagebox.askyesno("Clear All", "Reset everything? This cannot be undone."):
            return
        # Clear package
        self.pkg_entry.delete(0, "end")
        self.pkg_entry.insert(0, self.pkg_entry._placeholder)
        self.pkg_entry.config(fg=MUTED)
        self.pkg_entry._has_placeholder = True

        # Remove all sections
        for sec in list(self._mod_sections):
            sec.pack_forget()
            sec.destroy()
        self._mod_sections.clear()
        self._section_counter = 0

        # Add fresh section
        self._add_mod_section()
        self._set_status("Cleared.", MUTED)

    def _set_status(self, msg, color=MUTED):
        if hasattr(self, "_status_lbl"):
            self._status_lbl.config(text=msg, fg=color)

    def _pulse_title(self):
        c1 = hex_to_rgb(ACCENT)
        c2 = hex_to_rgb(ACCENT2)
        t  = (time.time() % 3) / 3
        tt = t if t < 0.5 else 1 - t
        color = lerp_color(c1, c2, tt * 2)
        self.title_lbl.config(fg=color)
        self._pulse_id = self.after(50, self._pulse_title)


if __name__ == "__main__":
    app = ModCreatorApp()
    app.mainloop()