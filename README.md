# StS2 Multiplayer Ascension Unlocker

Unlocks multiplayer ascension levels (up to A10) for a Slay the Spire 2 save profile.
Python 3.8+ with the standard library only.

## Usage

Quit Slay the Spire 2 completely first.

```sh
python3 sts2_unlocker.py            # simple GUI (falls back to CLI if tkinter is missing)
python3 sts2_unlocker.py --cli      # interactive terminal prompts
```

Options:

| Flag | Meaning |
|---|---|
| `--path DIR` | SlayTheSpire2 data folder, if auto-detection fails |
| `--steam-path DIR` | Steam install folder (the one containing `userdata/`), if auto-detection fails |
| `--level N` | Pre-fill the target ascension (0-10) |
| `--keep-preferred` | Don't change the preselected MP ascension |

The tool will:

1. Auto-detect the save folder and ask you to confirm it.
2. List every account/profile with its ID, playtime, floors climbed and current MP ascension, mark the last-used profile, and say whether Steam's cloud copy of the save was found and whether it matches the local file.
3. Ask which ascension to unlock up to, refuse to continue while the game is running, and ask for a final confirmation.
4. Back up every copy to `profileN/unlocker_backup_<timestamp>/`, then write the new value to all of them.

## What it changes

Two fields in `progress.save`:

- `max_multiplayer_ascension` is set to the chosen level.
- `preferred_multiplayer_ascension` is set to the same level, unless you pass `--keep-preferred` or untick the box in the GUI.

The file is rewritten in the game's own JSON format, so only those lines change.

The same content is written to every copy of the file the game might read:

| Copy | Location |
|---|---|
| Local save | `<data>/steam/<steamid64>/profileN/saves/progress.save` (and `.backup`) |
| Steam cloud cache | `<steam>/userdata/<account32>/2868840/remote/profileN/saves/progress.save` |

Default data folders:

- macOS: `~/Library/Application Support/SlayTheSpire2`
- Windows: `%APPDATA%\SlayTheSpire2`
- Linux: `~/.local/share/SlayTheSpire2`

Default Steam folders: `~/Library/Application Support/Steam` (macOS), the `SteamPath` registry value or `C:\Program Files (x86)\Steam` (Windows), `~/.steam/steam` or `~/.local/share/Steam` (Linux).

## Why both copies matter

When Steam is running, the game treats Steam's cloud cache as the source of truth. On every launch it compares the timestamp of the cloud copy with the local file and, if they differ at all (not only if the cloud is newer), copies the cloud version over the local file. Editing only the local file is therefore undone on the next launch whenever the cloud copy was touched since, for example after playing on another PC. Writing both copies with the same timestamp means the change survives whichever copy the game picks.

If the two copies disagree when the tool runs, it bases the edit on the one with more recorded playtime (normally the Steam copy, freshly synced from another machine) and says so, so you don't lose runs played elsewhere.

## Multiplayer rules worth knowing

- Multiplayer ascension is tracked separately from single-player and is capped at A10, the same as single-player. It normally goes up by one each time you win a multiplayer run at your current maximum.
- A lobby's selectable maximum is the **lowest** `max_multiplayer_ascension` among all players in it. Everyone you play with needs the unlock too, or the lobby stays capped at their level.
- When you join someone else's lobby, the host picks the ascension within that cap.

## Notes

- **Other PCs:** launch the game on the machine you edited first, so Steam Cloud uploads the change. If you play on another PC before that, its save wins and the edit is lost; run the tool again. If Steam shows a cloud sync conflict, keep this machine's files.
- **Restoring:** copy the files from `unlocker_backup_<timestamp>/` back into `saves/`, and `unlocker_backup_<timestamp>/steam_cloud/progress.save` back into the Steam cache folder.
- **Schema:** this was written against save schema version 21. A game update could rename these fields; if that happens, the tool skips profiles that are missing them. Saves from a newer game build (schema 24 seen in the wild) still carry the same two fields and are left otherwise untouched.
