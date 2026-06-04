import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import struct
import subprocess
import os
import sys
import shlex
import re
import json

moddable_games = []
base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)

def get_adb_path():
    return os.path.join(base_path, "platform-tools", "adb.exe")

class Editor:
    class StringLiteral:
        def __init__(self, length=0, offset=0):
            self.Length = length
            self.Offset = offset

    def __init__(self, full_name):
        self.reader = open(full_name, "rb")
        self.stringLiteralOffset = 0
        self.stringLiteralCount = 0
        self.DataInfoPosition = 0
        self.stringLiteralDataOffset = 0
        self.stringLiteralDataCount = 0
        self.stringLiterals = []
        self.strBytes = []
        self.ReadHeader()
        self.ReadLiteral()
        self.ReadStrByte()

    def ReadHeader(self):
        vansity = struct.unpack("<I", self.reader.read(4))[0]
        if vansity != 0xFAB11BAF:
            print("Error: Invalid metadata file signature")
            raise Exception("Flag check failed")
        version = struct.unpack("<i", self.reader.read(4))[0]
        self.stringLiteralOffset = struct.unpack("<I", self.reader.read(4))[0]
        self.stringLiteralCount = struct.unpack("<I", self.reader.read(4))[0]
        self.DataInfoPosition = self.reader.tell()
        self.stringLiteralDataOffset = struct.unpack("<I", self.reader.read(4))[0]
        self.stringLiteralDataCount = struct.unpack("<I", self.reader.read(4))[0]

    def ReadLiteral(self):
        self.reader.seek(self.stringLiteralOffset)
        for _ in range(self.stringLiteralCount // 8):
            length = struct.unpack("<I", self.reader.read(4))[0]
            offset = struct.unpack("<I", self.reader.read(4))[0]
            self.stringLiterals.append(self.StringLiteral(length, offset))

    def ReadStrByte(self):
        for lit in self.stringLiterals:
            self.reader.seek(self.stringLiteralDataOffset + lit.Offset)
            self.strBytes.append(self.reader.read(lit.Length))

    def WriteToNewFile(self, file_name):
        self.reader.seek(0)
        data = self.reader.read()
        with open(file_name, "wb") as writer:
            writer.write(data)

            count = 0
            for i, lit in enumerate(self.stringLiterals):
                lit.Offset = count
                lit.Length = len(self.strBytes[i])
                writer.seek(self.stringLiteralOffset + i * 8)
                writer.write(struct.pack("<I I", lit.Length, lit.Offset))
                count += lit.Length

            tmp = (self.stringLiteralDataOffset + count) % 4
            if tmp != 0:
                count += 4 - tmp

            if count > self.stringLiteralDataCount:
                if self.stringLiteralDataOffset + self.stringLiteralDataCount < len(data):
                    self.stringLiteralDataOffset = len(data)
            self.stringLiteralDataCount = count

            writer.seek(self.stringLiteralDataOffset)
            for b in self.strBytes:
                writer.write(b)

            writer.seek(self.DataInfoPosition)
            writer.write(struct.pack("<I I", self.stringLiteralDataOffset, self.stringLiteralDataCount))

    def Dispose(self):
        if self.reader:
            self.reader.close()

    def ReplaceStrings(self, replacements: dict):
        decoded = []
        for b in self.strBytes:
            try:
                decoded.append(b.decode('utf-8'))
            except UnicodeDecodeError:
                decoded.append(None)
        for key, new_val in replacements.items():
            found = False
            for i, s in enumerate(decoded):
                if s is None:
                    continue
                if s == key:
                    new = new_val or ""
                    self.strBytes[i] = new.encode('utf-8')
                    decoded[i] = new
                    found = True
                    break
            if not found:
                print(f"Error: String not found - '{key}'")

    def EditMetadata(self, replacements: dict, output_path: str):
        self.ReplaceStrings(replacements)
        self.WriteToNewFile(output_path)
        self.Dispose()


def run_adb(args, timeout=10):
    adb_path = get_adb_path()
    try:
        result = subprocess.run(
            [adb_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out", -1
    except Exception as e:
        return False, "", str(e), -1


def run_adb_cmd(command):
    return run_adb(shlex.split(command))


def list_directory_entries(path):
    success, out, err, code = run_adb(["shell", "ls", "-1", path])
    if not success:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def check_device_connected():
    success, out, err, code = run_adb(["devices"])
    if not success:
        return "disconnected"
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    device_lines = lines[1:]
    if any("\tdevice" in l for l in device_lines):
        return "connected"
    if any("unauthorized" in l for l in device_lines):
        return "unauthorized"
    return "disconnected"


def list_unity_games():
    global moddable_games

    base = "sdcard/Android/data"
    apps = list_directory_entries(base)

    unity_games = []
    for app in apps:
        files = list_directory_entries(f"{base}/{app}")
        if "files" not in files:
            continue
        il2cpp = list_directory_entries(f"{base}/{app}/files/il2cpp")
        if "Metadata" in il2cpp:
            unity_games.append(app)

    moddable_games = unity_games
    return unity_games


def get_version_code(game):
    success, out, err, code = run_adb_cmd(f"shell dumpsys package {game}")
    if not success:
        return None
    match = re.search(r"versionCode=(\d+)", out)
    return int(match.group(1)) if match else None


def update_game_data(game):
    data_dir = os.path.join(base_path, "game_data", game)
    os.makedirs(data_dir, exist_ok=True)

    data_path = os.path.join(data_dir, "data.json")
    meta_dir = os.path.join(data_dir, "original_metadata")
    os.makedirs(meta_dir, exist_ok=True)

    if not os.path.exists(data_path):
        with open(data_path, "w") as f:
            json.dump({}, f)

    with open(data_path, "r") as f:
        data = json.load(f)

    current_version = get_version_code(game)
    old_version = data.get("version_code")
    meta_path = os.path.join(meta_dir, "global-metadata.dat")

    if current_version != old_version or not os.path.exists(meta_path):
        if os.path.exists(meta_path):
            os.remove(meta_path)

        success, out, err, code = run_adb(
            ["pull",
             f"/sdcard/Android/data/{game}/files/il2cpp/Metadata/global-metadata.dat",
             meta_path]
        )

        print(f"[DEBUG] Expected file at: {meta_path}")
        print(f"[DEBUG] File exists after pull: {os.path.exists(meta_path)}")
        print(f"[DEBUG] meta_dir contents: {os.listdir(meta_dir)}")

        if not os.path.exists(meta_path):
            print(f"[update_game_data] ADB pull failed.\n  stdout: {out}\n  stderr: {err}")
            return False

        print(f"[update_game_data] Pull successful.")

    if current_version is not None:
        data["version_code"] = current_version

    with open(data_path, "w") as f:
        json.dump(data, f, indent=4)

    return True


def mod_metadata(game, *mods):
    original_metadata = os.path.join(
        base_path, "game_data", game, "original_metadata", "global-metadata.dat"
    )

    if not os.path.exists(original_metadata):
        raise FileNotFoundError(
            f"Original metadata not cached for '{game}'.\n"
            f"Scan the device first so the file can be pulled."
        )

    instructions = get_mod_instructions(game, *mods)

    modded_metadata = os.path.join(
        base_path, "game_data", game, "modded_metadata", "global-metadata.dat"
    )
    try:
        os.makedirs(os.path.dirname(modded_metadata), exist_ok=True)
    except OSError as e:
        raise OSError(f"Cannot create modded_metadata directory: {e}") from e

    ed = Editor(original_metadata)
    try:
        ed.EditMetadata(instructions, modded_metadata)
    except Exception as e:
        ed.Dispose()
        raise RuntimeError(f"Failed to write modded metadata: {e}") from e

    if not os.path.exists(modded_metadata):
        raise RuntimeError("Modded metadata file was not created. Something went wrong during editing.")

    device_path = (
        f"/sdcard/Android/data/{game}/files/il2cpp/Metadata/global-metadata.dat"
    )
    success, out, err, code = run_adb(["push", modded_metadata, device_path])
    if not success:
        raise RuntimeError(
            f"ADB push failed (exit {code}).\n"
            f"stdout: {out}\nstderr: {err}"
        )


def restore_original_metadata(game):
    original_metadata = os.path.join(
        base_path, "game_data", game, "original_metadata", "global-metadata.dat"
    )

    if not os.path.exists(original_metadata):
        raise FileNotFoundError(
            f"No original metadata cached for '{game}'.\n"
            f"Scan the device first so the file can be pulled."
        )

    device_path = (
        f"/sdcard/Android/data/{game}/files/il2cpp/Metadata/global-metadata.dat"
    )
    success, out, err, code = run_adb(["push", original_metadata, device_path])

    if not success:
        raise RuntimeError(
            f"ADB push failed (exit {code}).\n"
            f"stdout: {out}\nstderr: {err}"
        )

    return True

def get_mod_instructions_path(game):
    game_name = game.split(".")[-1]
    return os.path.join(base_path, "mods", f"{game_name}.json")

def has_mod_instructions(game):
    path = get_mod_instructions_path(game)
    if not os.path.exists(path):
        return False, f"No mod file found at: mods/{game.split('.')[-1]}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return False, "Mod file is not a valid JSON object."
        if not raw:
            return False, "Mod file exists but contains no mod entries."
        for name, data in raw.items():
            if not isinstance(data, dict):
                continue
            if "strings" in data and isinstance(data["strings"], list) and data["strings"]:
                return True, None
            if "original" in data and isinstance(data["original"], str):
                return True, None
        return False, "Mod file has no valid entries (need 'strings' array or 'original' key)."
    except json.JSONDecodeError as e:
        return False, f"Mod file has invalid JSON: {e}"
    except OSError as e:
        return False, f"Cannot read mod file: {e}"


def load_mod_names(game):
    path = get_mod_instructions_path(game)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return []
        result = []
        for name, data in raw.items():
            if not isinstance(data, dict):
                continue
            has_strings = (
                ("strings" in data and isinstance(data["strings"], list) and data["strings"]) or
                ("original" in data and isinstance(data["original"], str))
            )
            if not has_strings:
                continue
            desc = data.get("description") or data.get("desc") or None
            result.append((name, desc))
        return result
    except Exception:
        return []


def get_mod_instructions(game, *mods):
    path = get_mod_instructions_path(game)

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No mod instructions found for '{game}'.\n"
            f"Expected: mods/{game.split('.')[-1]}.json"
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Mod file for '{game}' contains invalid JSON: {e}") from e
    except OSError as e:
        raise OSError(f"Cannot read mod file for '{game}': {e}") from e

    if not isinstance(raw, dict):
        raise TypeError(f"Mod file for '{game}' must be a JSON object, got {type(raw).__name__}.")

    replacements = {}
    skipped = []

    for mod_name, mod_data in raw.items():
        if mods and mod_name not in mods:
            continue
        if not isinstance(mod_data, dict):
            skipped.append(f"  • '{mod_name}': not a dict, skipping")
            continue

        if "strings" in mod_data:
            entries = mod_data["strings"]
            if not isinstance(entries, list) or not entries:
                skipped.append(f"  • '{mod_name}': 'strings' must be a non-empty list, skipping")
                continue
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    skipped.append(f"  • '{mod_name}[{idx}]': entry is not a dict, skipping")
                    continue
                original = entry.get("original")
                if not isinstance(original, str):
                    skipped.append(f"  • '{mod_name}[{idx}]': missing or invalid 'original', skipping")
                    continue
                replacements[original] = entry.get("new") or ""

        elif "original" in mod_data:
            original = mod_data.get("original")
            if not isinstance(original, str):
                skipped.append(f"  • '{mod_name}': 'original' must be a string, skipping")
                continue
            replacements[original] = mod_data.get("new") or ""

        else:
            skipped.append(f"  • '{mod_name}': missing 'strings' or 'original' key, skipping")

    if skipped:
        print(f"[get_mod_instructions] Skipped entries for '{game}':\n" + "\n".join(skipped))

    if not replacements:
        raise ValueError(
            f"No valid mod entries found for '{game}' in the mod file. "
            f"Check that each entry has a 'strings' array or an 'original' key."
        )

    return replacements


BG          = "#0d0f14"
PANEL       = "#13161e"
CARD        = "#1a1e2a"
CARD_HOVER  = "#1f2538"
ACCENT      = "#00e5ff"
ACCENT2     = "#7b2fff"
SUCCESS     = "#00e676"
WARNING     = "#ffab00"
DANGER      = "#ff3d71"
TEXT        = "#e8eaf6"
MUTED       = "#5c6080"
BORDER      = "#252a3a"

FONT_TITLE  = ("Courier New", 26, "bold")
FONT_SUB    = ("Courier New", 10)
FONT_LABEL  = ("Courier New", 9, "bold")
FONT_CARD   = ("Courier New", 11, "bold")
FONT_SMALL  = ("Courier New", 8)
FONT_BTN    = ("Courier New", 9, "bold")
FONT_MONO   = ("Courier New", 9)

SCAN_FRAMES = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def lerp_color(c1, c2, t):
    r = int(c1[0] + (c2[0]-c1[0])*t)
    g = int(c1[1] + (c2[1]-c1[1])*t)
    b = int(c1[2] + (c2[2]-c1[2])*t)
    return f"#{r:02x}{g:02x}{b:02x}"


class ScanButton(tk.Canvas):
    def __init__(self, parent, text, command, **kw):
        super().__init__(parent, bg=PANEL, highlightthickness=0,
                         width=200, height=42, cursor="hand2", **kw)
        self._text      = text
        self._command   = command
        self._animating = False
        self._frame     = 0
        self._hover     = False
        self._draw()
        self.bind("<Enter>",         self._on_enter)
        self.bind("<Leave>",         self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)

    def _draw(self, label=None, bg=CARD, fg=ACCENT):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        r = 6
        self.create_arc(0,     0,     r*2,   r*2,   start=90,  extent=90, fill=bg, outline=bg)
        self.create_arc(w-r*2, 0,     w,     r*2,   start=0,   extent=90, fill=bg, outline=bg)
        self.create_arc(0,     h-r*2, r*2,   h,     start=180, extent=90, fill=bg, outline=bg)
        self.create_arc(w-r*2, h-r*2, w,     h,     start=270, extent=90, fill=bg, outline=bg)
        self.create_rectangle(r, 0, w-r, h, fill=bg, outline=bg)
        self.create_rectangle(0, r, w, h-r, fill=bg, outline=bg)
        border = fg if self._hover else BORDER
        self.create_rectangle(0, 0, w-1, h-1, outline=border, width=1)
        txt = label or self._text
        self.create_text(w//2, h//2, text=txt, fill=fg, font=FONT_BTN, anchor="center")

    def _on_enter(self, e):
        self._hover = True
        if not self._animating:
            self._draw(bg=CARD_HOVER)

    def _on_leave(self, e):
        self._hover = False
        if not self._animating:
            self._draw()

    def _on_press(self, e):
        if not self._animating:
            self._command()

    def start_spin(self):
        self._animating = True
        self._spin()

    def _spin(self):
        if not self._animating:
            return
        self._frame = (self._frame + 1) % len(SCAN_FRAMES)
        self._draw(label=f"{SCAN_FRAMES[self._frame]}  Scanning…", bg=CARD, fg=WARNING)
        self.after(80, self._spin)

    def stop_spin(self):
        self._animating = False
        self._draw()


class GameCard(tk.Frame):
    def __init__(self, parent, pkg_name, on_select, **kw):
        super().__init__(parent, bg=CARD, cursor="hand2",
                         highlightthickness=1, highlightbackground=BORDER, **kw)
        self.pkg       = pkg_name
        self.on_select = on_select
        self._selected = False

        ico = tk.Label(self, text="⬡", font=("Courier New", 18), bg=CARD, fg=ACCENT2)
        ico.pack(side="left", padx=(14, 8), pady=12)

        info = tk.Frame(self, bg=CARD)
        info.pack(side="left", fill="both", expand=True, pady=10)

        parts = pkg_name.split(".")
        short = parts[-1] if parts else pkg_name
        self.lbl_name = tk.Label(info, text=short, font=FONT_CARD, fg=TEXT, bg=CARD, anchor="w")
        self.lbl_name.pack(fill="x")
        self.lbl_pkg  = tk.Label(info, text=pkg_name, font=FONT_SMALL, fg=MUTED, bg=CARD, anchor="w")
        self.lbl_pkg.pack(fill="x")

        for w in (self, ico, info, self.lbl_name, self.lbl_pkg):
            w.bind("<Enter>",         lambda e: self._hover(True))
            w.bind("<Leave>",         lambda e: self._hover(False))
            w.bind("<ButtonPress-1>", lambda e: self.on_select(self.pkg))

    def _hover(self, on):
        if self._selected:
            return
        c  = CARD_HOVER if on else CARD
        bc = ACCENT if on else BORDER
        self._recolor(c, bc)

    def _recolor(self, bg, border):
        self.config(bg=bg, highlightbackground=border)
        for child in self.winfo_children():
            try:
                child.config(bg=bg)
                for sub in child.winfo_children():
                    try:
                        sub.config(bg=bg)
                    except Exception:
                        pass
            except Exception:
                pass

    def set_selected(self, val):
        self._selected = val
        if val:
            self._recolor(CARD_HOVER, ACCENT)
        else:
            self._recolor(CARD, BORDER)


class ModCheckRow(tk.Frame):
    def __init__(self, parent, mod_name, description=None, **kw):
        super().__init__(parent, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER, **kw)
        self.mod_name = mod_name
        self._var = tk.BooleanVar(value=True)

        self._cb_canvas = tk.Canvas(self, width=18, height=18, bg=CARD,
                                     highlightthickness=0, cursor="hand2")
        self._cb_canvas.pack(side="left", padx=(10, 8), pady=10)
        self._draw_check()

        text_frame = tk.Frame(self, bg=CARD)
        text_frame.pack(side="left", fill="both", expand=True, pady=8)

        self._name_lbl = tk.Label(text_frame, text=mod_name, font=FONT_BTN,
                                   fg=TEXT, bg=CARD, anchor="w")
        self._name_lbl.pack(fill="x")

        if description:
            self._desc_lbl = tk.Label(text_frame, text=description, font=FONT_SMALL,
                                       fg=MUTED, bg=CARD, anchor="w", wraplength=220,
                                       justify="left")
            self._desc_lbl.pack(fill="x")

        for w in (self, self._cb_canvas, text_frame, self._name_lbl):
            w.bind("<ButtonPress-1>", self._toggle)
        if description:
            self._desc_lbl.bind("<ButtonPress-1>", self._toggle)

    def _draw_check(self):
        self._cb_canvas.delete("all")
        checked = self._var.get()
        bg = ACCENT if checked else CARD
        border = ACCENT if checked else MUTED
        self._cb_canvas.create_rectangle(1, 1, 17, 17, fill=bg, outline=border, width=1)
        if checked:
            self._cb_canvas.create_line(3, 9, 7, 13, fill=BG, width=2)
            self._cb_canvas.create_line(7, 13, 15, 5, fill=BG, width=2)

    def _toggle(self, e=None):
        self._var.set(not self._var.get())
        self._draw_check()
        checked = self._var.get()
        fg = TEXT if checked else MUTED
        self._name_lbl.config(fg=fg)
        border = BORDER if not checked else ACCENT
        self.config(highlightbackground=border)

    def is_checked(self):
        return self._var.get()

    def set_checked(self, val):
        self._var.set(val)
        self._draw_check()
        fg = TEXT if val else MUTED
        self._name_lbl.config(fg=fg)
        self.config(highlightbackground=ACCENT if val else BORDER)


class StatusBar(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=PANEL, **kw)
        self.dot = tk.Label(self, text="●", font=("Courier New", 9), fg=SUCCESS, bg=PANEL)
        self.dot.pack(side="left", padx=(14, 4))
        self.msg = tk.Label(self, text="Ready", font=FONT_SMALL, fg=MUTED, bg=PANEL, anchor="w")
        self.msg.pack(side="left", fill="x", expand=True)
        self.ver = tk.Label(self, text="v0.1.0-alpha", font=FONT_SMALL, fg=MUTED, bg=PANEL)
        self.ver.pack(side="right", padx=14)

    def set(self, text, color=MUTED, dot=SUCCESS):
        self.msg.config(text=text, fg=color)
        self.dot.config(fg=dot)


class Main_App(tk.Tk):
    def __init__(self):
        super().__init__()
        try:
            self.iconbitmap(os.path.join(base_path, "icon.ico"))
        except Exception:
            pass
        self.title("i need a name for this — Metadata Patcher")
        self.configure(bg=BG)
        self.geometry("820x640")
        self.minsize(700, 520)
        self.resizable(True, True)

        os.makedirs(os.path.join(base_path, "mods"), exist_ok=True)

        self._cards        = {}
        self._selected_pkg = None
        self._pulse_id     = None
        self._mod_rows     = []

        self._build_ui()
        self._pulse_title()
        self.after(200, self._scan)

    def _build_ui(self):
        self._build_header()
        self._build_body()
        self._build_statusbar()

    def _build_header(self):
        hdr = tk.Frame(self, bg=PANEL, pady=0)
        hdr.pack(fill="x")

        tk.Frame(hdr, bg=ACCENT, height=3).pack(fill="x")

        inner = tk.Frame(hdr, bg=PANEL)
        inner.pack(fill="x", padx=28, pady=(16, 14))

        left = tk.Frame(inner, bg=PANEL)
        left.pack(side="left")

        self.title_lbl = tk.Label(left, text="i need a name for this",
                                   font=FONT_TITLE, fg=ACCENT, bg=PANEL)
        self.title_lbl.pack(anchor="w")

        tk.Label(left, text="Metadata Patcher",
                 font=FONT_SUB, fg=MUTED, bg=PANEL).pack(anchor="w")

        right = tk.Frame(inner, bg=PANEL)
        right.pack(side="right", anchor="n")

        self.scan_btn = ScanButton(right, "⌕  Scan Device", self._scan)
        self.scan_btn.pack(pady=4)

        self.adb_lbl = tk.Label(right, text="ADB: disconnected",
                                 font=FONT_SMALL, fg=MUTED, bg=PANEL)
        self.adb_lbl.pack()

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        tk.Label(left, text="DETECTED GAMES",
                 font=FONT_LABEL, fg=MUTED, bg=PANEL,
                 anchor="w", padx=14, pady=10).pack(fill="x")
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x")

        self.game_frame = tk.Frame(left, bg=PANEL)
        self.game_frame.pack(fill="both", expand=True)

        self.placeholder = tk.Label(
            self.game_frame,
            text="No games detected.\nConnect a device and scan.",
            font=FONT_MONO, fg=MUTED, bg=PANEL, justify="center"
        )
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")

        self._list_canvas = tk.Canvas(self.game_frame, bg=PANEL, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.game_frame, orient="vertical",
                                  command=self._list_canvas.yview)
        self._list_canvas.configure(yscrollcommand=scrollbar.set)

        self._inner_list = tk.Frame(self._list_canvas, bg=PANEL)
        self._canvas_window = self._list_canvas.create_window(
            (0, 0), window=self._inner_list, anchor="nw"
        )
        self._inner_list.bind("<Configure>", self._on_list_configure)
        self._list_canvas.bind("<Configure>", self._on_canvas_configure)

        right = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        right.grid(row=0, column=1, sticky="nsew")

        tk.Label(right, text="ACTIONS",
                 font=FONT_LABEL, fg=MUTED, bg=PANEL,
                 anchor="w", padx=14, pady=10).pack(fill="x")
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x")

        self._detail_canvas = tk.Canvas(right, bg=PANEL, highlightthickness=0)
        detail_scroll = tk.Scrollbar(right, orient="vertical",
                                      command=self._detail_canvas.yview)
        self._detail_canvas.configure(yscrollcommand=detail_scroll.set)

        self.detail_pane = tk.Frame(self._detail_canvas, bg=PANEL)
        self._detail_window = self._detail_canvas.create_window(
            (0, 0), window=self.detail_pane, anchor="nw"
        )
        self.detail_pane.bind("<Configure>",
                               lambda e: self._detail_canvas.configure(
                                   scrollregion=self._detail_canvas.bbox("all")))
        self._detail_canvas.bind("<Configure>",
                                  lambda e: self._detail_canvas.itemconfig(
                                      self._detail_window, width=e.width))

        self._detail_canvas.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")

        self.bind_all("<MouseWheel>", self._on_mousewheel)

        self._build_detail_empty()

    def _build_statusbar(self):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self.status = StatusBar(self)
        self.status.pack(fill="x", ipady=6)

    def _widget_is_under(self, canvas, x_root, y_root):
        """Return True if the screen coordinate is inside the given canvas."""
        try:
            cx = canvas.winfo_rootx()
            cy = canvas.winfo_rooty()
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            return cx <= x_root < cx + cw and cy <= y_root < cy + ch
        except Exception:
            return False

    def _on_mousewheel(self, event):
        delta = int(-1 * (event.delta / 120))
        if self._widget_is_under(self._detail_canvas, event.x_root, event.y_root):
            self._detail_canvas.yview_scroll(delta, "units")
        elif self._widget_is_under(self._list_canvas, event.x_root, event.y_root):
            self._list_canvas.yview_scroll(delta, "units")

    def _build_detail_empty(self):
        for w in self.detail_pane.winfo_children():
            w.destroy()
        self._mod_rows = []
        tk.Label(self.detail_pane,
                 text="←  Select a game\nto see options",
                 font=("Courier New", 12), fg=MUTED, bg=PANEL,
                 justify="center").place(relx=0.5, rely=0.5, anchor="center")

    def _build_detail_game(self, pkg):
        for w in self.detail_pane.winfo_children():
            w.destroy()
        self._mod_rows = []

        parts = pkg.split(".")
        name  = parts[-1].replace("_", " ").title()

        tk.Label(self.detail_pane, text=name,
                 font=("Courier New", 16, "bold"), fg=TEXT, bg=PANEL,
                 anchor="w").pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(self.detail_pane, text=pkg,
                 font=FONT_SMALL, fg=MUTED, bg=PANEL, anchor="w").pack(fill="x", padx=18)

        tk.Frame(self.detail_pane, bg=BORDER, height=1).pack(fill="x", pady=12)

        mod_ok, mod_err = has_mod_instructions(pkg)

        if not mod_ok:
            warn = tk.Frame(self.detail_pane, bg="#1a0d00",
                             highlightthickness=1, highlightbackground=WARNING)
            warn.pack(fill="x", padx=18, pady=(0, 10))
            tk.Label(warn, text="⚠  No mod instructions found",
                     font=FONT_BTN, fg=WARNING, bg="#1a0d00",
                     anchor="w", padx=10, pady=6).pack(fill="x")
            tk.Label(warn, text=mod_err,
                     font=FONT_SMALL, fg=MUTED, bg="#1a0d00",
                     anchor="w", padx=10,
                     justify="left", wraplength=280).pack(fill="x", pady=(0, 8))
        else:
            mod_names = load_mod_names(pkg)

            hdr = tk.Frame(self.detail_pane, bg=PANEL)
            hdr.pack(fill="x", padx=18, pady=(0, 6))

            tk.Label(hdr, text="SELECT MODS", font=FONT_LABEL,
                     fg=MUTED, bg=PANEL, anchor="w").pack(side="left")

            tk.Label(hdr, text="ALL", font=FONT_SMALL, fg=ACCENT,
                     bg=PANEL, cursor="hand2").pack(side="right", padx=(6, 0))
            tk.Label(hdr, text="/", font=FONT_SMALL, fg=MUTED,
                     bg=PANEL).pack(side="right")
            none_lbl = tk.Label(hdr, text="NONE", font=FONT_SMALL, fg=ACCENT,
                                 bg=PANEL, cursor="hand2")
            none_lbl.pack(side="right")

            def select_all(e=None):
                for row in self._mod_rows:
                    row.set_checked(True)
            def select_none(e=None):
                for row in self._mod_rows:
                    row.set_checked(False)

            children = hdr.winfo_children()
            all_lbl = children[-1]
            all_lbl.bind("<ButtonPress-1>", select_all)
            none_lbl.bind("<ButtonPress-1>", select_none)

            rows_frame = tk.Frame(self.detail_pane, bg=PANEL)
            rows_frame.pack(fill="x", padx=18, pady=(0, 4))

            if mod_names:
                for mod_name, desc in mod_names:
                    row = ModCheckRow(rows_frame, mod_name, desc)
                    row.pack(fill="x", pady=2)
                    self._mod_rows.append(row)
            else:
                tk.Label(rows_frame, text="(mod file has no valid entries)",
                         font=FONT_SMALL, fg=MUTED, bg=PANEL).pack(anchor="w")

        tk.Frame(self.detail_pane, bg=BORDER, height=1).pack(fill="x", padx=18, pady=10)

        original_meta = os.path.join(
            base_path, "game_data", pkg, "original_metadata", "global-metadata.dat"
        )
        has_original = os.path.exists(original_meta)

        btn_frame = tk.Frame(self.detail_pane, bg=PANEL)
        btn_frame.pack(fill="x", padx=18, pady=(0, 4))

        apply_color = ACCENT if mod_ok else MUTED
        apply_label = "⚡  Apply Selected Mods" if mod_ok else "⚡  Apply Mods  (no mod file)"
        self._action_btn(
            btn_frame, apply_label, apply_color,
            (lambda: self._apply(pkg)) if mod_ok
            else lambda: self._log("Cannot apply mods: no valid mod instructions file found.")
        )

        restore_color = WARNING if has_original else MUTED
        restore_label = "↩  Restore Original" if has_original else "↩  Restore Original  (not cached)"
        self._action_btn(
            btn_frame, restore_label, restore_color,
            (lambda: self._restore(pkg)) if has_original
            else lambda: self._log(
                "Cannot restore: original metadata not cached. Run a scan first.")
        )

        tk.Frame(self.detail_pane, bg=BORDER, height=1).pack(fill="x", padx=18, pady=10)

        tk.Label(self.detail_pane, text="OUTPUT LOG",
                 font=FONT_LABEL, fg=MUTED, bg=PANEL, anchor="w",
                 padx=18).pack(fill="x")

        log_frame = tk.Frame(self.detail_pane, bg="#090b10",
                              highlightthickness=1, highlightbackground=BORDER)
        log_frame.pack(fill="x", padx=18, pady=(6, 18))

        self.log_text = tk.Text(log_frame, bg="#090b10", fg=SUCCESS,
                                 font=FONT_MONO, insertbackground=ACCENT,
                                 relief="flat", wrap="word",
                                 state="disabled", padx=10, pady=10,
                                 height=7)
        self.log_text.pack(fill="x")
        self._log(f"Selected: {pkg}\n")
        if not mod_ok:
            self._log(f"⚠ No mod file: {mod_err}")
        if not has_original:
            self._log("⚠ Original metadata not cached — scan device to pull it.")
        elif mod_ok:
            count = len(self._mod_rows)
            self._log(f"{count} mod(s) available — check/uncheck to choose, then apply.")

    def _action_btn(self, parent, text, color, cmd):
        f   = tk.Frame(parent, bg=color, cursor="hand2")
        f.pack(fill="x", pady=3)
        lbl = tk.Label(f, text=text, font=FONT_BTN,
                        fg=BG, bg=color, pady=8, padx=14, anchor="w")
        lbl.pack(fill="x")
        for w in (f, lbl):
            w.bind("<ButtonPress-1>", lambda e, c=cmd: c())
            w.bind("<Enter>", lambda e, b=f, l=lbl, c=color:
                   (b.config(bg=self._lighten(c)), l.config(bg=self._lighten(c))))
            w.bind("<Leave>", lambda e, b=f, l=lbl, c=color:
                   (b.config(bg=c), l.config(bg=c)))

    def _lighten(self, hex_color, amt=30):
        r, g, b = hex_to_rgb(hex_color)
        return f"#{min(r+amt,255):02x}{min(g+amt,255):02x}{min(b+amt,255):02x}"

    def _log(self, msg):
        if not hasattr(self, "log_text"):
            return
        self.log_text.config(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _show_no_device_warning(self):
        for w in self._inner_list.winfo_children():
            w.destroy()
        self._cards.clear()

        warn_frame = tk.Frame(self._inner_list,
                               bg="#1a1000",
                               highlightthickness=1, highlightbackground=WARNING)
        warn_frame.pack(fill="x", padx=8, pady=12)

        tk.Label(warn_frame, text="⚠  No device connected",
                 font=FONT_CARD, fg=WARNING, bg="#1a1000",
                 anchor="w", padx=12, pady=8).pack(fill="x")
        tk.Label(warn_frame,
                 text="Connect your headset via USB,\nenable ADB debugging,\nthen press Scan Device.",
                 font=FONT_SMALL, fg=MUTED, bg="#1a1000",
                 anchor="w", padx=12, justify="left").pack(fill="x", pady=(0, 10))

    def _show_unauthorized_warning(self):
        for w in self._inner_list.winfo_children():
            w.destroy()
        self._cards.clear()

        warn_frame = tk.Frame(self._inner_list,
                               bg="#1a0d00",
                               highlightthickness=1, highlightbackground=WARNING)
        warn_frame.pack(fill="x", padx=8, pady=12)

        tk.Label(warn_frame, text="⚠  Authorization required",
                 font=FONT_CARD, fg=WARNING, bg="#1a0d00",
                 anchor="w", padx=12, pady=8).pack(fill="x")
        tk.Label(warn_frame,
                 text="Your headset is connected but hasn't\n"
                      "granted this computer access.\n\n"
                      "Put on your headset — you should see\n"
                      "a prompt asking to \"Allow USB debugging\".\n"
                      "Tap Allow, then press Scan Device again.",
                 font=FONT_SMALL, fg=MUTED, bg="#1a0d00",
                 anchor="w", padx=12, justify="left").pack(fill="x", pady=(0, 12))

    def _populate_games(self, games, device_connected=True):
        self.placeholder.place_forget()
        self._list_canvas.pack(side="left", fill="both", expand=True)

        for w in self._inner_list.winfo_children():
            w.destroy()
        self._cards.clear()

        if device_connected == "unauthorized":
            self._show_unauthorized_warning()
            return

        if not device_connected:
            self._show_no_device_warning()
            return

        if not games:
            tk.Label(self._inner_list,
                     text="No Unity games found.",
                     font=FONT_MONO, fg=MUTED, bg=PANEL, pady=20).pack()
            return

        for i, pkg in enumerate(games):
            card = GameCard(self._inner_list, pkg, on_select=self._select_game)
            card.pack_forget()
            self.after(i * 80, lambda c=card: c.pack(fill="x", padx=8, pady=3))
            self._cards[pkg] = card

    def _select_game(self, pkg):
        if self._selected_pkg and self._selected_pkg in self._cards:
            self._cards[self._selected_pkg].set_selected(False)
        self._selected_pkg = pkg
        self._cards[pkg].set_selected(True)
        self._build_detail_game(pkg)
        self.status.set(f"Selected: {pkg}", color=TEXT)

    def _scan(self):
        self.scan_btn.start_spin()
        self.adb_lbl.config(text="ADB: connecting…", fg=WARNING)
        self.status.set("Scanning device for Unity games…", color=WARNING, dot=WARNING)

        def worker():
            state = check_device_connected()
            games = list_unity_games() if state == "connected" else []
            self.after(0, lambda: self._on_scan_done(games, state))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, games, device_state):
        self.scan_btn.stop_spin()

        if device_state == "unauthorized":
            self.adb_lbl.config(text="ADB: unauthorized !", fg=WARNING)
            self.status.set("Headset connected but not authorized — check your headset.", color=WARNING, dot=WARNING)
            self._populate_games([], device_connected="unauthorized")
            return

        if device_state != "connected":
            self.adb_lbl.config(text="ADB: no device ✗", fg=DANGER)
            self.status.set("No device connected. Connect a device and allow USB debugging in headset.", color=DANGER, dot=DANGER)
            self._populate_games([], device_connected=False)
            return

        count = len(games)
        if count:
            self.adb_lbl.config(text="ADB: connected ✓", fg=SUCCESS)
            self.status.set(f"Found {count} game(s) — pulling metadata…", color=WARNING, dot=WARNING)
        else:
            self.adb_lbl.config(text="ADB: no games found", fg=DANGER)
            self.status.set("No Unity games detected.", color=DANGER, dot=DANGER)

        self._populate_games(games, device_connected=True)

        if games:
            def pull_all():
                for pkg in games:
                    self.after(0, lambda p=pkg: self.status.set(
                        f"Pulling metadata: {p}…", color=WARNING, dot=WARNING))
                    ok  = update_game_data(pkg)
                    msg = f"{'✓' if ok else '✗'}  {pkg}"
                    col = SUCCESS if ok else DANGER
                    self.after(0, lambda m=msg, c=col: self.status.set(m, color=c, dot=c))
                self.after(0, lambda: self.status.set(
                    f"Ready — metadata updated for {len(games)} game(s).",
                    color=SUCCESS, dot=SUCCESS))

            threading.Thread(target=pull_all, daemon=True).start()

    def _apply(self, pkg):
        selected = [row.mod_name for row in self._mod_rows if row.is_checked()]

        if not selected:
            self._log("⚠ No mods selected — check at least one mod to apply.")
            self.status.set("No mods selected.", color=WARNING, dot=WARNING)
            return

        self.status.set(f"Applying {len(selected)} mod(s) to {pkg}…", color=WARNING, dot=WARNING)
        self._log(f"Applying {len(selected)} mod(s): {', '.join(selected)}")

        def worker():
            try:
                mod_metadata(pkg, *selected)
                self.after(0, lambda: (
                    self.status.set(f"{len(selected)} mod(s) applied ✓", color=SUCCESS, dot=SUCCESS),
                    self._log(f"Mods applied successfully ✓  [{', '.join(selected)}]")
                ))
            except Exception as ex:
                self.after(0, lambda: (
                    self.status.set(f"Error: {ex}", color=DANGER, dot=DANGER),
                    self._log(f"ERROR: {ex}")
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _restore(self, pkg):
        self.status.set(f"Restoring original metadata for {pkg}…", color=WARNING, dot=WARNING)
        self._log(f"Restoring original metadata: {pkg}")

        def worker():
            try:
                restore_original_metadata(pkg)
                self.after(0, lambda: (
                    self.status.set("Original metadata restored ✓", color=SUCCESS, dot=SUCCESS),
                    self._log("Original metadata restored successfully ✓")
                ))
            except Exception as ex:
                self.after(0, lambda: (
                    self.status.set(f"Restore failed: {ex}", color=DANGER, dot=DANGER),
                    self._log(f"ERROR: {ex}")
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _pulse_title(self):
        c1 = hex_to_rgb(ACCENT)
        c2 = hex_to_rgb(ACCENT2)
        t  = (time.time() % 3) / 3
        tt = t if t < 0.5 else 1 - t
        color = lerp_color(c1, c2, tt * 2)
        self.title_lbl.config(fg=color)
        self._pulse_id = self.after(50, self._pulse_title)

    def _on_list_configure(self, e):
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._list_canvas.itemconfig(self._canvas_window, width=e.width)

if __name__ == "__main__":
    app = Main_App()
    app.mainloop()