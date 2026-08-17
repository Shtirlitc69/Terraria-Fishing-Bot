"""Temporary read-only Main.projectile diagnostic for recast closed-loop work.

Does not click, write memory, or change fishing behaviour. Armed by a GUI
switch; 2s windows start after a successful hook and at recast. Manual LMB
windows only fire over Terraria (not the bot's SendInput or Stop).
type @+0x7C and owner @+0x5C are confirmed; other prefix fields are hypothesized.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

import win32api
import win32con


USER_SPACE_END = 0x7FFFFFFF
PROBE_WINDOW_S = 2.0
PROBE_SNAPSHOT_S = 0.05
PROJECTILE_ARRAY_LENS = frozenset((1000, 1001))
PROJ_STATIC_OFF_FROM_PLAYER = 0x4C
OBJ_PREFIX = 0x80  # through type at +0x7C
HYP_WHOAMI_OFF = 0x04
HYP_ACTIVE_OFF = 0x08
HYP_WIDTH_OFF = 0x10
HYP_HEIGHT_OFF = 0x14
OFF_PROJ_OWNER = 0x5C
OFF_PROJ_TYPE = 0x7C
STATIC_SCAN_SPAN = 0x8000
STATIC_SCAN_BACK = 0x2000
MAX_SEARCH_READ_ERRORS = 5
MAX_PROJECTILES_PER_SNAPSHOT = 1001
OCCUPANCY_SAMPLE = 32
BOBBER_TYPES = frozenset(
    list(range(360, 367)) + list(range(378, 383)) + list(range(760, 765))
)


def _hex_addr(addr: int) -> str:
    return f"{int(addr):#x}"


def _rank_array_candidate(r: dict):
    near = r.get("near") or ""
    dist = int(r.get("dist") or 0)
    return (
        0 if near == "host_cache" else 1 if near == "player_rel" else 2,
        0 if r.get("length") == 1001 else 1,
        0 if dist == PROJ_STATIC_OFF_FROM_PLAYER else 1,
        -int(r.get("whoami_matches") or 0),
        -int(r.get("non_null") or 0),
        dist,
        r.get("static_slot") or 0,
    )


def append_probe_event(log_path, event: str, **fields):
    """Append one JSONL object; used for probe snapshots and hook_error."""
    rec = {"mono": time.monotonic(), "event": event}
    rec.update(fields)
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
    except OSError:
        pass


def _u32(data: bytes, off: int) -> Optional[int]:
    if off < 0 or off + 4 > len(data):
        return None
    return int.from_bytes(data[off:off + 4], "little")


def _lmb_down() -> bool:
    try:
        return bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
    except Exception:
        return False


class ProjectileProbe:
    """Background 2s projectile-array snapshots → JSONL next to the app."""

    def __init__(self, host, log_path):
        self._host = host
        self._log_path = Path(log_path)
        self._enabled = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._io_lock = threading.Lock()
        self._search_read_errors = 0
        self._post_hook = threading.Event()
        self._recast = threading.Event()

    def set_enabled(self, enabled: bool):
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    def request_post_hook(self):
        self._post_hook.set()

    def request_recast(self):
        self._recast.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ProjectileProbe",
        )
        self._thread.start()

    def stop(self):
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _emit(self, event: str, **fields):
        rec = {"mono": time.monotonic(), "event": event}
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._io_lock:
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
        except OSError:
            pass

    def _read_u32(self, addr: int) -> int:
        return self._host._read_u32(addr)

    def _read_bytes(self, addr: int, size: int) -> bytes:
        return self._host._read_bytes(addr, size)

    def _read_ptr_table(self, addr: int, count: int) -> bytes:
        read_table = getattr(self._host, "_read_u32_table", None)
        if read_table is not None:
            return read_table(addr, count)
        return self._read_bytes(addr, max(0, count) * 4)

    def _bot_clicking(self) -> bool:
        return int(getattr(self._host, "_bot_lmb_depth", 0) or 0) > 0

    def _my_player_id(self) -> int:
        idx = 0
        static = getattr(self._host, "_myplayer_static", 0) or 0
        if static:
            try:
                idx = self._read_u32(static)
            except RuntimeError as exc:
                self._emit(
                    "read_error",
                    addr=_hex_addr(static),
                    err=str(exc),
                    what="my_player",
                )
                idx = 0
        else:
            fallback = getattr(self._host, "_myplayer_fallback_idx", None)
            if fallback is not None:
                idx = int(fallback)
        if not (0 <= idx < 256):
            idx = 0
        return idx

    def _lmb_over_terraria(self) -> bool:
        try:
            return self._host._cursor_client_in_terraria() is not None
        except Exception:
            return False

    def _run(self):
        prev_lmb = False
        while not self._host.stop_event.is_set():
            if not self._enabled.is_set() or not self._host.process_handle:
                prev_lmb = _lmb_down()
                time.sleep(0.05)
                continue
            if self._recast.is_set():
                self._recast.clear()
                self._scan_window(reason="recast")
                prev_lmb = _lmb_down()
                continue
            if self._post_hook.is_set():
                self._post_hook.clear()
                self._scan_window(reason="post_hook")
                prev_lmb = _lmb_down()
                continue
            lmb = _lmb_down()
            rising = lmb and not prev_lmb
            prev_lmb = lmb
            if (
                rising
                and not self._bot_clicking()
                and self._lmb_over_terraria()
            ):
                self._scan_window(reason="manual_lmb")
                prev_lmb = _lmb_down()
                continue
            time.sleep(0.02)

    def _scan_window(self, reason: str = "manual_lmb"):
        t0 = time.monotonic()
        self._search_read_errors = 0
        dumped_ptr = set()
        my_player = self._my_player_id()
        host = self._host
        self._emit(
            "scan_start",
            rel=0.0,
            pid=int(getattr(host, "_pid", 0) or 0),
            my_player=my_player,
            player_static=_hex_addr(getattr(host, "_player_static", 0) or 0),
            myplayer_static=_hex_addr(getattr(host, "_myplayer_static", 0) or 0),
            context_static=_hex_addr(getattr(host, "_context_static_addr", 0) or 0),
            rolled_ptr=_hex_addr(getattr(host, "_rolled_ptr", 0) or 0),
            sig_match=_hex_addr(getattr(host, "_sig_match", 0) or 0),
            sig_source=getattr(host, "_sig_source", "") or "",
            sig_name="FishingCheck",
            game_assumed="1.4.5.6",
            probe_reason=reason,
            window_s=PROBE_WINDOW_S,
            snapshot_s=PROBE_SNAPSHOT_S,
            fields="confirmed:type@0x7c,owner@0x5c",
        )
        candidates = self._find_array_candidates(reason=reason)
        chosen = None
        if candidates:
            ranked = sorted(
                candidates,
                key=_rank_array_candidate,
            )
            chosen = ranked[0]
        snapshots = 0
        projectile_events = 0
        deadline = t0 + PROBE_WINDOW_S
        frame = 0
        try:
            while time.monotonic() < deadline and not self._host.stop_event.is_set():
                if self._recast.is_set() and reason != "recast":
                    break
                if not self._enabled.is_set():
                    break
                rel = time.monotonic() - t0
                if chosen is None:
                    self._emit(
                        "snapshot",
                        frame=frame,
                        rel=round(rel, 4),
                        array_ptr=None,
                        non_null=0,
                        null_slots=0,
                        hyp_active=0,
                        logged=0,
                    )
                else:
                    n_proj, non_null, null_slots, hyp_active, logged = self._snapshot_array(
                        chosen, frame, rel, my_player, dumped_ptr, reason,
                    )
                    projectile_events += n_proj
                    self._emit(
                        "snapshot",
                        frame=frame,
                        rel=round(rel, 4),
                        array_static=_hex_addr(chosen["static_slot"]),
                        array_ptr=_hex_addr(chosen["array_ptr"]),
                        length=chosen["length"],
                        non_null=non_null,
                        null_slots=null_slots,
                        hyp_active=hyp_active,
                        logged=logged,
                    )
                snapshots += 1
                frame += 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(PROBE_SNAPSHOT_S, remaining))
        finally:
            self._emit(
                "scan_end",
                rel=round(time.monotonic() - t0, 4),
                duration_s=round(time.monotonic() - t0, 4),
                snapshots=snapshots,
                projectile_events=projectile_events,
                array_candidates=len(candidates),
                search_read_errors=self._search_read_errors,
                reason=(
                    "stop" if self._host.stop_event.is_set()
                    else "recast" if self._recast.is_set() and reason != "recast"
                    else "disabled" if not self._enabled.is_set()
                    else "timeout"
                ),
            )

    def _occupancy(self, arr: int, length: int) -> dict:
        """Pointer fill plus sampled whoAmI/active. No full hex dump."""
        non_null = 0
        null_slots = 0
        whoami_matches = 0
        hyp_active = 0
        n = max(0, int(length))
        sample = set(range(min(OCCUPANCY_SAMPLE, n)))
        if n > 0:
            sample.add(n - 1)
        blob = self._read_ptr_table(arr + 8, n)
        for slot in range(n):
            if slot * 4 + 4 > len(blob):
                break
            ptr = int.from_bytes(blob[slot * 4:slot * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END):
                null_slots += 1
                continue
            non_null += 1
            if slot not in sample:
                continue
            data = self._read_bytes(ptr, 0x18)
            if len(data) <= HYP_ACTIVE_OFF:
                continue
            who = _u32(data, HYP_WHOAMI_OFF)
            if who == slot:
                whoami_matches += 1
            if data[HYP_ACTIVE_OFF] == 1:
                hyp_active += 1
        return {
            "non_null": non_null,
            "null_slots": null_slots,
            "whoami_matches": whoami_matches,
            "hyp_active": hyp_active,
        }

    def _add_candidate(self, found: dict, near: str, slot: int, arr: int, length: int, dist: int):
        key = (slot, arr, length)
        if key in found:
            return
        occ = self._occupancy(arr, length)
        rec = {
            "near": near,
            "static_slot": slot,
            "array_ptr": arr,
            "length": length,
            "dist": dist,
            **occ,
        }
        found[key] = rec
        self._emit(
            "array_candidate",
            near=near,
            static_slot=_hex_addr(slot),
            array_ptr=_hex_addr(arr),
            length=length,
            dist=dist,
            non_null=occ["non_null"],
            null_slots=occ["null_slots"],
            whoami_matches=occ["whoami_matches"],
            hyp_active=occ["hyp_active"],
            fields="hypothesized",
        )

    def _find_array_candidates(self, reason: str = "manual_lmb") -> list:
        host = self._host
        arounds = []
        player_s = getattr(host, "_player_static", 0) or 0
        ctx_s = getattr(host, "_context_static_addr", 0) or 0
        if player_s:
            arounds.append(("player_static", player_s))
        if ctx_s and ctx_s != player_s:
            arounds.append(("context_static", ctx_s))
        found = {}
        host_ps = getattr(host, "_projectile_static", 0) or 0
        if host_ps:
            try:
                arr = self._read_u32(host_ps)
                length = self._read_u32(arr + 4)
                if length in PROJECTILE_ARRAY_LENS:
                    dist = abs(host_ps - player_s) if player_s else 0
                    self._add_candidate(
                        found, "host_cache", host_ps, arr, length, dist,
                    )
            except RuntimeError as exc:
                self._search_read_errors += 1
                if self._search_read_errors <= MAX_SEARCH_READ_ERRORS:
                    self._emit(
                        "read_error",
                        addr=_hex_addr(host_ps),
                        err=str(exc),
                        what="host_projectile_static",
                    )
        rel_slot = (player_s - PROJ_STATIC_OFF_FROM_PLAYER) if player_s else 0
        if rel_slot:
            try:
                arr = self._read_u32(rel_slot)
                length = self._read_u32(arr + 4)
                if length in PROJECTILE_ARRAY_LENS:
                    self._add_candidate(
                        found,
                        "player_rel",
                        rel_slot,
                        arr,
                        length,
                        PROJ_STATIC_OFF_FROM_PLAYER,
                    )
            except RuntimeError as exc:
                self._search_read_errors += 1
                if self._search_read_errors <= MAX_SEARCH_READ_ERRORS:
                    self._emit(
                        "read_error",
                        addr=_hex_addr(rel_slot),
                        err=str(exc),
                        what="player_rel_projectile_static",
                    )
        if reason == "recast" and found:
            ranked = sorted(
                found.values(),
                key=_rank_array_candidate,
            )
            return ranked
        for near, around in arounds:
            page = around & ~0xFFF
            start = max(0x10000, page - STATIC_SCAN_BACK)
            for off in range(0, STATIC_SCAN_SPAN, 4):
                if self._host.stop_event.is_set() or not self._enabled.is_set():
                    break
                if self._recast.is_set() and reason != "recast":
                    break
                slot = start + off
                try:
                    arr = self._read_u32(slot)
                except RuntimeError as exc:
                    self._search_read_errors += 1
                    if self._search_read_errors <= MAX_SEARCH_READ_ERRORS:
                        self._emit(
                            "read_error",
                            addr=_hex_addr(slot),
                            err=str(exc),
                            what="static_slot",
                        )
                    continue
                if not (0x10000 < arr < USER_SPACE_END):
                    continue
                try:
                    length = self._read_u32(arr + 4)
                except RuntimeError as exc:
                    self._search_read_errors += 1
                    if self._search_read_errors <= MAX_SEARCH_READ_ERRORS:
                        self._emit(
                            "read_error",
                            addr=_hex_addr(arr + 4),
                            err=str(exc),
                            what="array_length",
                        )
                    continue
                if length not in PROJECTILE_ARRAY_LENS:
                    continue
                dist = abs(slot - player_s) if player_s else abs(slot - around)
                self._add_candidate(found, near, slot, arr, length, dist)
        ranked = sorted(
            found.values(),
            key=_rank_array_candidate,
        )
        return ranked

    def _snapshot_array(self, chosen, frame, rel, my_player, dumped_ptr, reason):
        arr = chosen["array_ptr"]
        length = chosen["length"]
        non_null = 0
        null_slots = 0
        hyp_active = 0
        logged = 0
        n_proj = 0
        n = max(0, int(length))
        blob = self._read_ptr_table(arr + 8, n)
        dummy_slot = n - 1 if n in PROJECTILE_ARRAY_LENS else None
        for slot in range(n):
            if reason != "recast" and self._recast.is_set():
                break
            if self._host.stop_event.is_set() or not self._enabled.is_set():
                break
            if slot * 4 + 4 > len(blob):
                self._emit(
                    "read_error",
                    addr=_hex_addr(arr + 8 + slot * 4),
                    err="short_ptr_table",
                    what="slot_ptr",
                    slot=slot,
                    frame=frame,
                    rel=round(rel, 4),
                )
                break
            ptr = int.from_bytes(blob[slot * 4:slot * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END):
                null_slots += 1
                continue
            non_null += 1
            head = self._read_bytes(ptr, HYP_ACTIVE_OFF + 1)
            type_raw = self._read_bytes(ptr + OFF_PROJ_TYPE, 4)
            active_b = head[HYP_ACTIVE_OFF] if len(head) > HYP_ACTIVE_OFF else 0
            type_val = _u32(type_raw, 0) if len(type_raw) >= 4 else None
            if active_b == 1:
                hyp_active += 1
            is_dummy = dummy_slot is not None and slot == dummy_slot
            want = (
                active_b == 1
                or type_val in BOBBER_TYPES
                or is_dummy
            )
            if not want:
                continue
            data = self._read_bytes(ptr, OBJ_PREFIX)
            if len(data) < OFF_PROJ_TYPE + 4:
                self._emit(
                    "read_error",
                    addr=_hex_addr(ptr),
                    err=f"short_read n={len(data)}",
                    what="object_prefix",
                    slot=slot,
                    frame=frame,
                    rel=round(rel, 4),
                )
                continue
            parsed = self._parse_prefix(data, slot, my_player)
            rec = {
                "frame": frame,
                "rel": round(rel, 4),
                "slot": slot,
                "ptr": _hex_addr(ptr),
                "fields": "confirmed:type@0x7c,owner@0x5c",
                "whoAmI": parsed["whoAmI"],
                "whoAmI_off": _hex_addr(HYP_WHOAMI_OFF),
                "active": parsed["active"],
                "active_off": _hex_addr(HYP_ACTIVE_OFF),
                "width": parsed["width"],
                "height": parsed["height"],
                "owner": parsed["owner"],
                "owner_off": _hex_addr(OFF_PROJ_OWNER),
                "type": parsed["type"],
                "type_off": _hex_addr(OFF_PROJ_TYPE),
                "kind": parsed["kind"],
            }
            if (not is_dummy) and ptr not in dumped_ptr:
                dumped_ptr.add(ptr)
                rec["hex"] = data[:OBJ_PREFIX].hex()
            self._emit("projectile", **rec)
            n_proj += 1
            logged += 1
            if logged >= MAX_PROJECTILES_PER_SNAPSHOT:
                break
        return n_proj, non_null, null_slots, hyp_active, logged

    def _parse_prefix(self, data: bytes, slot: int, my_player: int) -> dict:
        who = _u32(data, HYP_WHOAMI_OFF)
        active_b = data[HYP_ACTIVE_OFF] if len(data) > HYP_ACTIVE_OFF else 0
        width = _u32(data, HYP_WIDTH_OFF)
        height = _u32(data, HYP_HEIGHT_OFF)
        owner_val = _u32(data, OFF_PROJ_OWNER)
        type_val = _u32(data, OFF_PROJ_TYPE)
        if type_val in BOBBER_TYPES:
            kind = "bobber_type"
        elif active_b == 1:
            kind = "active"
        elif who == slot:
            kind = "whoami_slot"
        else:
            kind = "other"
        return {
            "whoAmI": who,
            "active": int(active_b),
            "width": width,
            "height": height,
            "type": type_val,
            "owner": owner_val,
            "kind": kind,
        }
