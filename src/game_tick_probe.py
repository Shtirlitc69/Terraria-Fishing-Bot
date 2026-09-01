"""Read-only search for a per-frame counter in Terraria's local Player.

This tool does not send input and never writes to Terraria.  It samples the
first 0x900 bytes of the local Player object, ranks integer fields that advance
once per game update, and writes the result to release/game_tick_probe.jsonl.

Run from the repository root while in a multiplayer world, after FishingCheck
has been JIT-compiled at least once::

    python src/game_tick_probe.py 30

Keep Terraria focused and do not click during the capture.  The resulting JSONL
is a measurement only; MemoryBot does not use a discovered candidate yet.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

import memory_bot as mb  # noqa: E402


PLAYER_SCAN = 0x900
POLL_S = 0.004
STATIC_RADIUS = 0x4000
STATIC_POLL_S = 0.020
DEFAULT_SECONDS = 30.0
OUT = ROOT / "release" / "game_tick_probe.jsonl"


class FieldTrack:
    """Compact statistics for one aligned u32 Player field."""

    def __init__(self, value: int):
        self.last = value
        self.samples = 1
        self.changes = 0
        self.rises = 0
        self.falls = 0
        self.unit_rises = 0
        self.rise_deltas: list[int] = []
        self.last_change_at: float | None = None
        self.max_gap_s = 0.0
        self.long_gaps_s: list[float] = []

    def add(self, value: int, now: float):
        self.samples += 1
        delta = int(value) - int(self.last)
        self.last = value
        if delta == 0:
            return
        if self.last_change_at is not None:
            gap = now - self.last_change_at
            self.max_gap_s = max(self.max_gap_s, gap)
            if gap >= 0.100 and len(self.long_gaps_s) < 12:
                self.long_gaps_s.append(gap)
        self.last_change_at = now
        self.changes += 1
        if delta > 0:
            self.rises += 1
            if delta == 1:
                self.unit_rises += 1
            if len(self.rise_deltas) < 2048:
                self.rise_deltas.append(delta)
        else:
            self.falls += 1


def _emit(record: dict):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.flush()


def _rank(tracks: dict[str, FieldTrack], duration_s: float) -> list[dict]:
    """Return plausible monotonic update counters, best first."""
    ranked = []
    for location, st in tracks.items():
        if st.changes < 12 or st.rises == 0:
            continue
        rise_ratio = st.rises / st.changes
        unit_ratio = st.unit_rises / st.rises
        hz = st.changes / max(duration_s, 0.001)
        # A game heartbeat changes frequently, almost always increases, and
        # normally advances by one.  Broad Hz bounds cover low-FPS machines
        # and 120+ FPS while excluding rare inventory/stat counters.
        # Static GameUpdateCount may be sampled slightly below the game rate.
        # At 120 FPS that means increments of two or three, not just one.
        if not (10.0 <= hz <= 300.0 and rise_ratio >= 0.92):
            continue
        median_delta = statistics.median(st.rise_deltas) if st.rise_deltas else 0
        score = (rise_ratio * 4.0) + (unit_ratio * 3.0) - (st.falls / st.changes)
        ranked.append({
            "location": location,
            "changes": st.changes,
            "rises": st.rises,
            "falls": st.falls,
            "hz": round(hz, 2),
            "rise_ratio": round(rise_ratio, 4),
            "unit_ratio": round(unit_ratio, 4),
            "median_rise": median_delta,
            "max_gap_ms": round(st.max_gap_s * 1000.0, 2),
            "long_gaps_ms": [round(gap * 1000.0, 2) for gap in st.long_gaps_s],
            "score": round(score, 4),
        })
    return sorted(ranked, key=lambda x: (-x["score"], -x["changes"]))


def _make_bot() -> mb.MemoryBot:
    return mb.MemoryBot(
        on_catch=lambda _item: None,
        on_status=lambda _status: None,
        on_error=lambda _error: None,
        probe_enabled=False,
    )


def main(seconds: float = DEFAULT_SECONDS) -> int:
    seconds = max(5.0, min(float(seconds), 180.0))
    print("game_tick_probe: searching for Terraria/FishingCheck ...", flush=True)
    bot = _make_bot()
    try:
        # _hook attaches and resolves static addresses only; unlike _run it
        # does not start the fishing loop, click, or write game memory.
        bot._hook()
        player = bot.local_player_ptr()
        if not player:
            print("FAIL: local Player pointer was not resolved.", flush=True)
            return 2
        first = bot._read_bytes(player, PLAYER_SCAN)
        if len(first) != PLAYER_SCAN:
            print("FAIL: cannot read local Player object prefix.", flush=True)
            return 2
        player_tracks = {
            f"Player+0x{off:x}": FieldTrack(
                int.from_bytes(first[off:off + 4], "little")
            )
            for off in range(0, PLAYER_SCAN - 3, 4)
        }
        static_base = max(0, int(bot._player_static) - STATIC_RADIUS)
        static_size = STATIC_RADIUS * 2
        static_first = bot._read_bytes(static_base, static_size)
        static_tracks = {
            f"Static{off - STATIC_RADIUS:+#x}": FieldTrack(
                int.from_bytes(static_first[off:off + 4], "little")
            )
            for off in range(0, len(static_first) - 3, 4)
        }
        start = time.monotonic()
        _emit({
            "event": "tick_probe_start",
            "pid": bot._pid,
            "player": f"0x{player:x}",
            "player_scan": PLAYER_SCAN,
            "poll_s": POLL_S,
            "static_base": f"0x{static_base:x}",
            "static_size": static_size,
            "static_poll_s": STATIC_POLL_S,
            "requested_s": seconds,
        })
        print(
            f"sampling Player @ {player:#x} for {seconds:.0f}s "
            f"({POLL_S * 1000:.0f} ms poll), Main statics ±{STATIC_RADIUS:#x} "
            f"({STATIC_POLL_S * 1000:.0f} ms poll); do not click ...",
            flush=True,
        )
        samples = 1
        read_errors = 0
        static_samples = 1 if static_tracks else 0
        static_read_errors = 0
        next_static = start
        try:
            while time.monotonic() - start < seconds:
                blob = bot._read_bytes(player, PLAYER_SCAN)
                if len(blob) != PLAYER_SCAN:
                    read_errors += 1
                    time.sleep(POLL_S)
                    continue
                samples += 1
                now = time.monotonic()
                for off, track in enumerate(player_tracks.values()):
                    track.add(
                        int.from_bytes(blob[off * 4:off * 4 + 4], "little"), now
                    )
                if static_tracks and now >= next_static:
                    static_blob = bot._read_bytes(static_base, static_size)
                    if len(static_blob) != static_size:
                        static_read_errors += 1
                    else:
                        static_samples += 1
                        for off, track in enumerate(static_tracks.values()):
                            track.add(
                                int.from_bytes(
                                    static_blob[off * 4:off * 4 + 4], "little"
                                ), now
                            )
                    next_static = now + STATIC_POLL_S
                time.sleep(POLL_S)
        except KeyboardInterrupt:
            print("interrupted; saving partial measurement", flush=True)
        elapsed = time.monotonic() - start
        candidates = _rank({**player_tracks, **static_tracks}, elapsed)
        _emit({
            "event": "tick_probe_end",
            "elapsed_s": round(elapsed, 3),
            "samples": samples,
            "read_errors": read_errors,
            "static_samples": static_samples,
            "static_read_errors": static_read_errors,
            "candidates": candidates[:30],
        })
        print(
            f"done: samples={samples}, read_errors={read_errors}, "
            f"static_samples={static_samples}, static_read_errors={static_read_errors}, "
            f"candidates={len(candidates)}",
            flush=True,
        )
        for item in candidates[:12]:
            print(
                f"  {item['location']}: {item['hz']:6.1f} Hz  "
                f"rises={item['rises']}/{item['changes']}  "
                f"unit={item['unit_ratio']:.1%}  "
                f"max_gap={item['max_gap_ms']:.1f} ms",
                flush=True,
            )
        print(f"saved: {OUT}", flush=True)
        return 0 if candidates else 3
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        bot._close_handles()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SECONDS
    raise SystemExit(main(float(arg)))
