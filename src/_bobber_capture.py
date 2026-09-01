"""Capture the live fishing bobber, then find the field that signals a dunk.

Read-only: no clicks, no memory writes, no re-hooking. Safe to leave running
while you fish by hand.

Why this exists: the current detector assumes `Projectile.ai` is a CLR
`float[2]` and that `ai[1]` goes negative on a bite. A live 1.4.5.8 dump shows
`ai`/`localAI` are `float[3]`, and a 4200-sample watch found no field that
goes negative on a bite at all. So this script stops guessing: it records raw
bytes and analyses them offline.

Two modes:

  capture  - poll the bobber and append every sample verbatim to JSONL:
             the full object prefix as hex, every small CLR array it points
             at, and rolledItemDrop. No filtering, no interpretation.

  analyze  - replay that JSONL offline. Bites are located by rolledItemDrop
             changing value; every scalar (each aligned word of the object and
             each array element) is compared against its own calm baseline,
             and the ones that deviate around bites are ranked.

Usage:

    python src/_bobber_capture.py capture [seconds]
    python src/_bobber_capture.py analyze [path]

Capture needs Terraria open with a line already in the water. Alt-tab to the
game and let two or three fish bite and escape; do NOT click, a click reels
the line. Analyze runs on the saved file and can be repeated freely.

Writes release/bobber_capture.jsonl.
"""
from __future__ import annotations

import json
import struct
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

OBJ_SCAN = 0x100          # object prefix captured verbatim
ARRAY_LEN_MAX = 16        # ai/localAI are tiny; keeps 200-byte blobs out
POLL_S = 0.03
DEFAULT_SECONDS = 180.0
OUT = ROOT / "release" / "bobber_capture.jsonl"

# --- analysis tuning -------------------------------------------------------
BITE_LEAD_S = 1.0         # window opens this long before a rolled change
BITE_TAIL_S = 1.5         # ... and closes this long after
MIN_SAMPLES = 30          # scalars polled less than this are ignored
MAX_BITE_FRACTION = 0.5   # a signal must be quiet most of the time
TOP_N = 30


def hx(n) -> str:
    return f"{int(n):#x}"


def as_float(word: int):
    """float32 view of a u32, or None if NaN / absurd."""
    (v,) = struct.unpack("<f", int(word).to_bytes(4, "little"))
    if v != v or abs(v) > 1e12:
        return None
    return v


def decode(word: int) -> str:
    raw = int(word).to_bytes(4, "little")
    signed = int.from_bytes(raw, "little", signed=True)
    parts = [f"u32={word}"]
    if signed != word:
        parts.append(f"i32={signed}")
    f = as_float(word)
    if f is not None:
        parts.append(f"f32={round(f, 5)}")
    return " ".join(parts)


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def bobber_candidates(bot, mb) -> list:
    """Projectiles whose object carries a bobber item id.

    Found by content, not by `owner == myPlayer`: Main.myPlayer resolves to 0
    when its static is not located, which hides the real bobber in
    multiplayer. whoAmI at +0x4 is skipped because slots 360-366 store a slot
    number that is also a valid bobber id.
    """
    try:
        entries = bot._raw_projectile_blobs()
    except RuntimeError:
        return []
    out = []
    for slot, ptr, blob in entries:
        offs = [
            o
            for o in range(0, len(blob) - 3, 4)
            if o != mb.HYP_WHOAMI_OFF
            and int.from_bytes(blob[o:o + 4], "little") in mb.BOBBER_TYPES
        ]
        if not offs:
            continue
        out.append({
            "slot": slot,
            "ptr": ptr,
            "type_offs": offs,
            "type_vals": [int.from_bytes(blob[o:o + 4], "little") for o in offs],
        })
    # A real bobber repeats its type id (seen live at +0x80 and +0x88); a
    # false hit from whoAmI-like data usually has a single match.
    out.sort(key=lambda b: -len(b["type_offs"]))
    return out


