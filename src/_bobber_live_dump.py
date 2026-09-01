"""One-shot: attach to Terraria, list 1000/1001 pointer tables, find bobber ids.

Requires a bobber already in the water. Writes release/bobber_live_dump.json.
"""
import json
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

import memory_bot as mb  # noqa: E402

BOBBER_SCAN = 0x100
WHOAMI_SCAN = 0x40
OUT = ROOT / "release" / "bobber_live_dump.json"


def hx(n):
    return f"{int(n):#x}"


def main():
    statuses = []
    bot = mb.MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: statuses.append(s) or print("status:", s, flush=True),
        on_error=lambda e: print("error:", e, flush=True),
    )
    print("hooking Terraria...", flush=True)
    bot._hook()
    print(
        f"hooked pid={bot._pid} player_static={hx(bot._player_static)} "
        f"projectile_static={hx(bot._projectile_static)}",
        flush=True,
    )
    around = bot._player_static
    start = max(0x10000, around - mb.STATIC_SCAN_RADIUS)
    end = min(mb.USER_SPACE_END, around + mb.STATIC_SCAN_RADIUS)
    span = (end - start) & ~3
    seen_arr = set()
    arrays = []
    for off in range(0, span, 4):
        slot = start + off
        try:
            arr = bot._read_u32(slot)
        except RuntimeError:
            continue
        if not (0x10000 < arr < mb.USER_SPACE_END) or arr in seen_arr:
            continue
        try:
            length = bot._read_u32(arr + 4)
        except RuntimeError:
            continue
        if length not in mb.PROJECTILE_ARRAY_LENS:
            continue
        seen_arr.add(arr)
        n = min(mb.PROJECTILE_LIVE_SLOTS, max(0, length))
        try:
            table = bot._read_u32_table(arr + 8, n)
        except RuntimeError:
            continue
        entries = []
        for i in range(n):
            ptr = int.from_bytes(table[i * 4:i * 4 + 4], "little")
            if 0x10000 < ptr < mb.USER_SPACE_END:
                entries.append((i, ptr))
        if not entries:
            continue
        rec = {
            "static_slot": hx(slot),
            "array_ptr": hx(arr),
            "length": length,
            "non_null": len(entries),
            "dist": abs(slot - around),
            "player_rel": slot == around - mb.PROJ_STATIC_OFF_FROM_PLAYER,
            "bobbers": [],
            "whoami_votes": {},
        }
        who_votes = Counter()
        for i, ptr in entries:
            try:
                blob = bot._read_bytes(ptr, BOBBER_SCAN)
            except RuntimeError:
                continue
            for woff in range(0, min(WHOAMI_SCAN, len(blob) - 3)):
                if int.from_bytes(blob[woff:woff + 4], "little") == i:
                    who_votes[woff] += 1
                    break
            type_offs = [
                o
                for o in range(0, len(blob) - 3)
                if int.from_bytes(blob[o:o + 4], "little") in mb.BOBBER_TYPES
            ]
            if not type_offs:
                continue
            owner_offs = [
                o
                for o in range(0, len(blob) - 3)
                if int.from_bytes(blob[o:o + 4], "little") == 0
                or int.from_bytes(blob[o:o + 4], "little") < 256
            ]
            rec["bobbers"].append({
                "slot": i,
                "ptr": hx(ptr),
                "type_offs": type_offs[:8],
                "type_vals": [
                    int.from_bytes(blob[o:o + 4], "little") for o in type_offs[:8]
                ],
                "blob_prefix_hex": blob[:0x90].hex(),
            })
        rec["whoami_votes"] = {
            hx(k): v for k, v in who_votes.most_common(6)
        }
        rec["whoami_best"] = (
            hx(who_votes.most_common(1)[0][0]) if who_votes else None
        )
        rec["whoami_best_count"] = (
            who_votes.most_common(1)[0][1] if who_votes else 0
        )
        arrays.append(rec)

    arrays.sort(key=lambda r: (-len(r["bobbers"]), -r["non_null"], r["dist"]))
    out = {
        "player_static": hx(bot._player_static),
        "chosen_projectile_static": hx(bot._projectile_static),
        "my_player": bot._my_player_id(),
        "array_count": len(arrays),
        "arrays_with_bobber": sum(1 for a in arrays if a["bobbers"]),
        "arrays": arrays[:24],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(
        f"arrays={out['array_count']} with_bobber={out['arrays_with_bobber']}",
        flush=True,
    )
    for a in arrays[:8]:
        print(
            f"  {a['array_ptr']} len={a['length']} non_null={a['non_null']} "
            f"bobbers={len(a['bobbers'])} who={a['whoami_best']}"
            f"({a['whoami_best_count']}) dist={a['dist']}"
            f"{' REL' if a['player_rel'] else ''}",
            flush=True,
        )
    bot._close_handles()
    return 0 if out["arrays_with_bobber"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
