# AGENTS.md

Memory-reading fishing bot for **Terraria 1.4.5.8, 32-bit only**. Python 3.11
+ PySide6 + pymem, shipped as a PyInstaller onedir bundle. It reads the live
`Terraria.exe` process and left-clicks at a saved point; it does not patch the
game.

## Commands

```powershell
python "src/Fishing bot.py"                                    # run from source
python src/_sim_test.py                                        # offline loop tests
python src/_gui_smoke.py                                       # offscreen GUI test
powershell -ExecutionPolicy Bypass -File scripts/build.ps1      # build to release\
```

There is **no pytest**. `src/_sim_test.py` and `src/_gui_smoke.py` are
hand-rolled runners with their own `SCENARIOS` list and `main()`; a new test
must be appended to that list or it never runs. Both exit non-zero on failure.
Run both after touching `memory_bot.py` or the GUI.

`build.ps1` fails if `Fishing bot.exe` is running (`_internal` stays locked).
It moves `release\preferences.json` / `statistics.json` into `.build_backup\`
first, so user data survives a rebuild.

## Diagnostics (need a live game)

| Script | Needs Terraria | Purpose |
|---|---|---|
| `src/_sim_test.py`, `src/_gui_smoke.py` | no | stubs win32/pymem before importing `memory_bot` |
| `src/mem_probe.py` | yes | prints hook resolution + `rolledItemDrop` samples |
| `src/_bobber_live_dump.py` | yes, bobber in water | dumps candidate `Main.projectile` arrays |
| `src/_bobber_capture.py capture N` / `analyze` | capture only | records raw bobber bytes, then finds which field signals a bite, offline |
| `src/_ai_watch.py` | yes, bobber in water | live per-field watch for a dunk signal |

All of these are read-only: no clicks, no memory writes. They must stay that
way — a click reels the line and corrupts the measurement.

Layout: flat imports from `src/`; each `_*.py` does its own
`sys.path.insert(0, SRC)`, so run them from the repo root as shown.

## Architecture

```
src/Fishing bot.py     entry: load prefs/stats, start Qt
src/gui/               PySide6 UI; gui/window.py maps bot status strings -> log lines
src/gui/bridge.py      MemoryBot callbacks -> Qt signals
src/memory_bot.py      everything memory: JIT scan, bite detection, clicks (~2600 lines)
src/projectile_probe.py optional JSONL debug probe
```

`MemoryBot` runs on its own thread and must only talk to the GUI through
`gui/bridge.py` (signals connected with `QueuedConnection`). Never touch a
widget from the bot thread.

`gui/paths.py` decides where runtime files land: next to the exe when frozen,
in `src/` from source. That covers `preferences.json`, `statistics.json`,
`fishing_log.txt`, `memory_bot_debug.log`, `projectile_probe.jsonl`,
`bobber_layout.json`.

## Terraria memory assumptions

Hard-coded for 1.4.5.8 x86 and verified against a live process. If the game
updates, these are the first suspects:

- `FISHING_CHECK_PATTERN` — JIT signature for `FishingCheck`; scanned across
  executable pages, then the JIT heap, then all readable memory.
- `OFF_PROJ_TYPE = 0x80` (was `0x7C` on 1.4.5.6), `OFF_PROJ_OWNER = 0x5C`.
- `OFF_ITEM_ANIMATION = 0x64C`, `OFF_CONTROL_USE_ITEM = 0x782` on `Player`.
- `ROLLED_ITEM_DROP_OFF = 0x68` on `FishingAttempt`.
- **`ai` and `localAI` are `float[3]`, not `float[2]`** — fields `+0x40` and
  `+0x44`, sharing one MethodTable. `AI_DUNK_INDEX = 1`. A previous exact
  `length == 2` test matched nothing, which left the bot hooked but idle,
  never casting. Accept a range (`AI_ARRAY_LENS`), never an exact length.

A bite is the bobber dunk: `ai[1] < 0` while a fish pulls it under (~1.2-2.4s,
every 2-10s depending on fishing power). `rolledItemDrop` is only the item id
for the whitelist — the game leaves it sticky, so an unchanged id is not a new
bite, and the same fish twice in a row does not change it at all. Do not use
`rolledItemDrop` transitions to count bites when validating a change.

`_apply_bobber_layout` writes the module-level globals `OFF_PROJ_TYPE`,
`OFF_PROJ_OWNER`, `HYP_ACTIVE_OFF`. Calibrated offsets therefore leak between
tests through the module; reset them if a test depends on defaults. The same
values are cached to `bobber_layout.json` — delete it when debugging
calibration, or a stale layout will be reused.

## Click behaviour (easy to break)

- Recast is **one** click: a fishing cast does not raise `itemAnimation`, and a
  second click reels the fresh line back in.
- Recast holds LMB with a busy-wait (`RECAST_HOLD_S = 0.012`), not
  `time.sleep`: 16 ms sleeps stretch to two frames and the second use reels.
- Recast must not pulse `releaseUseItem` — that edge starts a second use.
- Terraria copies mouse state over `controlUseItem` every frame, so the bot
  pulses that flag during LMB.
- Any click brings Terraria to the foreground. The game and the bot must run at
  the same privilege level or `SendInput` is blocked while memory reads still
  work.

## Conventions

- New UI string: add the key to **both** `src/locale/en.json` and
  `src/locale/ru.json`. `I18n.t` silently returns the key itself when missing.
- Bot -> GUI messages are plain strings (`"hooked"`, `"bite:2290:skip"`,
  `"dunk_unavailable"`) decoded in `gui/window.py`; adding a status means
  adding a branch there plus the two locale keys.
- Verbose logging is opt-in via the `debug_log` preference and goes through
  `_phase()`. It runs inside a 40 Hz poll loop — throttle or latch anything you
  log from there, or the file grows tens of KB per minute.
- `preferences.json` defaults live in `gui/prefs.py` (`DEFAULT_PREFERENCES`);
  `projectile_probe` and `debug_log` default to off.