def read_arrays(bot, blob: bytes, user_end: int) -> dict:
    """{field_off: {mt, len, vals}} for small CLR float arrays the blob points at.

    A CLR SzArray on x86 is MethodTable @0, length @+4, element 0 @+8. Both
    the MethodTable and the length are checked so position/velocity floats,
    whose bytes happen to look like pointers, are not dereferenced as arrays.
    """
    out = {}
    for off in range(0, len(blob) - 3, 4):
        word = int.from_bytes(blob[off:off + 4], "little")
        if not (0x10000 < word < user_end):
            continue
        try:
            head = bot._read_bytes(word, 8 + ARRAY_LEN_MAX * 4)
        except RuntimeError:
            continue
        if len(head) < 12:
            continue
        mt = int.from_bytes(head[0:4], "little")
        length = int.from_bytes(head[4:8], "little")
        if not (0x10000 < mt < user_end):
            continue
        if not (1 <= length <= ARRAY_LEN_MAX):
            continue
        need = 8 + length * 4
        if len(head) < need:
            continue
        words = [
            int.from_bytes(head[8 + i * 4:12 + i * 4], "little")
            for i in range(length)
        ]
        out[hx(off)] = {
            "mt": hx(mt),
            "len": int(length),
            "words": words,
        }
    return out


def capture(seconds: float) -> int:
    import memory_bot as mb

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
        f"hooked pid={bot._pid} proj={hx(bot._projectile_static)} "
        f"rolled={hx(bot._rolled_ptr)} myPlayer={bot._my_player_id()}",
        flush=True,
    )
    if not bot._ensure_projectile_array():
        print("Main.projectile not resolvable", flush=True)
        bot._close_handles()
        return 2

    found = bobber_candidates(bot, mb)
    if not found:
        print(
            "No bobber-type projectile found. Cast a line in Terraria first, "
            "then rerun.",
            flush=True,
        )
        bot._close_handles()
        return 2
    for b in found:
        print(
            f"  bobber slot={b['slot']} ptr={hx(b['ptr'])} "
            f"type={b['type_vals']} @ {[hx(o) for o in b['type_offs']]}",
            flush=True,
        )
    print(
        f"\nRecording {seconds:.0f}s at ~{1 / POLL_S:.0f} Hz. Alt-tab to "
        "Terraria and let two or\nthree fish bite and escape. Do NOT click.\n",
        flush=True,
    )

    if OUT.exists():
        OUT.unlink()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fh = OUT.open("a", encoding="utf-8")

    def emit(rec: dict):
        fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    def read_rolled() -> int:
        if not bot._rolled_ptr:
            return 0
        try:
            return bot._read_u32(bot._rolled_ptr)
        except RuntimeError:
            return 0

    emit({
        "event": "capture_start",
        "pid": bot._pid,
        "my_player": bot._my_player_id(),
        "projectile_static": hx(bot._projectile_static),
        "rolled_ptr": hx(bot._rolled_ptr),
        "obj_scan": OBJ_SCAN,
        "array_len_max": ARRAY_LEN_MAX,
        "poll_s": POLL_S,
        "seconds": seconds,
        "bobbers": [
            {"slot": b["slot"], "ptr": hx(b["ptr"]), "type_vals": b["type_vals"]}
            for b in found
        ],
    })

    t0 = time.monotonic()
    deadline = t0 + seconds
    samples = 0
    gaps = 0
    prev_rolled = read_rolled()
    rolled_changes = 0
    heartbeat = t0
    try:
        while time.monotonic() < deadline:
            rel = time.monotonic() - t0
            live = bobber_candidates(bot, mb)
            if not live:
                gaps += 1
                if time.monotonic() - heartbeat > 5.0:
                    heartbeat = time.monotonic()
                    print(
                        f"+{rel:6.1f}s no bobber in the water - cast a line",
                        flush=True,
                    )
                time.sleep(POLL_S)
                continue
            target = live[0]
            try:
                blob = bot._read_bytes(target["ptr"], OBJ_SCAN)
            except RuntimeError:
                time.sleep(POLL_S)
                continue
            if len(blob) < OBJ_SCAN:
                time.sleep(POLL_S)
                continue
            rolled = read_rolled()
            if rolled != prev_rolled:
                rolled_changes += 1
                print(
                    f"+{rel:6.1f}s rolledItemDrop {prev_rolled} -> {rolled}",
                    flush=True,
                )
                prev_rolled = rolled
            emit({
                "event": "sample",
                "rel": round(rel, 3),
                "slot": target["slot"],
                "ptr": hx(target["ptr"]),
                "rolled": rolled,
                "hex": blob.hex(),
                "arrays": read_arrays(bot, blob, mb.USER_SPACE_END),
            })
            samples += 1
            if time.monotonic() - heartbeat > 15.0:
                heartbeat = time.monotonic()
                print(
                    f"+{rel:6.1f}s samples={samples} rolled={rolled} "
                    f"rolled_changes={rolled_changes}",
                    flush=True,
                )
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)
    finally:
        emit({
            "event": "capture_end",
            "samples": samples,
            "gaps": gaps,
            "rolled_changes": rolled_changes,
            "duration_s": round(time.monotonic() - t0, 2),
        })
        fh.close()
        bot._close_handles()

    print(
        f"\nsamples={samples} rolled_changes={rolled_changes} gaps={gaps}",
        flush=True,
    )
    print(f"wrote {OUT}", flush=True)
    if rolled_changes < 2:
        print(
            "\nFewer than two bites recorded - the analysis needs at least "
            "two. Rerun for longer.",
            flush=True,
        )
        return 1
    print(f"\nNow run:  python src/_bobber_capture.py analyze", flush=True)
    return 0


