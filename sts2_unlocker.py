#!/usr/bin/env python3
"""Slay the Spire 2 - multiplayer ascension unlocker.

Sets `max_multiplayer_ascension` in a profile's progress.save so the chosen
multiplayer ascension level (and everything below it) is selectable.

Standard library only. Run with no arguments for the GUI, or `--cli` for an
interactive terminal flow. See README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

MAX_ASCENSION = 10
MIN_ASCENSION = 0
FIELD_MAX = "max_multiplayer_ascension"
FIELD_PREF = "preferred_multiplayer_ascension"
GAME_PROCESS_PATTERNS = ("Slay the Spire 2", "SlayTheSpire2")


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def default_data_dirs() -> List[Path]:
    """Candidate SlayTheSpire2 user-data folders for this OS."""
    home = Path.home()
    system = platform.system()
    dirs: List[Path] = []
    if system == "Darwin":
        dirs.append(home / "Library" / "Application Support" / "SlayTheSpire2")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "SlayTheSpire2")
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        dirs.append(Path(xdg) / "SlayTheSpire2" if xdg else home / ".local" / "share" / "SlayTheSpire2")
        # Proton prefix (Steam Play on Linux)
        dirs.append(
            home / ".steam" / "steam" / "steamapps" / "compatdata" / "2868840" / "pfx"
            / "drive_c" / "users" / "steamuser" / "AppData" / "Roaming" / "SlayTheSpire2"
        )
    return [d for d in dirs if d.is_dir()]


@dataclass
class Profile:
    account_dir: Path      # e.g. .../SlayTheSpire2/steam/7656119...
    profile_num: int       # 1, 2, 3
    save_path: Path        # .../profileN/saves/progress.save
    data: dict

    @property
    def account_label(self) -> str:
        return f"{self.account_dir.parent.name}/{self.account_dir.name}"

    @property
    def is_last_used(self) -> bool:
        marker = self.account_dir / "profile.save"
        try:
            return json.loads(marker.read_text(encoding="utf-8")).get("last_profile_id") == self.profile_num
        except (OSError, ValueError):
            return False

    @property
    def current_max(self) -> int:
        return int(self.data.get(FIELD_MAX, 0))

    def summary(self) -> str:
        d = self.data
        hours = int(d.get("total_playtime", 0)) / 3600
        chars = d.get("character_stats", [])
        best_sp = max((int(c.get("max_ascension", 0)) for c in chars), default=0)
        modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.save_path.stat().st_mtime))
        tag = "  [last used]" if self.is_last_used else ""
        return (
            f"{self.account_label}  profile{self.profile_num}{tag}\n"
            f"    id={d.get('unique_id', '?')}  playtime={hours:.1f}h  floors={d.get('floors_climbed', 0)}  "
            f"best single-player A{best_sp}\n"
            f"    multiplayer: max A{self.current_max}, preferred A{d.get(FIELD_PREF, 0)}  "
            f"(saved {modified})"
        )


def find_profiles(data_dir: Path) -> List[Profile]:
    """Find every <platform>/<account>/profileN/saves/progress.save under data_dir."""
    found: List[Profile] = []
    for save in sorted(data_dir.glob("*/*/profile*/saves/progress.save")):
        m = re.fullmatch(r"profile(\d+)", save.parent.parent.name)
        if not m:
            continue
        try:
            data = json.loads(save.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"warning: skipping unreadable {save}: {exc}", file=sys.stderr)
            continue
        if FIELD_MAX not in data:
            print(f"warning: {save} has no '{FIELD_MAX}' field, skipping", file=sys.stderr)
            continue
        found.append(Profile(save.parent.parent.parent, int(m.group(1)), save, data))
    return found


def game_is_running() -> bool:
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
        else:
            out = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True).stdout
    except OSError:
        return False
    return any(p.lower() in out.lower() for p in GAME_PROCESS_PATTERNS)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def serialise(data: dict) -> str:
    # Matches the game's own formatting byte-for-byte (2-space indent, no
    # trailing newline), so the only diff is the value we change.
    return json.dumps(data, indent=2)


def apply_unlock(profile: Profile, level: int, set_preferred: bool = True) -> Path:
    """Back up, then write the new level to progress.save (and its .backup twin).

    Returns the directory holding the backup copies.
    """
    if not MIN_ASCENSION <= level <= MAX_ASCENSION:
        raise ValueError(f"ascension must be {MIN_ASCENSION}-{MAX_ASCENSION}")

    saves_dir = profile.save_path.parent
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = saves_dir.parent / f"unlocker_backup_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for name in ("progress.save", "progress.save.backup"):
        src = saves_dir / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)

    # Re-read right before writing in case the file changed since discovery.
    data = json.loads(profile.save_path.read_text(encoding="utf-8"))
    data[FIELD_MAX] = level
    if set_preferred:
        data[FIELD_PREF] = level
    text = serialise(data)

    # The game keeps progress.save.backup as a fallback; keep both in sync so
    # it doesn't "recover" the old value.
    for name in ("progress.save", "progress.save.backup"):
        target = saves_dir / name
        if name.endswith(".backup") and not target.exists():
            continue
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)

    # Sanity check.
    check = json.loads(profile.save_path.read_text(encoding="utf-8"))
    assert check[FIELD_MAX] == level, "write verification failed"
    profile.data = check
    return backup_dir


WARNINGS = (
    "Close Slay the Spire 2 completely before applying (it overwrites saves on exit).\n"
    "If Steam Cloud offers a sync conflict on next launch, choose the LOCAL files.\n"
    "A timestamped backup is written next to the profile before any change."
)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    ans = input(f"{prompt}{suffix}: ").strip()
    return ans or (default or "")


def confirm(prompt: str, default_yes: bool = False) -> bool:
    ans = ask(prompt + (" (Y/n)" if default_yes else " (y/N)")).lower()
    return ans in ("y", "yes") if ans else default_yes


def choose_data_dir(override: Optional[str]) -> Path:
    if override:
        p = Path(override).expanduser()
        if not p.is_dir():
            sys.exit(f"error: {p} is not a directory")
        return p
    dirs = default_data_dirs()
    if not dirs:
        print("Could not find the Slay the Spire 2 save folder automatically.")
        p = Path(ask("Enter the path to your SlayTheSpire2 data folder")).expanduser()
        if not p.is_dir():
            sys.exit(f"error: {p} is not a directory")
        return p
    if len(dirs) == 1:
        d = dirs[0]
        print(f"Found save folder:\n  {d}")
        if confirm("Is this the right location?", default_yes=True):
            return d
        p = Path(ask("Enter the correct path")).expanduser()
        if not p.is_dir():
            sys.exit(f"error: {p} is not a directory")
        return p
    for i, d in enumerate(dirs, 1):
        print(f"  {i}) {d}")
    return dirs[int(ask("Choose a folder", "1")) - 1]


def run_cli(args: argparse.Namespace) -> int:
    print("=== Slay the Spire 2: multiplayer ascension unlocker ===\n")
    data_dir = choose_data_dir(args.path)
    profiles = find_profiles(data_dir)
    if not profiles:
        print(f"No profiles with progress.save found under {data_dir}")
        return 1

    print("\nProfiles found:")
    for i, p in enumerate(profiles, 1):
        print(f"\n {i}) {p.summary()}")
    default_idx = next((str(i) for i, p in enumerate(profiles, 1) if p.is_last_used), "1")
    while True:
        try:
            idx = int(ask("\nWhich profile?", default_idx))
            if 1 <= idx <= len(profiles):
                profile = profiles[idx - 1]
                break
        except ValueError:
            pass
        print(f"Enter a number from 1 to {len(profiles)}.")

    if args.level is not None:
        level = args.level
    else:
        try:
            level = int(ask(f"Unlock multiplayer ascension up to ({MIN_ASCENSION}-{MAX_ASCENSION})", str(MAX_ASCENSION)))
        except ValueError:
            print("Not a number.")
            return 1
    if not MIN_ASCENSION <= level <= MAX_ASCENSION:
        print(f"Ascension must be between {MIN_ASCENSION} and {MAX_ASCENSION}.")
        return 1

    if level == profile.current_max:
        print(f"Profile already has multiplayer A{level} unlocked. Nothing to do.")
        return 0

    print("\n" + WARNINGS + "\n")
    while game_is_running():
        if not confirm("Slay the Spire 2 appears to be running. Close it, then press y to re-check (n to abort)"):
            return 1

    direction = "LOWER" if level < profile.current_max else "raise"
    print(f"About to {direction} multiplayer max ascension A{profile.current_max} -> A{level}")
    print(f"  file: {profile.save_path}")
    if not confirm("Proceed?"):
        print("Aborted, nothing changed.")
        return 1

    backup = apply_unlock(profile, level, set_preferred=not args.keep_preferred)
    print(f"\nDone. Multiplayer ascension A{level} unlocked.")
    print(f"Backup saved to: {backup}")
    return 0


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

def run_gui(args: argparse.Namespace) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("StS2 Multiplayer Ascension Unlocker")
    root.minsize(640, 420)

    state = {"profiles": []}  # type: dict
    dir_var = tk.StringVar()
    level_var = tk.IntVar(value=MAX_ASCENSION if args.level is None else args.level)
    pref_var = tk.BooleanVar(value=not args.keep_preferred)
    status_var = tk.StringVar()

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    # Folder row
    ttk.Label(frm, text="Save folder:").grid(row=0, column=0, sticky="w")
    ttk.Entry(frm, textvariable=dir_var, width=60).grid(row=0, column=1, sticky="we", padx=4)

    def browse():
        d = filedialog.askdirectory(initialdir=dir_var.get() or str(Path.home()))
        if d:
            dir_var.set(d)
            load_profiles()

    ttk.Button(frm, text="Browse...", command=browse).grid(row=0, column=2)

    # Profile list
    ttk.Label(frm, text="Profiles (check this is YOUR profile):").grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 2))
    listbox = tk.Listbox(frm, height=6, exportselection=False, font=("Menlo", 11))
    listbox.grid(row=2, column=0, columnspan=3, sticky="nsew")
    detail = tk.Text(frm, height=5, wrap="word", font=("Menlo", 11))
    detail.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
    detail.configure(state="disabled")

    def show_detail(_evt=None):
        sel = listbox.curselection()
        text = state["profiles"][sel[0]].summary() + f"\n    file: {state['profiles'][sel[0]].save_path}" if sel else ""
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("1.0", text)
        detail.configure(state="disabled")

    listbox.bind("<<ListboxSelect>>", show_detail)

    def load_profiles():
        listbox.delete(0, "end")
        d = Path(dir_var.get()).expanduser()
        state["profiles"] = find_profiles(d) if d.is_dir() else []
        for p in state["profiles"]:
            tag = " *last used*" if p.is_last_used else ""
            listbox.insert("end", f"{p.account_label} / profile{p.profile_num}  (MP A{p.current_max}){tag}")
        if state["profiles"]:
            idx = next((i for i, p in enumerate(state["profiles"]) if p.is_last_used), 0)
            listbox.selection_set(idx)
            status_var.set(f"Found {len(state['profiles'])} profile(s).")
        else:
            status_var.set("No profiles found here. Use Browse... to pick the SlayTheSpire2 folder.")
        show_detail()

    # Controls
    ctl = ttk.Frame(frm)
    ctl.grid(row=4, column=0, columnspan=3, sticky="we", pady=10)
    ttk.Label(ctl, text="Unlock multiplayer ascension up to:").pack(side="left")
    tk.Spinbox(ctl, from_=MIN_ASCENSION, to=MAX_ASCENSION, textvariable=level_var, width=4).pack(side="left", padx=6)
    ttk.Checkbutton(ctl, text="Also select it as the preferred level", variable=pref_var).pack(side="left", padx=10)

    def do_apply():
        sel = listbox.curselection()
        if not sel:
            messagebox.showwarning("No profile", "Select a profile first.")
            return
        profile = state["profiles"][sel[0]]
        try:
            level = int(level_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid", "Ascension must be a number.")
            return
        if not MIN_ASCENSION <= level <= MAX_ASCENSION:
            messagebox.showerror("Invalid", f"Ascension must be {MIN_ASCENSION}-{MAX_ASCENSION}.")
            return
        if game_is_running():
            messagebox.showerror("Game running", "Slay the Spire 2 is running. Quit it completely, then try again.")
            return
        lower = " (this LOWERS it)" if level < profile.current_max else ""
        if not messagebox.askyesno(
            "Confirm",
            f"{profile.summary()}\n\nSet multiplayer max ascension A{profile.current_max} -> A{level}{lower}?\n\n{WARNINGS}",
        ):
            return
        try:
            backup = apply_unlock(profile, level, set_preferred=pref_var.get())
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            messagebox.showerror("Failed", f"Nothing was changed or a backup exists.\n\n{exc}")
            return
        load_profiles()
        messagebox.showinfo("Done", f"Multiplayer A{level} unlocked.\n\nBackup:\n{backup}")

    btns = ttk.Frame(frm)
    btns.grid(row=5, column=0, columnspan=3, sticky="e")
    ttk.Button(btns, text="Refresh", command=load_profiles).pack(side="left", padx=4)
    ttk.Button(btns, text="Unlock", command=do_apply).pack(side="left")

    ttk.Label(frm, textvariable=status_var, foreground="gray").grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(2, weight=1)

    if args.path:
        dir_var.set(str(Path(args.path).expanduser()))
    else:
        dirs = default_data_dirs()
        dir_var.set(str(dirs[0]) if dirs else "")
    load_profiles()
    if not dir_var.get():
        status_var.set("Save folder not found automatically. Use Browse... to locate SlayTheSpire2.")

    root.mainloop()
    return 0


# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Unlock Slay the Spire 2 multiplayer ascensions.")
    ap.add_argument("--cli", action="store_true", help="use the interactive terminal flow instead of the GUI")
    ap.add_argument("--path", help="SlayTheSpire2 data folder (auto-detected if omitted)")
    ap.add_argument("--level", type=int, help=f"ascension to unlock up to ({MIN_ASCENSION}-{MAX_ASCENSION})")
    ap.add_argument("--keep-preferred", action="store_true", help="don't change the preselected MP ascension")
    args = ap.parse_args(argv)

    if args.cli:
        return run_cli(args)
    try:
        return run_gui(args)
    except ImportError:
        print("tkinter not available, falling back to the terminal flow.\n")
        return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
