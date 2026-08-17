**English** · [Русский](README.ru.md)

# Terraria Fishing Bot

Memory-based fishing helper for Terraria **1.4.5.6** on Windows. It reads the live `Terraria.exe` process and left-clicks at a saved cast point when a whitelisted bite appears. **It does not patch the game.**

Use the default **32-bit Steam** build. Do not add `-autoarch` (that starts 64-bit Terraria; the bot will refuse to attach).

## Warning

Multiplayer use may violate the game ToS. Prefer single-player / offline.

If an older version of this project patched `Terraria.exe`, restore the original with Steam **Verify integrity of game files**. This bot will not patch or restore the game.

## Stack

| Layer | What |
|--------|------|
| Python 3.11 | Runtime |
| customtkinter + pywin32 | Window, tabs, language, `SendInput` mouse clicks |
| pymem + `ReadProcessMemory` / `WriteProcessMemory` | Attach to `Terraria.exe`, scan JIT, read/write fields |
| PyInstaller | Onedir bundle: `release\Fishing bot.exe` + `_internal\` |

## What does what

| Path | Role |
|------|------|
| [`src/Fishing bot.py`](src/Fishing%20bot.py) | GUI: Start / Stop / Cast point, catch list, statistics, settings |
| [`src/memory_bot.py`](src/memory_bot.py) | JIT scan for `FishingCheck`, read `rolledItemDrop`, save aim, click to reel/recast |
| [`src/data/catches.json`](src/data/catches.json) | Terraria item IDs and names |
| [`src/locale/`](src/locale/) | UI strings (en / ru) |
| `preferences.json` | Next to the exe: catch list, language, theme, saved cast point |
| `statistics.json` | Next to the exe: catch counts |

Flow:

```
Start → attach to Terraria.exe
  → scan JIT for FishingCheck → Projectile._context.FishingAttempt.rolledItemDrop
  → user casts once → cursor position saved (client coordinates)
  → whitelist bite → short left-click at that point (reel, then recast)
Log + statistics.json
```

## How to use

1. Launch **32-bit** Terraria, enter a world, equip a fishing rod.
2. Double-click [`release/Fishing bot.exe`](release/Fishing%20bot.exe). Keep `_internal` next to the exe.
3. Open the **Catch** tab. Tick items, use presets (Fish / Quest / Crates), or **Select All**. Start needs a non-empty list.
4. Click **Start**. Wait until the log says the hook was found.
5. Aim at the water in Terraria and **cast once**. The bot stores that point. **Cast point** clears it so you can set it again.
6. Leave Terraria in the foreground. The cursor will move to the saved point on each reel/recast. Run Terraria and the bot at the **same privilege level** (both as a normal user, or both as administrator). Otherwise Windows can block `SendInput` while memory reads still work.
7. **Stop** ends the hook.

Settings (language, theme, auto-drink / Quick Buff key) are on the **Settings** tab. Theme changes need a restart.

### Known limitation

Reel retries until `itemAnimation` starts (the first click often misses). Recast is a **single** click: a fishing cast does not raise `itemAnimation`, and a second click would reel the line back. If the log shows reel/focus failures, check the cast point and that the game and bot share the same privilege level.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

Output: `release\Fishing bot.exe`. Close any running bot first so `_internal` is not locked.

### From source (no exe)

```powershell
pip install -r requirements.txt
python "src/Fishing bot.py"
```

`Fishing bot.bat` is a fallback: it starts `release\Fishing bot.exe` if present, otherwise `pythonw`.

### GitHub Releases

Push a tag `v*` (e.g. `v0.4.0`). Workflow [`.github/workflows/release.yml`](.github/workflows/release.yml) builds the Windows zip.

```powershell
git tag v0.4.0
git push origin v0.4.0
```

## License

MIT — see [LICENSE](LICENSE).
