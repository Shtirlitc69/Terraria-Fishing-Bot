"""Diagnostic: find the memory field that signals a fishing-bobber dunk.

Read-only: no clicks, no memory writes, no re-hooking. Safe to leave running
while you fish by hand.

The old dunk detector assumed `Projectile.ai` is a CLR `float[2]` and that
`ai[1]` goes negative on a bite. On live 1.4.5.8 no `float[2]` is reachable
from the bobber object at all, so the detector never fires. This script
replaces the guess with a measurement.

Method: poll every 4-aligned word of the bobber object prefix, plus the head
of every object those words point at, and keep per-field statistics. A dunk is
a short transient, so the signal is a field that is normally non-negative but
dips negative for a moment. `rolledItemDrop` is recorded alongside for
correlation but is NOT used as a trigger (the game leaves that field sticky).

Bobbers are found by content (a bobber item id anywhere in the object), not by
`owner == myPlayer`: `Main.myPlayer` resolves to 0 when its static is not
located, which hides the real bobber in multiplayer. Every bobber-type
projectile is tracked and reported per slot.

Run with Terraria open, a rod equipped and a line already in the water:

    python src/_ai_watch.py [seconds]

Then alt-tab to Terraria and just wait for two or three natural bites. Do NOT
click: a click reels the line. Let the fish escape each time. Afterwards
alt-tab back here and read the report (also saved to release/ai_watch.jsonl).
"""
from __future__ import annotations

import json
import struct
import sys
import time
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

import memory_bot as mb  # noqa: E402

OBJ_SCAN = 0x100          # projectile object prefix to sweep
TARGET_SCAN = 0x20        # bytes read from each object a word points at
POLL_S = 0.03
DEFAULT_SECONDS = 300.0
MAX_DISTINCT = 24         # per-field value memory before it is marked varied
MIN_SAMPLES = 20          # a field must be polled this often to be judged
MAX_NEG_FRACTION = 0.6    # always-negative fields are velocities, not signals
LIVE_PRINT_COOLDOWN_S = 0.4
OUT = ROOT / "release" / "ai_watch.jsonl"


def hx(n) -> str:
    return f"{int(n):#x}"


def _emit(rec: dict):
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
    except OSError:
        pass


def as_float(word: int):
    """float32 view of a u32, or None if it is NaN / absurd."""
    (v,) = struct.unpack("<f", int(word).to_bytes(4, "little"))
    if v != v or abs(v) > 1e12:
        return None
    return round(v, 5)


def read_fields(bot, ptr: int) -> dict:
    """{key: u32} for the object prefix and the head of what it points at.

    key is ("obj", off) or ("ptr", field_off, target_off). Values stay raw
    u32 so nothing depends on guessing a type or an array header layout.
    """
    try:
        blob = bot._read_bytes(ptr, OBJ_SCAN)
    except RuntimeError:
        return {}
    if len(blob) < OBJ_SCAN:
        return {}
    out = {}
    for off in range(0, len(blob) - 3, 4):
        word = int.from_bytes(blob[off:off + 4], "little")
        out[("obj", off)] = word
        if not (0x10000 < word < mb.USER_SPACE_END):
            continue
        try:
            target = bot._read_bytes(word, TARGET_SCAN)
        except RuntimeError:
            continue
        if len(target) < 4:
            continue
        for toff in range(0, len(target) - 3, 4):
            out[("ptr", off, toff)] = int.from_bytes(
                target[toff:toff + 4], "little"
            )
    return out


def key_label(key) -> str:
    """Human label for a stats key: (slot, "obj", off) / (slot, "ptr", off, toff)."""
    slot, kind = key[0], key[1]
    if kind == "obj":
        return f"s{slot} obj +{key[2]:#x}"
    return f"s{slot} [+{key[2]:#x}]->+{key[3]:#x}"


