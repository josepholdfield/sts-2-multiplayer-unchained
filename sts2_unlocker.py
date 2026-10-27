#!/usr/bin/env python3
"""Slay the Spire 2 - multiplayer ascension unlocker.

Sets `max_multiplayer_ascension` in a profile's progress.save so the chosen
multiplayer ascension level (and everything below it) is selectable.

The game keeps two copies of progress.save when Steam is running: the one in
its own data folder and the one in Steam's cloud cache (userdata/.../remote).
On launch it copies the Steam copy over the local one whenever their
timestamps differ, so both copies are written here, with matching timestamps.

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

MAX_ASCENSION = 10          # hard cap in the game (ProgressState.ClampAscension)
MIN_ASCENSION = 0
FIELD_MAX = "max_multiplayer_ascension"
FIELD_PREF = "preferred_multiplayer_ascension"
GAME_PROCESS_PATTERNS = ("Slay the Spire 2", "SlayTheSpire2")
STEAM_APP_ID = 2868840
STEAMID64_BASE = 76561197960265728   # steamID64 - this = the 32-bit account id Steam uses for userdata/


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


def default_steam_roots() -> List[Path]:
    """Candidate Steam install folders (the ones holding userdata/) for this OS."""
    home = Path.home()
    system = platform.system()
    roots: List[Path] = []
    if system == "Darwin":
        roots.append(home / "Library" / "Application Support" / "Steam")
    elif system == "Windows":
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except Exception:  # noqa: BLE001 - registry lookup is best-effort
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(Path(os.environ[env]) / "Steam")
    else:
        roots += [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
        ]
    seen: List[Path] = []
    for r in roots:
        if (r / "userdata").is_dir() and r not in seen:
            seen.append(r)
    return seen


def steam_cloud_copy(account_dir: Path, profile_num: int, steam_roots: List[Path]) -> Optional[Path]:
    """Steam's cached cloud copy of this profile's progress.save, if any.

    Lives at <steam>/userdata/<account32>/2868840/remote/profileN/saves/progress.save.
    Only exists for the 'steam' platform folder.
    """
    if account_dir.parent.name != "steam":
        return None
    try:
        account32 = int(account_dir.name) - STEAMID64_BASE
    except ValueError:
        return None
    for root in steam_roots:
        p = root / "userdata" / str(account32) / str(STEAM_APP_ID) / "remote" / f"profile{profile_num}" / "saves" / "progress.save"
        if p.is_file():
            return p
    return None


def _load_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warning: skipping unreadable {path}: {exc}", file=sys.stderr)
        return None


def _fmt_mtime(path: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))


@dataclass
class Profile:
    account_dir: Path      # e.g. .../SlayTheSpire2/steam/7656119...
    profile_num: int       # 1, 2, 3
    save_path: Path        # .../profileN/saves/progress.save
    data: dict
    cloud_path: Optional[Path] = None   # Steam's cached cloud copy, if found
    cloud_data: Optional[dict] = None

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

    @property
    def cloud_differs(self) -> bool:
        return self.cloud_data is not None and self.cloud_data != self.data

    def base_data(self) -> "tuple[dict, str]":
        """The copy to build the edit on, and a label saying which one.

        When the local file and Steam's cloud copy disagree, the game will use
        the Steam copy on next launch, so start from whichever has more play
        recorded (normally the Steam copy, freshly synced from another PC).
        """
        if not self.cloud_differs:
            return self.data, "local save"
        assert self.cloud_data is not None
        if int(self.cloud_data.get("total_playtime", 0)) >= int(self.data.get("total_playtime", 0)):
            return self.cloud_data, "Steam cloud copy (has more playtime than the local file)"
        return self.data, "local save (has more playtime than the Steam cloud copy)"

    def summary(self) -> str:
        d = self.data
        hours = int(d.get("total_playtime", 0)) / 3600
        chars = d.get("character_stats", [])
        best_sp = max((int(c.get("max_ascension", 0)) for c in chars), default=0)
        tag = "  [last used]" if self.is_last_used else ""
        lines = [
            f"{self.account_label}  profile{self.profile_num}{tag}",
            f"    id={d.get('unique_id', '?')}  playtime={hours:.1f}h  floors={d.get('floors_climbed', 0)}  "
            f"best single-player A{best_sp}",
            f"    multiplayer: max A{self.current_max}, preferred A{d.get(FIELD_PREF, 0)}  "
            f"(saved {_fmt_mtime(self.save_path)})",
        ]
        if self.cloud_path is None:
            lines.append("    Steam cloud copy: not found (only the local file will be written)")
        elif self.cloud_differs:
            cd = self.cloud_data or {}
            lines.append(
                f"    Steam cloud copy DIFFERS: max A{cd.get(FIELD_MAX, '?')}, playtime={int(cd.get('total_playtime', 0)) / 3600:.1f}h, "
                f"floors={cd.get('floors_climbed', 0)} (saved {_fmt_mtime(self.cloud_path)}). Edit will be based on the {self.base_data()[1]}."
            )
        else:
            lines.append("    Steam cloud copy: identical to local (both will be written)")
        return "\n".join(lines)


def find_profiles(data_dir: Path, steam_roots: Optional[List[Path]] = None) -> List[Profile]:
    """Find every <platform>/<account>/profileN/saves/progress.save under data_dir.

    Also locates Steam's cached cloud copy of each profile (see steam_cloud_copy).
    """
    if steam_roots is None:
        steam_roots = default_steam_roots()
    found: List[Profile] = []
    for save in sorted(data_dir.glob("*/*/profile*/saves/progress.save")):
        m = re.fullmatch(r"profile(\d+)", save.parent.parent.name)
        if not m:
            continue
        data = _load_json(save)
        if data is None:
            continue
        if FIELD_MAX not in data:
            print(f"warning: {save} has no '{FIELD_MAX}' field, skipping", file=sys.stderr)
            continue
        profile = Profile(save.parent.parent.parent, int(m.group(1)), save, data)
        cloud = steam_cloud_copy(profile.account_dir, profile.profile_num, steam_roots)
        if cloud is not None:
            cloud_data = _load_json(cloud)
            if cloud_data is not None and FIELD_MAX in cloud_data:
                profile.cloud_path, profile.cloud_data = cloud, cloud_data
        found.append(profile)
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


@dataclass
class ApplyResult:
    backup_dir: Path
    written: List[Path]
    base_label: str

    def describe(self) -> str:
        files = "\n".join(f"  {p}" for p in self.written)
        return f"Based on: {self.base_label}\nWritten:\n{files}\nBackup saved to: {self.backup_dir}"


def apply_unlock(profile: Profile, level: int, set_preferred: bool = True) -> ApplyResult:
    """Back up, then write the new level to every copy of progress.save.

    Writes the local file, its .backup twin and Steam's cached cloud copy, all
    with the same content and the same modification time, so the game's
    start-up sync sees them as identical and keeps the change whichever copy
    it picks.
    """
    if not MIN_ASCENSION <= level <= MAX_ASCENSION:
        raise ValueError(f"ascension must be {MIN_ASCENSION}-{MAX_ASCENSION}")

    saves_dir = profile.save_path.parent
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = saves_dir.parent / f"unlocker_backup_{stamp}"
    n = 1
    while backup_dir.exists():
        n += 1
        backup_dir = saves_dir.parent / f"unlocker_backup_{stamp}-{n}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for name in ("progress.save", "progress.save.backup"):
        src = saves_dir / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)
    if profile.cloud_path is not None:
        (backup_dir / "steam_cloud").mkdir()
        shutil.copy2(profile.cloud_path, backup_dir / "steam_cloud" / "progress.save")

    # Re-read right before writing in case the files changed since discovery.
    profile.data = json.loads(profile.save_path.read_text(encoding="utf-8"))
    if profile.cloud_path is not None:
        profile.cloud_data = json.loads(profile.cloud_path.read_text(encoding="utf-8"))
    data, base_label = profile.base_data()
    data = dict(data)
    data[FIELD_MAX] = level
    if set_preferred:
        data[FIELD_PREF] = level
    text = serialise(data)

    # The game keeps progress.save.backup as a fallback; keep it in sync so it
    # doesn't "recover" the old value. The Steam copy wins over the local one
    # on launch whenever their timestamps differ, so write it too.
    targets = [saves_dir / "progress.save"]
    if (saves_dir / "progress.save.backup").exists():
        targets.append(saves_dir / "progress.save.backup")
    if profile.cloud_path is not None:
        targets.append(profile.cloud_path)
    for target in targets:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    now = int(time.time())
    for target in targets:
        os.utime(target, (now, now))

    # Sanity check every copy.
    for target in targets:
        check = json.loads(target.read_text(encoding="utf-8"))
        assert check[FIELD_MAX] == level, f"write verification failed for {target}"
    profile.data = json.loads(profile.save_path.read_text(encoding="utf-8"))
    if profile.cloud_path is not None:
        profile.cloud_data = json.loads(profile.cloud_path.read_text(encoding="utf-8"))
    return ApplyResult(backup_dir, targets, base_label)


WARNINGS = (
    "Close Slay the Spire 2 completely before applying (it overwrites saves on exit).\n"
    "Launch the game on THIS machine next, so Steam Cloud uploads the change before\n"
    "any other PC you play on overwrites it. If Steam shows a cloud conflict, keep\n"
    "this machine's files.\n"
    "A multiplayer lobby only allows the LOWEST level unlocked by any player in it,\n"
    "so everyone you play with needs the unlock too.\n"
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
    profiles = find_profiles(data_dir, steam_roots_from_args(args))
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

    if level == profile.current_max and not profile.cloud_differs:
        print(f"Profile already has multiplayer A{level} unlocked. Nothing to do.")
        return 0

    print("\n" + WARNINGS + "\n")
    while game_is_running():
        if not confirm("Slay the Spire 2 appears to be running. Close it, then press y to re-check (n to abort)"):
            return 1

    direction = "LOWER" if level < profile.current_max else "raise"
    print(f"About to {direction} multiplayer max ascension A{profile.current_max} -> A{level}")
    print(f"  local file: {profile.save_path}")
    if profile.cloud_path is not None:
        print(f"  Steam copy: {profile.cloud_path}")
    if profile.cloud_differs:
        print(f"  The two copies differ; the edit will be based on the {profile.base_data()[1]}.")
    if not confirm("Proceed?"):
        print("Aborted, nothing changed.")
        return 1

    result = apply_unlock(profile, level, set_preferred=not args.keep_preferred)
    print(f"\nDone. Multiplayer ascension A{level} unlocked.")
    print(result.describe())
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
    detail = tk.Text(frm, height=7, wrap="word", font=("Menlo", 11))
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
        state["profiles"] = find_profiles(d, steam_roots_from_args(args)) if d.is_dir() else []
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
            result = apply_unlock(profile, level, set_preferred=pref_var.get())
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            messagebox.showerror("Failed", f"Nothing was changed or a backup exists.\n\n{exc}")
            return
        load_profiles()
        messagebox.showinfo("Done", f"Multiplayer A{level} unlocked.\n\n{result.describe()}")

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

def steam_roots_from_args(args: argparse.Namespace) -> List[Path]:
    if getattr(args, "steam_path", None):
        p = Path(args.steam_path).expanduser()
        if not (p / "userdata").is_dir():
            sys.exit(f"error: {p} has no userdata/ folder; point --steam-path at the Steam install folder")
        return [p]
    return default_steam_roots()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Unlock Slay the Spire 2 multiplayer ascensions.")
    ap.add_argument("--cli", action="store_true", help="use the interactive terminal flow instead of the GUI")
    ap.add_argument("--path", help="SlayTheSpire2 data folder (auto-detected if omitted)")
    ap.add_argument("--steam-path", help="Steam install folder containing userdata/ (auto-detected if omitted)")
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