# --------------------------------------------------------------------------
# analyze
# --------------------------------------------------------------------------

def scalars_of(rec: dict) -> dict:
    """{key: u32} for every aligned object word and every array element."""
    out = {}
    blob = bytes.fromhex(rec["hex"])
    for off in range(0, len(blob) - 3, 4):
        out[("obj", off)] = int.from_bytes(blob[off:off + 4], "little")
    for off_s, arr in (rec.get("arrays") or {}).items():
        off = int(off_s, 16)
        for i, word in enumerate(arr["words"]):
            out[("arr", off, arr["len"], i)] = word
    return out


def key_label(key) -> str:
    if key[0] == "obj":
        return f"obj +{key[1]:#x}"
    return f"[+{key[1]:#x}] float[{key[2]}][{key[3]}]"


def analyze(path: Path) -> int:
    if not path.exists():
        print(f"{path} not found - run capture first.", flush=True)
        return 2

    head = {}
    tail = {}
    samples = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ev = rec.get("event")
            if ev == "capture_start":
                head = rec
            elif ev == "capture_end":
                tail = rec
            elif ev == "sample":
                samples.append(rec)
    if not samples:
        print("no samples in the file", flush=True)
        return 2

    print(f"{path.name}: {len(samples)} samples", flush=True)
    if head:
        print(
            f"pid={head.get('pid')} myPlayer={head.get('my_player')} "
            f"bobbers={head.get('bobbers')}",
            flush=True,
        )
    if tail:
        print(
            f"duration={tail.get('duration_s')}s gaps={tail.get('gaps')} "
            f"rolled_changes={tail.get('rolled_changes')}",
            flush=True,
        )

    # Bites: rolledItemDrop changing value. The field is sticky, so a change
    # is the only reliable marker; the value itself is the item id.
    bites = []
    prev = samples[0]["rolled"]
    for rec in samples[1:]:
        if rec["rolled"] != prev:
            bites.append((rec["rel"], prev, rec["rolled"]))
            prev = rec["rolled"]
    print(f"\nbites (rolledItemDrop changes): {len(bites)}", flush=True)
    for rel, old, new in bites[:12]:
        print(f"  +{rel:7.2f}s  {old} -> {new}", flush=True)
    if not bites:
        print(
            "\nNo rolledItemDrop change: no bite was recorded, nothing to "
            "correlate.",
            flush=True,
        )
        return 1

    def bite_index(rel: float):
        """Index of the bite whose window contains rel, or None."""
        for i, (brel, _o, _n) in enumerate(bites):
            if brel - BITE_LEAD_S <= rel <= brel + BITE_TAIL_S:
                return i
        return None

    # Per-scalar: values seen while calm, values seen inside a bite window.
    calm = defaultdict(Counter)
    inside = defaultdict(lambda: defaultdict(Counter))  # key -> bite -> values
    totals = Counter()
    for rec in samples:
        bi = bite_index(rec["rel"])
        for key, word in scalars_of(rec).items():
            totals[key] += 1
            if bi is None:
                calm[key][word] += 1
            else:
                inside[key][bi][word] += 1

    n_calm_samples = sum(1 for r in samples if bite_index(r["rel"]) is None)
    n_bite_samples = len(samples) - n_calm_samples
    print(
        f"\ncalm samples={n_calm_samples} bite-window samples={n_bite_samples}",
        flush=True,
    )

    ranked = []
    for key, total in totals.items():
        if total < MIN_SAMPLES:
            continue
        calm_vals = calm[key]
        if not calm_vals:
            continue
        per_bite = inside[key]
        if not per_bite:
            continue
        calm_set = set(calm_vals)
        # Bites where this scalar took a value never seen while calm.
        hits = [bi for bi, vals in per_bite.items() if set(vals) - calm_set]
        if not hits:
            continue
        novel = Counter()
        for bi in hits:
            for v, c in per_bite[bi].items():
                if v not in calm_set:
                    novel[v] += c
        novel_samples = sum(novel.values())
        ranked.append({
            "key": key,
            "hits": len(hits),
            "bites": len(bites),
            "calm_distinct": len(calm_set),
            "calm_top": calm_vals.most_common(1)[0][0],
            "novel_samples": novel_samples,
            "novel_top": novel.most_common(3),
            "total": total,
            "bite_fraction": novel_samples / max(1, n_bite_samples),
        })

    if not ranked:
        print(
            "\nNo scalar took a bite-only value. The dunk is not visible in "
            "the first\n0x100 bytes or in the small arrays - widen OBJ_SCAN / "
            "ARRAY_LEN_MAX and recapture.",
            flush=True,
        )
        return 1

    # A real signal fires on every bite, is otherwise stable, and does not
    # churn constantly (position/velocity change every frame).
    ranked.sort(
        key=lambda r: (
            -r["hits"],
            r["calm_distinct"],
            -r["novel_samples"],
        )
    )
    print(
        f"\nscalars that took a bite-only value "
        f"(hits = bites covered, of {len(bites)}):",
        flush=True,
    )
    print(
        f"  {'field':<26} {'hits':>5} {'calm_vals':>10} {'novel':>6}  example",
        flush=True,
    )
    for r in ranked[:TOP_N]:
        star = "*" if r["hits"] == len(bites) and r["calm_distinct"] <= 4 else " "
        example = decode(r["novel_top"][0][0]) if r["novel_top"] else ""
        print(
            f" {star}{key_label(r['key']):<26} {r['hits']:>5} "
            f"{r['calm_distinct']:>10} {r['novel_samples']:>6}  {example}",
            flush=True,
        )

    best = next(
        (
            r for r in ranked
            if r["hits"] == len(bites)
            and r["calm_distinct"] <= 4
            and r["bite_fraction"] <= MAX_BITE_FRACTION
        ),
        None,
    )
    print("", flush=True)
    if best is None:
        print(
            "No scalar fired on every bite while staying stable otherwise.\n"
            "Read the table above: fields marked with many calm values are "
            "position/velocity\nnoise, not the dunk signal.",
            flush=True,
        )
        return 1

    key = best["key"]
    print("=" * 70, flush=True)
    if key[0] == "arr":
        print(
            f"=> dunk signal: object field +{key[1]:#x} -> float[{key[2]}] "
            f"index {key[3]}",
            flush=True,
        )
    else:
        print(f"=> dunk signal: object word +{key[1]:#x}", flush=True)
    print(
        f"   calm value {decode(best['calm_top'])}\n"
        f"   during bites {', '.join(decode(v) for v, _ in best['novel_top'])}",
        flush=True,
    )
    print(
        f"   fired on {best['hits']}/{len(bites)} bites, "
        f"{best['calm_distinct']} distinct calm value(s)",
        flush=True,
    )
    return 0


def main(argv: list) -> int:
    mode = argv[1] if len(argv) > 1 else "capture"
    if mode == "capture":
        secs = DEFAULT_SECONDS
        if len(argv) > 2:
            try:
                secs = float(argv[2])
            except ValueError:
                pass
        return capture(secs)
    if mode == "analyze":
        path = Path(argv[2]) if len(argv) > 2 else OUT
        return analyze(path)
    print(__doc__, flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