def bobber_type_offs(blob: bytes) -> list:
    """Aligned offsets holding a bobber item id, ignoring whoAmI at +0x4."""
    return [
        off
        for off in range(0, len(blob) - 3, 4)
        if off != mb.HYP_WHOAMI_OFF
        and int.from_bytes(blob[off:off + 4], "little") in mb.BOBBER_TYPES
    ]


def find_bobbers(bot) -> list:
    """[{slot, ptr, type_offs, owner_at_5c, active_at_8}] for every projectile
    whose object carries a bobber item id.

    Deliberately layout- and owner-independent: MemoryBot._local_bobbers()
    filters on `owner == myPlayer`, and myPlayer resolves to 0 when its static
    is not found, which hides the local bobber in multiplayer. A diagnostic
    must not inherit that assumption.
    """
    try:
        entries = bot._raw_projectile_blobs()
    except RuntimeError:
        return []
    out = []
    for slot, ptr, blob in entries:
        offs = bobber_type_offs(blob)
        if not offs:
            continue
        out.append({
            "slot": slot,
            "ptr": ptr,
            "type_offs": offs,
            "type_vals": [
                int.from_bytes(blob[o:o + 4], "little") for o in offs
            ],
            "owner_at_5c": int.from_bytes(blob[0x5C:0x60], "little"),
            "active_at_8": blob[0x08] if len(blob) > 8 else 0,
        })
    return out


def decode(word: int) -> str:
    raw = int(word).to_bytes(4, "little")
    signed = int.from_bytes(raw, "little", signed=True)
    f = as_float(word)
    parts = [f"u32={word}"]
    if signed != word:
        parts.append(f"i32={signed}")
    if f is not None:
        parts.append(f"f32={f}")
    return " ".join(parts)


class FieldStat:
    __slots__ = (
        "samples", "values", "varied", "neg_samples", "neg_min",
        "neg_first_rel", "neg_rolled", "nonneg_samples",
    )

    def __init__(self):
        self.samples = 0
        self.values = Counter()
        self.varied = False
        self.neg_samples = 0
        self.nonneg_samples = 0
        self.neg_min = None
        self.neg_first_rel = None
        self.neg_rolled = set()

    def add(self, word: int, rel: float, rolled: int):
        self.samples += 1
        if not self.varied:
            self.values[word] += 1
            if len(self.values) > MAX_DISTINCT:
                self.varied = True
                self.values.clear()
        f = as_float(word)
        if f is not None and f < 0.0:
            self.neg_samples += 1
            self.neg_min = f if self.neg_min is None else min(self.neg_min, f)
            if self.neg_first_rel is None:
                self.neg_first_rel = rel
            if rolled:
                self.neg_rolled.add(rolled)
        else:
            self.nonneg_samples += 1

    @property
    def neg_fraction(self) -> float:
        return self.neg_samples / self.samples if self.samples else 0.0


