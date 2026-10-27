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
| `--level N` | Pre-fill the target ascension (0-10) |
| `--keep-preferred` | Don't change the preselected MP ascension |

The tool will:

1. Auto-detect the save folder and ask you to confirm it.
2. List every account/profile with its ID, playtime, floors climbed and current MP ascension, and mark the last-used profile.
3. Ask which ascension to unlock up to, refuse to continue while the game is running, and ask for a final confirmation.
4. Copy `progress.save` and `progress.save.backup` to `profileN/unlocker_backup_<timestamp>/`, then write the new value to both files.

## What it changes

In `<data>/<platform>/<account>/profileN/saves/progress.save`:

- `max_multiplayer_ascension` is set to the chosen level.
- `preferred_multiplayer_ascension` is set to the same level, unless you pass `--keep-preferred` or untick the box in the GUI.

The file is rewritten in the game's own JSON format, so only those lines change.

Default data folders:

- macOS: `~/Library/Application Support/SlayTheSpire2`
- Windows: `%APPDATA%\SlayTheSpire2`
- Linux: `~/.local/share/SlayTheSpire2`

## Notes

- **Steam Cloud:** if Steam reports a sync conflict on the next launch, choose the local files.
- **Restoring:** copy the files from `unlocker_backup_<timestamp>/` back into `saves/`.
- **Schema:** this was written against save schema version 21. A game update could rename these fields; if that happens, the tool skips profiles that are missing them.