def main(seconds: float = DEFAULT_SECONDS) -> int:
    bot = mb.MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: print("error:", e, flush=True),
    )
    print("hooking Terraria (JIT scan, can take a minute)...", flush=True)
    try:
        bot._hook()
    except Exception as exc:  # noqa: BLE001
        print(f"hook failed: {exc}", flush=True)
        if str(exc) == "signature_not_found":
            print(
                "Cast a line at least once so FishingCheck gets JIT-compiled, "
                "then rerun.",
                flush=True,
            )
        return 2
    print(
        f"hooked pid={bot._pid} player={hx(bot._player_static)} "
        f"proj={hx(bot._projectile_static)} rolled={hx(bot._rolled_ptr)}",
        flush=True,
    )
    if not bot._ensure_projectile_array():
        print("Main.projectile not resolvable", flush=True)
        bot._close_handles()
        return 2

    found = find_bobbers(bot)
    if not found:
        print(
            "No bobber-type projectile found. Cast a line in Terraria first, "
            "then rerun.",
            flush=True,
        )
        print(
            f"(scanned Main.projectile at {hx(bot._projectile_static)}; "
            f"myPlayer={bot._my_player_id()})",
            flush=True,
        )
        bot._close_handles()
        return 2
    if len(found) > 1:
        print(f"{len(found)} bobber-type projectiles in the world:", flush=True)
        for b in found:
            print(
                f"  slot={b['slot']} ptr={hx(b['ptr'])} "
                f"type={b['type_vals']} @ {[hx(o) for o in b['type_offs']]} "
                f"owner@0x5c={b['owner_at_5c']} active@0x8={b['active_at_8']}",
                flush=True,
            )
        print(
            "In multiplayer other players' bobbers show up too; the one that "
            "dunks while\nyou fish is yours.",
            flush=True,
        )
    target = found[0]
    slot, ptr = target["slot"], target["ptr"]
    print(
        f"watching bobber slot={slot} ptr={hx(ptr)} "
        f"type={target['type_vals']} owner@0x5c={target['owner_at_5c']}",
        flush=True,
    )
    print(
        f"\nAlt-tab to Terraria and wait for {seconds:.0f}s. Do NOT click - "
        "let two or three\nfish bite and escape on their own. Come back here "
        "afterwards.\n",
        flush=True,
    )
    _emit({
        "event": "watch_start",
        "pid": bot._pid,
        "slot": slot,
        "ptr": hx(ptr),
        "my_player": bot._my_player_id(),
        "bobbers": [
            {
                "slot": b["slot"], "ptr": hx(b["ptr"]),
                "type_vals": b["type_vals"], "owner_at_5c": b["owner_at_5c"],
            }
            for b in found
        ],
        "rolled_ptr": hx(bot._rolled_ptr),
        "obj_scan": OBJ_SCAN,
        "target_scan": TARGET_SCAN,
        "seconds": seconds,
    })

    def read_rolled() -> int:
        if not bot._rolled_ptr:
            return 0
        try:
            return bot._read_u32(bot._rolled_ptr)
        except RuntimeError:
            return 0

    stats: dict = {}
    t0 = time.monotonic()
    deadline = t0 + seconds
    polls = 0
    no_bobber_polls = 0
    heartbeat = t0
    last_live_print = 0.0
    seen_negative = set()
    rolled_seen = Counter()
    slots_seen = Counter()
    try:
        while time.monotonic() < deadline:
            rel = time.monotonic() - t0
            # Re-discover every poll: the pool reuses objects, so a recast can
            # move the bobber to a different slot/pointer.
            live = find_bobbers(bot)
            if not live:
                no_bobber_polls += 1
                if rel - (heartbeat - t0) > 5.0:
                    heartbeat = time.monotonic()
                    print(
                        f"+{rel:7.1f}s no bobber in the water - cast a line",
                        flush=True,
                    )
                time.sleep(POLL_S)
                continue
            rolled = read_rolled()
            rolled_seen[rolled] += 1
            polled_any = False
            fresh = []
            for b in live:
                slots_seen[b["slot"]] += 1
                fields = read_fields(bot, b["ptr"])
                if not fields:
                    continue
                polled_any = True
                for fkey, word in fields.items():
                    key = (b["slot"],) + fkey
                    st = stats.get(key)
                    if st is None:
                        st = stats[key] = FieldStat()
                    st.add(word, rel, rolled)
                    f = as_float(word)
                    if f is not None and f < 0.0 and key not in seen_negative:
                        seen_negative.add(key)
                        fresh.append((key, f))
            if polled_any:
                polls += 1
            if fresh and rel - last_live_print > LIVE_PRINT_COOLDOWN_S:
                last_live_print = rel
                txt = " ".join(f"{key_label(k)}={v}" for k, v in fresh[:5])
                print(
                    f"+{rel:7.1f}s rolled={rolled} first-negative: {txt}",
                    flush=True,
                )
            if time.monotonic() - heartbeat > 15.0:
                heartbeat = time.monotonic()
                print(
                    f"+{rel:7.1f}s watching... polls={polls} "
                    f"bobbers={len(live)} fields={len(stats)} rolled={rolled}",
                    flush=True,
                )
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)

    print("\n" + "=" * 70, flush=True)
    print(
        f"polls={polls} fields={len(stats)} no_bobber_polls={no_bobber_polls}",
        flush=True,
    )
    print(
        "bobber slots seen: "
        + (", ".join(f"{s}x{c}" for s, c in slots_seen.most_common(8)) or "-"),
        flush=True,
    )
    print(
        "rolledItemDrop values seen: "
        + ", ".join(f"{v}x{c}" for v, c in rolled_seen.most_common(6)),
        flush=True,
    )

    ranked = []
    for key, st in stats.items():
        if st.samples < MIN_SAMPLES:
            continue
        if not st.neg_samples or st.neg_fraction > MAX_NEG_FRACTION:
            continue
        ranked.append((key, st))
    # A dunk is transient and coincides with a rolled item id: prefer fields
    # that were negative only briefly and only while rolled was nonzero.
    ranked.sort(
        key=lambda kv: (
            0 if kv[1].neg_rolled else 1,
            kv[1].neg_fraction,
            -kv[1].samples,
        )
    )

    if ranked:
        print(
            "\ntransient negative fields (best dunk candidates first):",
            flush=True,
        )
        for key, st in ranked[:25]:
            mark = "*" if st.neg_rolled else " "
            print(
                f" {mark} {key_label(key):<26} neg={st.neg_samples}/{st.samples}"
                f" ({st.neg_fraction:.1%})  min={st.neg_min}"
                f"  first=+{st.neg_first_rel:.1f}s"
                f"  rolled_during_neg={sorted(st.neg_rolled) or '-'}",
                flush=True,
            )
        best_key, best = ranked[0]
        if best_key[1] == "ptr":
            field_off, target_off = best_key[2], best_key[3]
            print(
                f"\n=> candidate dunk signal: object field +{field_off:#x} "
                f"-> target offset +{target_off:#x}, read as float32, "
                f"min {best.neg_min}",
                flush=True,
            )
            print(
                "   (a CLR array puts its length at +0x4 and element 0 at "
                f"+0x8, so +{target_off:#x} is element "
                f"{max(0, (target_off - 8) // 4)} if that object is a float[])",
                flush=True,
            )
        else:
            print(
                f"\n=> candidate dunk signal: inline float32 at object "
                f"+{best_key[2]:#x}, min {best.neg_min}",
                flush=True,
            )
    elif polls:
        print(
            "\nNo transient negative field. Either no bite happened, or the "
            "dunk is not\na negative float in the first 0x100 bytes - widen "
            "OBJ_SCAN / TARGET_SCAN.",
            flush=True,
        )
    else:
        print("\nNo samples collected - was a bobber ever in the water?", flush=True)

    _emit({
        "event": "watch_end",
        "polls": polls,
        "fields": len(stats),
        "slots_seen": {str(s): c for s, c in slots_seen.most_common(10)},
        "rolled_seen": {str(v): c for v, c in rolled_seen.most_common(10)},
        "candidates": [
            {
                "field": key_label(key),
                "key": [str(x) for x in key],
                "neg_samples": st.neg_samples,
                "samples": st.samples,
                "neg_fraction": round(st.neg_fraction, 4),
                "neg_min": st.neg_min,
                "neg_first_rel": st.neg_first_rel,
                "rolled_during_neg": sorted(st.neg_rolled),
                "varied": st.varied,
                "top_values": [decode(v) for v, _ in st.values.most_common(3)],
            }
            for key, st in ranked[:40]
        ],
    })
    print(f"\nwrote {OUT}", flush=True)
    bot._close_handles()
    return 0 if ranked else 1


if __name__ == "__main__":
    secs = DEFAULT_SECONDS
    if len(sys.argv) > 1:
        try:
            secs = float(sys.argv[1])
        except ValueError:
            pass
    raise SystemExit(main(secs))
