"""Memory-based fishing bot for Terraria 1.4.5.x (replaces the IL-patch path).

A bite is the fishing bobber dunk: Projectile.ai[1] goes negative
(AI_061_FishingBobber). rolledItemDrop is only the item id for the
whitelist — the game leaves that field sticky, so a restored id is not a
bite. One reel click on dunk, then a bobber-verified recast. Recast must not pulse
releaseUseItem: that edge starts a second use and reels the fresh bobber.

ai and localAI are float[maxAI] with maxAI == 3, not float[2]. A 300s live
1.4.5.8 capture put ai at field +0x40 and localAI at +0x44, both float[3]
sharing one MethodTable; ai[1] was negative only while a fish pulled the
bobber under, and localAI[1] mirrored rolledItemDrop during every dunk
(2108/2108 samples). Requiring an exact length of 2 matched nothing and left
the bot hooked but idle, never casting. If those offsets stop resolving the
loop degrades to the older rule (a change of rolledItemDrop is a bite) and
reports `dunk_unavailable`.

Field offsets and Main.player were recovered from the 1.4.5.6 x86 JIT:
controlUseItem is +0x782 on Player; itemAnimation is +0x64C; Main.player is
the static Player[256] near Projectile._context; Main.myPlayer is the static
int paired with that array in JIT. Projectile.type is +0x80 on 1.4.5.8.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import struct
import threading
import time
from collections import Counter
from typing import Callable, Optional

import pymem
import win32api
import win32con
import win32gui
import win32process


MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100
EXECUTABLE_PROTECT = {
    PAGE_EXECUTE,
    PAGE_EXECUTE_READ,
    PAGE_EXECUTE_READWRITE,
    PAGE_EXECUTE_WRITECOPY,
}
READABLE_PROTECT = EXECUTABLE_PROTECT | {
    PAGE_READONLY,
    PAGE_READWRITE,
    PAGE_WRITECOPY,
}

# 32-bit CLR JIT typically lands in this band (live 1.4.5.6 match was ~0x2bxxxxxx).
JIT_HEAP_START = 0x10000000
JIT_HEAP_END = 0x50000000
USER_SPACE_END = 0x7FFFFFFF

# Player instance offsets on Terraria 1.4.5.6 x86 (from live JIT / object dump).
# Entity.width/height at +0x10/+0x14 are 20/42 for a player.
OFF_ENTITY_WIDTH = 0x10
OFF_ENTITY_HEIGHT = 0x14
OFF_ITEM_ANIMATION = 0x64C
OFF_CONTROL_USE_ITEM = 0x782
OFF_RELEASE_USE_ITEM = 0x792  # release* cluster after control*; edge for ItemCheck
# Multiplayer 1.4.5.8 x86: monotonically increments once per Player update.
# This is only an input-frame marker; it is not used for bite detection.
OFF_PLAYER_FRAME_TICK = 0x228
PLAYER_WIDTH = 20
PLAYER_HEIGHT = 42
PLAYER_HEIGHTS = (42, 23, 24)  # standing / sitting-ish
HYP_WHOAMI_OFF = 0x04
HYP_ACTIVE_OFF = 0x08
OFF_PROJ_OWNER = 0x5C
# Live 1.4.5.8 dump: type moved +4 from the 1.4.5.6 slot (0x7C -> 0x80).
OFF_PROJ_TYPE = 0x80
_DEFAULT_LAYOUT = {
    "active_off": HYP_ACTIVE_OFF,
    "owner_off": OFF_PROJ_OWNER,
    "type_off": OFF_PROJ_TYPE,
    "ai_offs": [],
}
# CLR SzArray<float> on x86: MT @0, length @+4, float[0] @+8, float[1] @+12.
CLR_SZARRAY_LEN_OFF = 4
CLR_SZARRAY_F0_OFF = 8
CLR_SZARRAY_F1_OFF = 12
# Projectile.ai / localAI are float[maxAI] and maxAI is 3, not 2. A live
# 1.4.5.8 capture (300s, 4214 samples) found ai at field +0x40 and localAI at
# +0x44, both float[3] sharing one MethodTable; no float[2] is reachable from
# the object at all. Accept a small range so a future maxAI change degrades
# instead of silently disabling bite detection.
AI_ARRAY_LENS = frozenset((2, 3, 4))
# ai[1] is the dunk depth: negative while a fish pulls the bobber under.
# Confirmed against localAI[1], which mirrors rolledItemDrop during a dunk
# (2108/2108 samples matched, and ai[1] was never negative with rolled == 0).
AI_DUNK_INDEX = 1
STATIC_SCAN_RADIUS = 0x40000  # ±256 KiB around a context static
PROJECTILE_ARRAY_LEN = 1001
PROJECTILE_ARRAY_LENS = frozenset((1000, 1001))
# Content-based validation of a candidate Main.projectile array (see
# _projectile_array_richness). Live data: the real array is length 1001,
# dense, with many whoAmI==index matches. A nearby length-1000 table with
# ~257 non-null slots and whoami_matches=0 is not Main.projectile. Dummy-only
# 1001 (slot 1000 only) is an empty-but-valid table: keep the player-rel
# slot rather than switching to a dense decoy.
PROJECTILE_VALIDATE_SLOTS = 300
PROJECTILE_MIN_NON_NULL = 20
PROJECTILE_MIN_WHOAMI_MATCH = 10
PROJECTILE_LIVE_SLOTS = 1000  # dummy extra slot 1000 is ignored
# 1.4.5.6 was 0x4C; 1.4.5.8 live dump used 0x48. Try both.
PROJ_STATIC_OFF_FROM_PLAYER = 0x4C
PROJ_STATIC_OFFS_FROM_PLAYER = (0x48, 0x4C)
PROJ_OCCUPANCY_SAMPLE = 32
PROJ_OCCUPANCY_MIN = 2  # unused as a recast gate; dummy-only 1001 is valid
BOBBER_OBJ_PREFIX = 0x90  # through type at +0x80 and the duplicate at +0x88
BOBBER_TYPES = frozenset(
    list(range(360, 367)) + list(range(378, 383)) + list(range(760, 765))
    + [775] + list(range(986, 994))
)
BOBBER_CALIB_TIMEOUT_S = 60.0
BOBBER_CALIB_MAX_CANDIDATES = 5
BOBBER_CALIB_SCAN_LEN = 0x100
BOBBER_CALIB_STABLE_FRAMES = 3
BOBBER_CALIB_EXTRA_S = 0.5
CALIB_POLL_S = 0.05
LAYOUT_CACHE_NAME = "bobber_layout.json"
ROLLED_ITEM_DROP_OFF = 0x68
HOOK_ERROR_CANDIDATES_MAX = 24


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL

CLICK_HOLD_S = 0.03
AIM_SETTLE_S = 0.05
REEL_RETRY_PAUSE_S = 0.3
CATCH_DELAY_S = 0.25
LINE_SNAP_GRACE_S = 0.6
RECAST_INTERVAL_S = 1.0
RECAST_SETTLE_S = 1.0
# The two animation waits plus the catch/interval pause must stay within
# roughly two seconds. Bobber verification still blocks an unsafe recast.
RECAST_ANIM_TIMEOUT_S = 0.5
WAIT_ANIM_CLEAR_S = 0.25
# Wait for actual Player updates instead of a Windows-clock hold.  A stalled
# game therefore produces no click, rather than a delayed second use.
RECAST_TICK_TIMEOUT_S = 0.50
RECAST_STABLE_S = 0.160
RECAST_OLD_BOBBER_WAIT_S = 0.5
RECAST_POLL_S = 0.002
RECAST_MAX_CLICKS = 2
RECAST_RETRY_PAUSE_S = 0.80
BOBBER_WAIT_S = 1.0
USE_ITEM_PULSE_S = 0.002
AI_OFFS_RETRY_S = 2.0  # poll-loop throttle for ai-offset rediscovery
DUNK_FALLBACK_AFTER_S = 20.0  # then bite = rolledItemDrop changed
INPUT_MOUSE = 0
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("mi", MOUSEINPUT),
    ]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT

ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
ReadProcessMemory.restype = wt.BOOL

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
WriteProcessMemory.restype = wt.BOOL

VirtualQueryEx = kernel32.VirtualQueryEx
VirtualQueryEx.argtypes = [
    wt.HANDLE, ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t,
]
VirtualQueryEx.restype = ctypes.c_size_t


class ScanAborted(Exception):
    """Raised when the user hits Stop during a JIT scan."""


_HOOK_CACHE: dict = {
    "pid": None,
    "rolled_ptr": 0,
    "context_static": 0,
    "player_static": 0,
    "myplayer_static": 0,
    "sig_match": 0,
    "sig_source": "",
    "projectile_static": 0,
    "bobber_layout": None,  # active/owner/type/ai_offs after calib
}


FISHING_CHECK_PATTERN = [
    0x55,
    0x8B, 0xEC,
    0x57,
    0x56,
    0x50,
    0x8B, 0xF9,
    0x8B, 0x35, None, None, None, None,
    0x8B, 0xCF,
    0x8B, 0xD6,
]


def _is_wow64_process(process_handle) -> bool:
    flag = ctypes.c_int(0)
    try:
        kernel32.IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
        kernel32.IsWow64Process.restype = wt.BOOL
        if kernel32.IsWow64Process(process_handle, ctypes.byref(flag)):
            return bool(flag.value)
    except Exception:
        pass
    return True


def _iter_regions(
    process_handle,
    base=0,
    end=USER_SPACE_END,
    *,
    protect_set=EXECUTABLE_PROTECT,
):
    addr = base
    mbi = MEMORY_BASIC_INFORMATION()
    while addr < end:
        bytes_returned = VirtualQueryEx(
            process_handle, ctypes.c_void_p(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi),
        )
        if bytes_returned == 0:
            break
        region_base = mbi.BaseAddress or 0
        region_size = mbi.RegionSize
        if region_size == 0:
            break
        protect = mbi.Protect
        base_prot = protect & 0xFF
        if (
            mbi.State == MEM_COMMIT
            and not (protect & PAGE_GUARD)
            and base_prot != PAGE_NOACCESS
            and base_prot in protect_set
        ):
            yield region_base, region_size
        next_addr = region_base + region_size
        if next_addr <= addr:
            break
        addr = next_addr


def _iter_executable_regions(process_handle, base=0, end=USER_SPACE_END):
    yield from _iter_regions(
        process_handle, base, end, protect_set=EXECUTABLE_PROTECT
    )


def _find_pattern(data: bytes, pattern) -> Optional[int]:
    plen = len(pattern)
    if plen == 0 or len(data) < plen:
        return None
    first = pattern[0]
    needle = None if first is None else bytes([first])
    start = 0
    limit = len(data) - plen + 1
    while start <= limit - 1:
        if needle is not None:
            idx = data.find(needle, start, limit)
            if idx < 0 or idx >= limit:
                return None
            start = idx
        ok = True
        for j, p in enumerate(pattern):
            if p is not None and data[start + j] != p:
                ok = False
                break
        if ok:
            return start
        start += 1
    return None


def pattern_scan_iter(
    process_handle,
    pattern,
    *,
    chunk=0x200000,
    stop_event: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    base: int = 0,
    end: int = USER_SPACE_END,
    protect_set=EXECUTABLE_PROTECT,
):
    """Yield every pattern match; caller decides which one is valid."""
    regions = [
        (b, s) for b, s in _iter_regions(
            process_handle, base, end, protect_set=protect_set
        )
        if b < end
    ]
    total = sum(size for _, size in regions) or 1
    scanned = 0
    overlap = max(1, len(pattern) - 1)
    last_pct_report = -1
    last_progress_t = 0.0

    for region_base, region_size in regions:
        if stop_event is not None and stop_event.is_set():
            raise ScanAborted()
        region_end = min(region_base + region_size, end)
        cur = max(region_base, base)
        while cur < region_end:
            if stop_event is not None and stop_event.is_set():
                raise ScanAborted()
            read_size = min(chunk, region_end - cur)
            buf = (ctypes.c_ubyte * read_size)()
            n_read = ctypes.c_size_t(0)
            if not ReadProcessMemory(
                process_handle, ctypes.c_void_p(cur), buf, read_size,
                ctypes.byref(n_read),
            ) or n_read.value < len(pattern):
                cur += max(read_size, 1)
                continue
            data = bytes(buf[:n_read.value])
            skip = overlap if cur > max(region_base, base) else 0
            start = skip
            while True:
                rel = _find_pattern(data[start:], pattern)
                if rel is None:
                    break
                idx = start + rel
                yield cur + idx
                start = idx + 1
            cur += max(read_size - overlap, 1)
        scanned += region_size
        pct = int(scanned * 100 / total)
        now = time.monotonic()
        if on_progress is not None and (
            pct >= last_pct_report + 5 or now - last_progress_t >= 1.5
        ):
            last_pct_report = pct
            last_progress_t = now
            on_progress(min(pct, 99))


def pattern_scan(
    process_handle,
    pattern,
    *,
    chunk=0x200000,
    stop_event: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    base: int = 0,
    end: int = USER_SPACE_END,
    protect_set=EXECUTABLE_PROTECT,
) -> Optional[int]:
    for addr in pattern_scan_iter(
        process_handle,
        pattern,
        chunk=chunk,
        stop_event=stop_event,
        on_progress=on_progress,
        base=base,
        end=end,
        protect_set=protect_set,
    ):
        return addr
    return None


def _parse_aim(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return (int(value[0]), int(value[1]))
    except (TypeError, ValueError):
        return None


class MemoryBot:
    """Background memory reader + LMB reel/recast for one Terraria process."""

    def __init__(
        self,
        on_catch,
        on_status,
        on_error,
        *,
        whitelist_ids=None,
        poll_interval=0.025,
        aim_client=None,
        on_aim=None,
        probe_enabled=False,
        probe_log_path=None,
        on_input_busy=None,
        debug_log_path=None,
        layout_cache_path=None,
        restore_minimized_window=False,
    ):
        self.on_catch = on_catch
        self.on_status = on_status
        self.on_error = on_error
        self.on_input_busy = on_input_busy
        self.whitelist_ids = set(whitelist_ids or [])
        self.poll_interval = poll_interval
        self.aim_client = _parse_aim(aim_client)
        self.on_aim = on_aim
        self._probe_enabled = bool(probe_enabled)
        self._probe_log_path = probe_log_path
        self._debug_lock = threading.Lock()
        self._debug_path = debug_log_path
        self._layout_cache_path = layout_cache_path
        self._restore_minimized_window = bool(restore_minimized_window)

        self.stop_event = threading.Event()
        self._need_aim = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self._focus_fail_notified = False
        self._last_logged_rolled = None
        self._anim_ever_rose = False
        self._bobber_verified_reel = False
        self._failed_probe_done = False
        self._bobber_verified_layout = False
        self._t0 = 0.0
        self._ai_offs: list = []
        self._ai_offs_retry_at = 0.0
        self._ai_offs_fail_count = 0
        self._ai_read_ok = False
        self._dunk_fallback = False
        self._fallback_last_id = 0
        self._dunk_latched = False
        # A filtered bite is deliberately not reeled.  Keep watching its
        # bobber: a missed bite normally leaves it in the water, while a
        # snapped/despawned line needs one fresh cast.
        self._skip_recast_pending = False
        self._skip_bobber_missing_at = 0.0
        self._bot_lmb_depth = 0
        self._probe = None
        self._sig_match = 0
        self._sig_source = ""
        self._myplayer_fallback_idx = None
        self._projectile_static = 0
        self.bobber_layout: Optional[dict] = None

        self.pm: Optional[pymem.Pymem] = None
        self.process_handle = None
        self._pid = 0
        self._rolled_ptr: int = 0
        self._context_static_addr: int = 0
        self._player_static: int = 0
        self._myplayer_static: int = 0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self._need_aim.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="MemoryBot")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._need_aim.set()
        # Safety: if the thread died mid-click (or is stuck in SendInput),
        # make sure the physical LMB state and controlUseItem are cleared.
        self._lmb_release()
        self._set_input_busy(False)
        if self.thread:
            self.thread.join(timeout=15.0)
            self.thread = None
        self._close_handles()

    def request_aim(self):
        self.aim_client = None
        self._need_aim.set()

    def set_probe_enabled(self, enabled: bool):
        self._probe_enabled = bool(enabled)
        if self._probe is not None:
            self._probe.set_enabled(self._probe_enabled)

    def set_debug_enabled(self, enabled: bool, path: str):
        self._debug_path = path if enabled else None

    def set_restore_minimized_window(self, enabled: bool):
        self._restore_minimized_window = bool(enabled)

    def _start_probe(self):
        if not self._probe_log_path or self._probe is not None:
            return
        from projectile_probe import ProjectileProbe

        self._probe = ProjectileProbe(self, self._probe_log_path)
        self._probe.set_enabled(self._probe_enabled)
        self._probe.start()
        if self._probe_enabled:
            self._probe.request_post_hook()

    def _stop_probe(self):
        probe = self._probe
        self._probe = None
        if probe is not None:
            probe.set_enabled(False)
            probe.stop()

    def _run(self):
        try:
            self._hook()
        except ScanAborted:
            self.on_status("scan_aborted")
            return
        except Exception as exc:
            if self.stop_event.is_set():
                self.on_status("scan_aborted")
                self._close_handles()
                return
            self.on_error(str(exc))
            self._close_handles()
            return

        if self.stop_event.is_set():
            return

        self.on_status("hooked")
        self._t0 = time.monotonic()
        self._start_probe()
        self._debug_log("hooked")
        self._log_hook_addrs()
        self._dunk_latched = False
        self._phase("loop_start")
        try:
            if not self.aim_client:
                if not self._capture_aim():
                    return
                self._emit_aim()
            self._sanitize_start()
            self._log_hook_addrs()
            while not self.stop_event.is_set():
                if self._need_aim.is_set():
                    self._need_aim.clear()
                    self.aim_client = None
                    if not self._capture_aim():
                        return
                    self._emit_aim()
                    self._dunk_latched = False
                    self._fallback_last_id = 0
                    self._skip_recast_pending = False
                    self._skip_bobber_missing_at = 0.0
                    self._phase("aim_reset")
                    continue
                if not self.aim_client:
                    time.sleep(self.poll_interval)
                    continue
                if self._skip_recast_pending:
                    # Do not infer that a missing array means a missing
                    # bobber.  Input must stay fail-closed while memory is
                    # unverifiable.
                    bobbers = self._local_bobbers()
                    if bobbers is None:
                        time.sleep(self.poll_interval)
                        continue
                    if bobbers:
                        self._skip_bobber_missing_at = 0.0
                    else:
                        now = time.monotonic()
                        if not self._skip_bobber_missing_at:
                            self._skip_bobber_missing_at = now
                            self._phase("skip_bobber_missing")
                        elif now - self._skip_bobber_missing_at >= RECAST_OLD_BOBBER_WAIT_S:
                            self._skip_recast_pending = False
                            self._skip_bobber_missing_at = 0.0
                            self._clear_bite_latch()
                            self._set_input_busy(True)
                            try:
                                if not self._pause_then_recast():
                                    break
                            finally:
                                self._set_input_busy(False)
                            continue
                item_id = self._next_bite()
                if item_id is None:
                    time.sleep(self.poll_interval)
                    continue
                allow = item_id in self.whitelist_ids
                self._phase(f"bite id={item_id} allow={int(allow)}")
                self._probe_failed_fishing_flag()
                self._set_input_busy(True)
                try:
                    if not allow:
                        self.on_status(f"bite:{item_id}:skip")
                        if self._dunk_fallback:
                            # In multiplayer fallback mode the client table
                            # can be empty even while the visible bobber is
                            # still in water. Never infer its disappearance
                            # and never send a follow-up click for a skip.
                            self._phase("skip_wait_rolled_fallback")
                            continue
                        self._phase("skip_wait_for_bobber")
                        self._skip_recast_pending = True
                        self._skip_bobber_missing_at = 0.0
                        continue
                    self._phase(f"reel_begin id={item_id}")
                    self._phase("reel_click attempt=1/1")
                    self._click_left("reel")
                    self._phase("reel_ok wait_anim")
                    self._wait_animation_clear()
                    if self.stop_event.is_set():
                        break
                    self._phase(
                        f"pause_before_recast "
                        f"catch={CATCH_DELAY_S}s interval={RECAST_INTERVAL_S}s"
                    )
                    self._sleep_interruptible(CATCH_DELAY_S)
                    self._sleep_interruptible(RECAST_INTERVAL_S)
                    self._clear_bite_latch()
                    self.on_catch(item_id)
                    self._wait_animation_clear(RECAST_ANIM_TIMEOUT_S)
                    if self.stop_event.is_set():
                        break
                    self._phase("recast_click")
                    if not self._recast_until_bobber():
                        break
                    self._dunk_latched = False
                    self._phase("back_to_loop")
                finally:
                    self._set_input_busy(False)
                time.sleep(self.poll_interval)
        finally:
            self._close_handles()

    def _hook(self):
        try:
            self.pm = pymem.Pymem("Terraria.exe")
        except pymem.exception.ProcessNotFound:
            raise RuntimeError("terraria_not_running")
        self.process_handle = self.pm.process_handle
        self._pid = self.pm.process_id

        if ctypes.sizeof(ctypes.c_void_p) == 8 and not _is_wow64_process(
            self.process_handle
        ):
            raise RuntimeError("terraria_is_64bit")

        if self._try_cached_hook():
            return

        last_progress = [0.0]

        def on_progress(pct: int):
            now = time.monotonic()
            if now - last_progress[0] >= 1.5:
                last_progress[0] = now
                self.on_status(f"progress:{pct}")

        bands = (
            (
                "scanning",
                "executable",
                {"protect_set": EXECUTABLE_PROTECT},
            ),
            (
                "scanning_heap",
                "heap",
                {
                    "base": JIT_HEAP_START,
                    "end": JIT_HEAP_END,
                    "protect_set": READABLE_PROTECT,
                },
            ),
            (
                "scanning_heap_full",
                "heap_full",
                {"protect_set": READABLE_PROTECT},
            ),
        )
        candidates = []
        any_match = False
        any_context = False
        for status, source, kwargs in bands:
            self.on_status(status)
            for match in pattern_scan_iter(
                self.process_handle,
                FISHING_CHECK_PATTERN,
                stop_event=self.stop_event,
                on_progress=on_progress,
                **kwargs,
            ):
                any_match = True
                info = self._validate_fishing_match(match)
                if len(candidates) < HOOK_ERROR_CANDIDATES_MAX:
                    candidates.append({
                        "match": f"{match:#x}",
                        "source": source,
                        "fail": info.get("fail"),
                        "context_static": (
                            f"{info['context_static']:#x}"
                            if info.get("context_static") else None
                        ),
                    })
                if info.get("context"):
                    any_context = True
                if info.get("player_static"):
                    self._apply_validated_hook(match, source, info)
                    return
        if not any_match:
            self._emit_hook_error("signature_not_found", candidates)
            raise RuntimeError("signature_not_found")
        if not any_context:
            self._emit_hook_error("context_null", candidates)
            raise RuntimeError("context_null")
        self._emit_hook_error("player_not_found", candidates)
        raise RuntimeError("player_not_found")

    def _try_cached_hook(self) -> bool:
        if _HOOK_CACHE.get("pid") != self._pid:
            return False
        rolled = _HOOK_CACHE.get("rolled_ptr") or 0
        ctx = _HOOK_CACHE.get("context_static") or 0
        player_s = _HOOK_CACHE.get("player_static") or 0
        if not rolled or not ctx or not player_s:
            return False
        try:
            self._read_u32(rolled)
            self._read_u32(player_s)
        except RuntimeError:
            return False
        try:
            arr = self._read_u32(player_s)
            if self._player_array_score(arr) <= 0:
                return False
        except RuntimeError:
            return False
        self._rolled_ptr = rolled
        self._context_static_addr = ctx
        self._player_static = player_s
        self._myplayer_static = _HOOK_CACHE.get("myplayer_static") or 0
        self._sig_match = _HOOK_CACHE.get("sig_match") or 0
        self._sig_source = "cached"
        self._myplayer_fallback_idx = _HOOK_CACHE.get("myplayer_fallback_idx")
        self._projectile_static = _HOOK_CACHE.get("projectile_static") or 0
        self.bobber_layout = _HOOK_CACHE.get("bobber_layout")
        if self._projectile_static and not self._projectile_static_ok(
            self._projectile_static
        ):
            self._projectile_static = 0
        if not self._projectile_static:
            self._projectile_static = self._resolve_projectile_static(
                self._player_static, scan=True
            )
        if self._projectile_static:
            _HOOK_CACHE["projectile_static"] = self._projectile_static
        return True

    def _validate_fishing_match(self, match: int) -> dict:
        info = {"match": match}
        try:
            ctx_static = self._read_u32(match + 10)
        except RuntimeError as exc:
            info["fail"] = f"imm_read:{exc}"
            return info
        info["context_static"] = ctx_static
        try:
            ctx_obj = self._read_pointer(ctx_static)
        except RuntimeError as exc:
            info["fail"] = f"ctx_read:{exc}"
            return info
        if not ctx_obj:
            info["fail"] = "context_null"
            return info
        info["context"] = ctx_obj
        player_static = self._find_player_array_static(ctx_static)
        if not player_static:
            info["fail"] = "player_not_found"
            return info
        info["player_static"] = player_static
        info["rolled_ptr"] = ctx_obj + ROLLED_ITEM_DROP_OFF
        return info

    def _apply_validated_hook(self, match: int, source: str, info: dict):
        self._sig_match = match
        self._sig_source = source
        self._context_static_addr = info["context_static"]
        self._rolled_ptr = info["rolled_ptr"]
        self._player_static = info["player_static"]
        self._myplayer_static = self._find_myplayer_static(self._player_static)
        self._myplayer_fallback_idx = None
        if not self._myplayer_static:
            self._myplayer_fallback_idx = self._guess_myplayer_index()
        self._projectile_static = self._resolve_projectile_static(
            self._player_static, scan=True
        )
        _HOOK_CACHE.update({
            "pid": self._pid,
            "rolled_ptr": self._rolled_ptr,
            "context_static": self._context_static_addr,
            "player_static": self._player_static,
            "myplayer_static": self._myplayer_static,
            "myplayer_fallback_idx": self._myplayer_fallback_idx,
            "sig_match": self._sig_match,
            "sig_source": self._sig_source,
            "projectile_static": self._projectile_static,
        })
        if self.bobber_layout:
            _HOOK_CACHE["bobber_layout"] = dict(self.bobber_layout)

    def _apply_bobber_layout(self, layout: Optional[dict]):
        """Install calibrated offsets (None reverts to the built-in ones)."""
        global OFF_PROJ_TYPE, OFF_PROJ_OWNER, HYP_ACTIVE_OFF
        if layout:
            OFF_PROJ_TYPE = int(layout["type_off"])
            OFF_PROJ_OWNER = int(layout["owner_off"])
            HYP_ACTIVE_OFF = int(layout["active_off"])
            self._ai_offs = [int(x) for x in (layout.get("ai_offs") or [])]
            self._phase(
                f"bobber_layout type@{OFF_PROJ_TYPE:#x} "
                f"owner@{OFF_PROJ_OWNER:#x} active@{HYP_ACTIVE_OFF:#x} "
                f"ai_offs={','.join(f'{o:#x}' for o in self._ai_offs) or '-'}"
            )
            stored = dict(layout)
            stored["ai_offs"] = list(self._ai_offs)
            self.bobber_layout = stored
            _HOOK_CACHE["bobber_layout"] = dict(stored)
            return
        OFF_PROJ_TYPE = _DEFAULT_LAYOUT["type_off"]
        OFF_PROJ_OWNER = _DEFAULT_LAYOUT["owner_off"]
        HYP_ACTIVE_OFF = _DEFAULT_LAYOUT["active_off"]
        self._ai_offs = []
        self.bobber_layout = None
        _HOOK_CACHE["bobber_layout"] = None

    def _read_live_projectiles(self, need_blob: bool = False) -> list:
        """Active projectiles owned by the local player as (slot, ptr[, blob]).

        Uses the calibrated layout when present; falls back to the built-in
        offsets otherwise.
        """
        layout = self.bobber_layout or _DEFAULT_LAYOUT
        arr = self._read_u32(self._projectile_static)
        length = self._read_u32(arr + 4)
        my_player = self._my_player_id()
        n = min(PROJECTILE_LIVE_SLOTS, max(0, length))
        blob = self._read_u32_table(arr + 8, n)
        out = []
        for slot in range(n):
            ptr = int.from_bytes(blob[slot * 4:slot * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END):
                continue
            try:
                active = self._read_u8(ptr + layout["active_off"])
                owner = self._read_u32(ptr + layout["owner_off"])
            except RuntimeError:
                continue
            if active == 0 or owner != my_player:
                continue
            if need_blob:
                try:
                    data = self._read_bytes(ptr, BOBBER_CALIB_SCAN_LEN)
                except RuntimeError:
                    continue
                out.append((slot, ptr, data))
            else:
                out.append((slot, ptr))
        return out

    def _raw_projectile_ptrs(self) -> set:
        """Valid pointers from Main.projectile regardless of any field layout."""
        return {ptr for _, ptr, _ in self._raw_projectile_blobs()}

    def _raw_projectile_blobs(self) -> list:
        """(slot, ptr, blob) for every valid pointer; no owner/active filter.

        Terraria reuses a pool of Projectile objects, so a bobber is almost
        never a brand-new pointer. Calibration therefore diffs blob content
        (a bobber type id appearing) rather than pointer identity.
        """
        arr = self._read_u32(self._projectile_static)
        n = min(PROJECTILE_LIVE_SLOTS, max(0, self._read_u32(arr + 4)))
        table = self._read_u32_table(arr + 8, n)
        out = []
        for slot in range(n):
            ptr = int.from_bytes(table[slot * 4:slot * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END):
                continue
            try:
                data = self._read_bytes(ptr, BOBBER_CALIB_SCAN_LEN)
            except RuntimeError:
                continue
            out.append((slot, ptr, data))
        return out

    def _load_cached_layout(self):
        """Cached bobber layout from disk, or None."""
        if not self._layout_cache_path:
            return None
        try:
            with open(self._layout_cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            layout = {
                "active_off": int(data["active_off"]),
                "owner_off": int(data["owner_off"]),
                "type_off": int(data["type_off"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
        raw_ai = data.get("ai_offs") or []
        ai_offs = []
        if isinstance(raw_ai, list):
            for item in raw_ai:
                try:
                    off = int(item)
                except (TypeError, ValueError):
                    continue
                if 0 <= off < BOBBER_CALIB_SCAN_LEN and off % 4 == 0:
                    ai_offs.append(off)
        layout["ai_offs"] = ai_offs
        for key in ("active_off", "owner_off", "type_off"):
            if not (0 <= layout[key] < BOBBER_CALIB_SCAN_LEN):
                return None
        return layout

    def _save_cached_layout(self, layout):
        if not self._layout_cache_path:
            return
        if layout is None:
            try:
                import os as _os

                _os.remove(self._layout_cache_path)
            except OSError:
                pass
            return
        try:
            with open(self._layout_cache_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "active_off": int(layout["active_off"]),
                        "owner_off": int(layout["owner_off"]),
                        "type_off": int(layout["type_off"]),
                        "ai_offs": [int(x) for x in (layout.get("ai_offs") or [])],
                    },
                    fh,
                )
        except OSError:
            pass

    def _sample_idle_projectiles(self, exclude_ptrs: set, limit: int = 12):
        """Blobs of other projectiles: mostly inactive, useful as zero-fill."""
        arr = self._read_u32(self._projectile_static)
        n = min(PROJECTILE_LIVE_SLOTS, max(0, self._read_u32(arr + 4)))
        table = self._read_u32_table(arr + 8, n)
        out = []
        for slot in range(n):
            ptr = int.from_bytes(table[slot * 4:slot * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END) or ptr in exclude_ptrs:
                continue
            try:
                data = self._read_bytes(ptr, BOBBER_CALIB_SCAN_LEN)
            except RuntimeError:
                continue
            out.append(data)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _blob_has_bobber_type(blob: bytes) -> bool:
        """True if a bobber item id sits at an aligned field, not whoAmI.

        Slots 360-366 store whoAmI==slot at +0x04, which is also a bobber
        type id and must not count as a bobber.
        """
        n = len(blob)
        for off in range(0, n - 3, 4):
            if off == HYP_WHOAMI_OFF:
                continue
            if int.from_bytes(blob[off:off + 4], "little") in BOBBER_TYPES:
                return True
        return False

    @staticmethod
    def _score_bobber_blob(blob: bytes, my_player: int) -> dict:
        """Offsets inside one bobber object consistent with a bobber."""
        n = len(blob)
        type_offs = [
            off
            for off in range(0, n - 3, 4)
            if off != HYP_WHOAMI_OFF
            and int.from_bytes(blob[off:off + 4], "little") in BOBBER_TYPES
        ]
        owner_offs = [
            off
            for off in range(0, n - 3, 4)
            if off != HYP_WHOAMI_OFF
            and int.from_bytes(blob[off:off + 4], "little") == my_player
        ]
        active_offs = [off for off in range(n) if blob[off] != 0]
        return {
            "type_off": type_offs,
            "owner_off": owner_offs,
            "active_off": active_offs,
        }

    def _consensus_layout(self, scored: list, idle_blobs: list):
        """Offsets shared by every bobber candidate; active must be 0 in idles.

        Ties break by closeness to the known layout: a game update shifts
        fields slightly, it does not reshuffle them randomly.
        """

        def common(key):
            sets = [set(s[key]) for s in scored]
            inter = set.intersection(*sets) if sets else set()
            return sorted(inter)

        def pick(cands, prior, aligned_only: bool = True):
            if aligned_only:
                cands = [off for off in cands if off % 4 == 0] or list(cands)
            return min(cands, key=lambda off: (abs(off - prior), off))

        type_cands = common("type_off")
        owner_cands = common("owner_off")
        active_cands = common("active_off")

        def idle_zero_rate(off: int) -> float:
            if not idle_blobs:
                return 1.0
            zeros = sum(1 for b in idle_blobs if off < len(b) and b[off] == 0)
            return zeros / len(idle_blobs)

        active_cands = [
            off for off in active_cands if idle_zero_rate(off) >= 0.8
        ]
        if not type_cands or not owner_cands or not active_cands:
            return None
        return {
            "type_off": pick(
                type_cands, _DEFAULT_LAYOUT["type_off"]
            ),
            "owner_off": pick(
                owner_cands, _DEFAULT_LAYOUT["owner_off"]
            ),
            "active_off": pick(
                # active is a bool byte: it is not 4-aligned in general, so
                # an aligned-only filter would drop the real offset in favour
                # of a far-away aligned decoy.
                active_cands, _DEFAULT_LAYOUT["active_off"], aligned_only=False
            ),
        }

    def _try_layout_from_live_blobs(self) -> bool:
        """Apply a layout from bobbers already in the water, if any."""
        try:
            entries = self._raw_projectile_blobs()
        except RuntimeError:
            return False
        my_player = self._my_player_id()
        scored = []
        live_ptrs = set()
        for slot, ptr, data in entries:
            if not self._blob_has_bobber_type(data):
                continue
            if len(data) > HYP_ACTIVE_OFF and data[HYP_ACTIVE_OFF] == 0:
                continue
            s = self._score_bobber_blob(data, my_player)
            if not s["type_off"]:
                continue
            scored.append(s)
            live_ptrs.add(ptr)
            self._phase(f"calib_live slot={slot}")
        if not scored:
            return False
        idle = self._sample_idle_projectiles(live_ptrs)
        layout = self._consensus_layout(scored, idle)
        if layout is None:
            return False
        self._apply_bobber_layout(layout)
        found = self._local_bobbers()
        if not found:
            self._apply_bobber_layout(None)
            return False
        self._phase("calib_ok live")
        self._save_cached_layout(layout)
        self._resolve_ai_offs_from_bobbers()
        self.on_status("calibrated")
        return True

    def _calibrate_bobber_layout(self) -> bool:
        """Find shifted Projectile field offsets from manual casts.

        Terraria updates move fields inside Projectile, so the built-in
        owner/active/type offsets cannot be trusted to select candidates.
        Projectile objects are a reused pool: the bobber is almost never a
        new pointer. The window snapshots which blobs already contain a
        bobber type id; a candidate is a blob that newly gains one and
        keeps it for BOBBER_CALIB_STABLE_FRAMES polls. Offsets then come
        from content: type in BOBBER_TYPES, owner == myPlayer, active is a
        byte set here but zero in idle projectiles.
        """
        cached = self._load_cached_layout() or _HOOK_CACHE.get("bobber_layout")
        if cached:
            self._phase(f"calib_cached {cached}")
            self._apply_bobber_layout(cached)
            found = self._local_bobbers()
            if found:
                self._phase("calib_cached_ok")
                self._resolve_ai_offs_from_bobbers(found)
                return True
            self._phase("calib_cached_stale")
            self._apply_bobber_layout(None)
            self._save_cached_layout(None)
        found = self._local_bobbers()
        if found:
            try:
                data = self._read_bytes(found[0][1], BOBBER_OBJ_PREFIX)
                proj_type = int.from_bytes(
                    data[OFF_PROJ_TYPE:OFF_PROJ_TYPE + 4], "little"
                )
            except RuntimeError:
                return False
            if proj_type in BOBBER_TYPES:
                self._phase("calib_skip default_layout_ok")
                self._resolve_ai_offs_from_bobbers(found)
                return True

        if self._try_layout_from_live_blobs():
            return True

        self.on_status("calibrating")
        self._phase("calib_start")
        try:
            baseline_entries = self._raw_projectile_blobs()
        except RuntimeError:
            baseline_entries = None
        if baseline_entries is None:
            self._phase("calib_timeout no_baseline")
            if not self.stop_event.is_set():
                self.on_status("calibrate_timeout")
            return False
        baseline_bobber_ptrs = {
            ptr
            for _, ptr, data in baseline_entries
            if self._blob_has_bobber_type(data)
        }
        # Calibration is intentionally passive.  The user supplies this one
        # cast after the prompt; clicking while the previous rod animation is
        # still active may be ignored and leaves no bobber to calibrate.
        deadline = time.monotonic() + BOBBER_CALIB_TIMEOUT_S
        scored: list = []
        scored_ptrs: set = set()
        stable_hits: dict = {}
        idle_blobs: list = []
        first_hit_at = None
        while (
            time.monotonic() < deadline
            and not self.stop_event.is_set()
            and len(scored) < BOBBER_CALIB_MAX_CANDIDATES
        ):
            try:
                entries = self._raw_projectile_blobs()
            except RuntimeError:
                time.sleep(CALIB_POLL_S)
                continue
            for slot, ptr, data in entries:
                if ptr in baseline_bobber_ptrs or ptr in scored_ptrs:
                    continue
                if not self._blob_has_bobber_type(data):
                    stable_hits[ptr] = 0
                    continue
                hits = stable_hits.get(ptr, 0) + 1
                stable_hits[ptr] = hits
                if hits != BOBBER_CALIB_STABLE_FRAMES:
                    continue
                if len(scored) >= BOBBER_CALIB_MAX_CANDIDATES:
                    break
                s = self._score_bobber_blob(data, self._my_player_id())
                if not s["type_off"]:
                    stable_hits[ptr] = 0
                    continue
                scored.append(s)
                scored_ptrs.add(ptr)
                self._phase(f"calib_candidate slot={slot}")
                if first_hit_at is None:
                    first_hit_at = time.monotonic()
            if not idle_blobs and scored:
                idle_blobs = self._sample_idle_projectiles(scored_ptrs)
            if (
                scored
                and first_hit_at is not None
                and time.monotonic() - first_hit_at > BOBBER_CALIB_EXTRA_S
            ):
                # One cast is enough: extra waiting risks reeling the line.
                break
            time.sleep(CALIB_POLL_S)

        if self.stop_event.is_set():
            self._phase("calib_aborted")
            return False
        if not scored:
            self._phase("calib_timeout no_candidates")
            self.on_status("calibrate_timeout")
            return False
        layout = self._consensus_layout(scored, idle_blobs)
        if layout is None:
            self._phase("calib_failed no_consensus")
            if not self.stop_event.is_set():
                self.on_status("calibrate_timeout")
            return False
        self._apply_bobber_layout(layout)
        found = self._local_bobbers()
        if not found:
            self._phase("calib_rejected no_bobber_after_apply")
            self._apply_bobber_layout(None)
            self._save_cached_layout(None)
            return False
        self._phase("calib_ok")
        self._save_cached_layout(layout)
        self._resolve_ai_offs_from_bobbers()
        self.on_status("calibrated")
        return True

    def _emit_hook_error(self, reason: str, candidates: list):
        if not self._probe_enabled or not self._probe_log_path:
            return
        from projectile_probe import append_probe_event

        last = candidates[-1] if candidates else {}
        append_probe_event(
            self._probe_log_path,
            "hook_error",
            pid=int(self._pid or 0),
            reason=reason,
            sig_source=last.get("source") or self._sig_source or "",
            sig_match=last.get("match"),
            player_static="0x0",
            myplayer_static="0x0",
            candidate_count=len(candidates),
            candidates=candidates,
            fields="hypothesized",
        )

    def _is_player_size(self, width: int, height: int) -> bool:
        return width == PLAYER_WIDTH and height in PLAYER_HEIGHTS

    def _player_array_score(self, arr: int) -> int:
        score = 0
        for i in range(256):
            try:
                p = self._read_u32(arr + 8 + i * 4)
            except RuntimeError:
                continue
            if not (0x10000 < p < USER_SPACE_END):
                continue
            try:
                width = self._read_u32(p + OFF_ENTITY_WIDTH)
                height = self._read_u32(p + OFF_ENTITY_HEIGHT)
            except RuntimeError:
                continue
            if self._is_player_size(width, height):
                score += 1
                if score >= 3:
                    return score
        return score

    def _find_player_array_static(self, around: int) -> int:
        """Find Main.player: CLR Player[256] within ±256 KiB of _context."""
        start = max(0x10000, around - STATIC_SCAN_RADIUS)
        end = min(USER_SPACE_END, around + STATIC_SCAN_RADIUS)
        span = (end - start) & ~3
        best_slot = 0
        best_key = None
        for off in range(0, span, 4):
            if self.stop_event.is_set():
                raise ScanAborted()
            slot = start + off
            try:
                arr = self._read_u32(slot)
            except RuntimeError:
                continue
            if not (0x10000 < arr < USER_SPACE_END):
                continue
            try:
                length = self._read_u32(arr + 4)
            except RuntimeError:
                continue
            if length != 256:
                continue
            score = self._player_array_score(arr)
            if score <= 0:
                continue
            key = (abs(slot - around), -score)
            if best_key is None or key < best_key:
                best_key = key
                best_slot = slot
        return best_slot

    def _projectile_occupancy(self, arr: int, length: int, slots=None):
        """Return (whoami_matches, non_null) for sampled slots or an explicit list."""
        if slots is None:
            n = min(PROJ_OCCUPANCY_SAMPLE, max(0, length))
            slots = list(range(n))
            last = length - 1
            if last >= n:
                slots.append(last)
        matches = 0
        non_null = 0
        for slot in slots:
            try:
                ptr = self._read_u32(arr + 8 + slot * 4)
            except RuntimeError:
                continue
            if not (0x10000 < ptr < USER_SPACE_END):
                continue
            non_null += 1
            try:
                who = self._read_u32(ptr + HYP_WHOAMI_OFF)
            except RuntimeError:
                continue
            if who == slot:
                matches += 1
        return matches, non_null

    def _projectile_array_richness(self, arr: int) -> Optional[tuple]:
        """(non_null, whoami_matches) over the first PROJECTILE_VALIDATE_SLOTS
        slots, or None if `arr` isn't even a plausible array header.

        NOTE: dummy whoAmI on slot 1000 is not a signature. The live
        Main.projectile array is dense with many whoAmI==index matches in
        0..999. Reject dummy-only 1001 tables and length-1000 neighbors
        with non-null slots but whoami_matches=0 (not Main.projectile).
        """
        if not (0x10000 < arr < USER_SPACE_END):
            return None
        try:
            length = self._read_u32(arr + 4)
        except RuntimeError:
            return None
        if length not in PROJECTILE_ARRAY_LENS:
            return None
        n = min(length, PROJECTILE_VALIDATE_SLOTS)
        try:
            blob = self._read_u32_table(arr + 8, n)
        except RuntimeError:
            return None
        non_null = 0
        who_matches = 0
        for i in range(n):
            ptr = int.from_bytes(blob[i * 4:i * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END):
                continue
            non_null += 1
            try:
                who = self._read_u32(ptr + HYP_WHOAMI_OFF)
            except RuntimeError:
                continue
            if who == i:
                who_matches += 1
        return (non_null, who_matches)

    def _is_projectile_array(self, arr: int) -> bool:
        """length 1000/1001 with a real, populated live-object range.

        Length-1000 tables with many pointers but no whoAmI matches are
        decoys (live dump: 379 ptrs, 0 bobbers). Length-1001 with whoAmI
        matches is Main.projectile even if currently empty of live objects.
        """
        richness = self._projectile_array_richness(arr)
        if richness is None:
            return False
        non_null, who_matches = richness
        try:
            length = self._read_u32(arr + 4)
        except RuntimeError:
            return False
        if who_matches >= PROJECTILE_MIN_WHOAMI_MATCH:
            return True
        if length == PROJECTILE_ARRAY_LEN and non_null >= PROJECTILE_MIN_NON_NULL:
            return True
        return False

    def _projectile_header_ok(self, slot: int) -> bool:
        """True if the static points at a length-1000/1001 CLR array header."""
        if not slot:
            return False
        try:
            arr = self._read_u32(slot)
            if not (0x10000 < arr < USER_SPACE_END):
                return False
            length = self._read_u32(arr + 4)
        except RuntimeError:
            return False
        return length in PROJECTILE_ARRAY_LENS

    def _projectile_static_ok(self, slot: int) -> bool:
        if not slot:
            return False
        try:
            arr = self._read_u32(slot)
        except RuntimeError:
            return False
        if self._is_projectile_array(arr):
            return True
        try:
            return self._read_u32(arr + 4) == PROJECTILE_ARRAY_LEN
        except RuntimeError:
            return False

    def _projectile_static_from_player(self, player_static: int) -> int:
        if not player_static:
            return 0
        for off in PROJ_STATIC_OFFS_FROM_PLAYER:
            slot = player_static - off
            if slot < 0x10000:
                continue
            if self._projectile_header_ok(slot):
                return slot
        return 0

    def _projectile_array_score(self, arr: int) -> int:
        """1 if dummy/length look like Main.projectile, else 0."""
        return 1 if self._is_projectile_array(arr) else 0

    def _resolve_projectile_static(self, around: int, scan: bool = False) -> int:
        slot = self._projectile_static_from_player(around)
        if slot:
            return slot
        if not scan:
            return 0
        return self._find_projectile_array_static(around)

    def _find_projectile_array_static(self, around: int) -> int:
        """Fallback: static slot pointing at the richest length-1000/1001
        array nearby. Rank whoAmI==index matches first, then non_null.
        Length-1000 tables with many pointers but whoami_matches=0 are not
        Main.projectile. Runs once per hook session (cached); recast must
        not call this.
        """
        if not around:
            return 0
        start = max(0x10000, around - STATIC_SCAN_RADIUS)
        end = min(USER_SPACE_END, around + STATIC_SCAN_RADIUS)
        span = (end - start) & ~3
        cands = []
        for off in range(0, span, 4):
            if self.stop_event.is_set():
                raise ScanAborted()
            slot = start + off
            try:
                arr = self._read_u32(slot)
            except RuntimeError:
                continue
            richness = self._projectile_array_richness(arr)
            if richness is None:
                continue
            non_null, who_matches = richness
            try:
                length = self._read_u32(arr + 4)
            except RuntimeError:
                continue
            if who_matches < PROJECTILE_MIN_WHOAMI_MATCH and length != PROJECTILE_ARRAY_LEN:
                continue
            if non_null < 1 and who_matches < 1:
                continue
            cands.append((slot, non_null, who_matches, length))
        if not cands:
            return 0
        cands.sort(
            key=lambda c: (
                0 if c[3] == PROJECTILE_ARRAY_LEN else 1,
                -c[2],
                -c[1],
                abs(c[0] - around),
                c[0],
            )
        )
        return cands[0][0]

    def _guess_myplayer_index(self) -> Optional[int]:
        """Last-resort local index: whoAmI == slot and player-sized hitbox."""
        try:
            arr = self._read_u32(self._player_static)
        except RuntimeError:
            return None
        found = None
        for i in range(256):
            try:
                p = self._read_u32(arr + 8 + i * 4)
            except RuntimeError:
                continue
            if not (0x10000 < p < USER_SPACE_END):
                continue
            try:
                width = self._read_u32(p + OFF_ENTITY_WIDTH)
                height = self._read_u32(p + OFF_ENTITY_HEIGHT)
                who = self._read_u32(p + HYP_WHOAMI_OFF)
            except RuntimeError:
                continue
            if self._is_player_size(width, height) and who == i:
                found = i
                break
        return found

    def _find_myplayer_static(self, player_static: int) -> int:
        """Find Main.myPlayer by JIT refs sitting next to the player-array immediate."""
        needle = player_static.to_bytes(4, "little")
        votes: Counter = Counter()
        for base, size in _iter_executable_regions(self.process_handle):
            if self.stop_event.is_set():
                raise ScanAborted()
            data = self._read_bytes(base, size)
            if not data:
                continue
            start = 0
            while True:
                j = data.find(needle, start)
                if j < 0:
                    break
                window = data[max(0, j - 24): j + 24]
                k = 0
                while k < len(window) - 5:
                    op = window[k:k + 2]
                    imm = None
                    if op in (
                        b"\x8b\x05", b"\x8b\x0d", b"\x8b\x15",
                        b"\x8b\x1d", b"\x8b\x35", b"\x8b\x3d",
                    ):
                        imm = int.from_bytes(window[k + 2:k + 6], "little")
                        k += 6
                    elif window[k] == 0xA1:
                        imm = int.from_bytes(window[k + 1:k + 5], "little")
                        k += 5
                    else:
                        k += 1
                        continue
                    if imm == player_static:
                        continue
                    try:
                        val = self._read_u32(imm)
                    except RuntimeError:
                        continue
                    if 0 <= val < 256:
                        votes[imm] += 1
                start = j + 1
        if not votes:
            return 0
        return votes.most_common(1)[0][0]

    def _read_bytes(self, addr: int, size: int) -> bytes:
        """Read `size` bytes. One RPM first; page-split only the unread tail."""
        if size <= 0 or not self.process_handle:
            return b""
        buf = (ctypes.c_ubyte * size)()
        n = ctypes.c_size_t(0)
        if ReadProcessMemory(
            self.process_handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)
        ) and n.value:
            if n.value >= size:
                return bytes(buf[:size])
            out = bytearray(buf[:n.value])
        else:
            out = bytearray()
        cur = addr + len(out)
        remaining = size - len(out)
        while remaining > 0:
            page_left = 0x1000 - (cur & 0xFFF)
            chunk = min(remaining, page_left)
            piece = (ctypes.c_ubyte * chunk)()
            got = ctypes.c_size_t(0)
            if not ReadProcessMemory(
                self.process_handle, ctypes.c_void_p(cur), piece, chunk,
                ctypes.byref(got),
            ) or got.value == 0:
                break
            out.extend(bytes(piece[:got.value]))
            if got.value < chunk:
                break
            cur += got.value
            remaining -= got.value
        return bytes(out)

    def _read_u32_table(self, addr: int, count: int) -> bytes:
        """`count` little-endian u32s. Recover zeros if a bulk read dropped live ptrs."""
        if count <= 0:
            return b""
        want = count * 4
        blob = bytearray(self._read_bytes(addr, want))
        got = len(blob)
        if got < want:
            blob.extend(b"\x00" * (want - got))
            for i in range(got // 4, count):
                try:
                    blob[i * 4:i * 4 + 4] = self._read_u32(
                        addr + i * 4
                    ).to_bytes(4, "little")
                except RuntimeError:
                    pass
        live = False
        for i in range(min(count, PROJ_OCCUPANCY_SAMPLE)):
            if int.from_bytes(blob[i * 4:i * 4 + 4], "little") != 0:
                live = True
                break
        if not live and count > 0:
            lying = False
            for i in (0, min(19, count - 1)):
                try:
                    val = self._read_u32(addr + i * 4)
                except RuntimeError:
                    continue
                if val:
                    blob[i * 4:i * 4 + 4] = val.to_bytes(4, "little")
                    lying = True
            if lying:
                for i in range(count):
                    off = i * 4
                    if int.from_bytes(blob[off:off + 4], "little") != 0:
                        continue
                    try:
                        val = self._read_u32(addr + off)
                    except RuntimeError:
                        continue
                    if val:
                        blob[off:off + 4] = val.to_bytes(4, "little")
        return bytes(blob)

    def _read_u32(self, addr: int) -> int:
        if not self.process_handle:
            raise RuntimeError("closed")
        buf = (ctypes.c_ubyte * 4)()
        n = ctypes.c_size_t(0)
        if not ReadProcessMemory(
            self.process_handle, ctypes.c_void_p(addr), buf, 4, ctypes.byref(n)
        ) or n.value != 4:
            raise RuntimeError(
                f"ReadProcessMemory@{addr:#x} failed (err={ctypes.get_last_error()})"
            )
        return int.from_bytes(buf, "little")

    def _read_u8(self, addr: int) -> int:
        buf = (ctypes.c_ubyte * 1)()
        n = ctypes.c_size_t(0)
        if not ReadProcessMemory(
            self.process_handle, ctypes.c_void_p(addr), buf, 1, ctypes.byref(n)
        ) or n.value != 1:
            raise RuntimeError("read_u8 failed")
        return buf[0]

    def _write_u8(self, addr: int, value: int) -> bool:
        buf = (ctypes.c_ubyte * 1)(value & 0xFF)
        n = ctypes.c_size_t(0)
        ok = WriteProcessMemory(
            self.process_handle, ctypes.c_void_p(addr), buf, 1, ctypes.byref(n),
        )
        return bool(ok) and n.value == 1

    def _read_pointer(self, addr: int) -> int:
        return self._read_u32(addr)

    def _local_player(self) -> int:
        arr = self._read_u32(self._player_static)
        idx = 0
        if self._myplayer_static:
            try:
                idx = self._read_u32(self._myplayer_static)
            except RuntimeError:
                idx = 0
        elif self._myplayer_fallback_idx is not None:
            idx = int(self._myplayer_fallback_idx)
        if not (0 <= idx < 256):
            idx = 0
        return self._read_u32(arr + 8 + idx * 4)

    def local_player_ptr(self) -> int:
        """Read-only pointer to the local Player. 0 if the hook is not ready."""
        if not self.process_handle or not self._player_static:
            return 0
        try:
            ptr = self._local_player()
        except RuntimeError:
            return 0
        return int(ptr) if ptr else 0

    def _my_player_id(self) -> int:
        idx = 0
        if self._myplayer_static:
            try:
                idx = self._read_u32(self._myplayer_static)
            except RuntimeError:
                idx = 0
        elif self._myplayer_fallback_idx is not None:
            idx = int(self._myplayer_fallback_idx)
        if not (0 <= idx < 256):
            idx = 0
        return idx

    def _ensure_projectile_array(self) -> bool:
        if self._projectile_static_ok(self._projectile_static):
            return True
        self._projectile_static = 0
        if not self._player_static:
            return False
        # Hook-setup already ran scan=True and cached the slot. Recast must
        # not pay for ±STATIC_SCAN_RADIUS; reuse the slot if still valid.
        cached = _HOOK_CACHE.get("projectile_static") or 0
        if cached and self._projectile_static_ok(cached):
            self._projectile_static = cached
            return True
        self._projectile_static = self._resolve_projectile_static(
            self._player_static, scan=False
        )
        if self._projectile_static:
            _HOOK_CACHE["projectile_static"] = self._projectile_static
        return bool(self._projectile_static)

    def _parse_bobber_prefix(self, data: bytes, slot: int, my_player: int) -> bool:
        """Local fishing bobber: active!=0, type at layout type_off, owner at owner_off."""
        if len(data) < OFF_PROJ_TYPE + 4:
            return False
        who = int.from_bytes(data[HYP_WHOAMI_OFF:HYP_WHOAMI_OFF + 4], "little")
        if who != slot:
            return False
        if data[HYP_ACTIVE_OFF] == 0:
            return False
        proj_type = int.from_bytes(
            data[OFF_PROJ_TYPE:OFF_PROJ_TYPE + 4], "little"
        )
        if proj_type not in BOBBER_TYPES:
            return False
        owner = int.from_bytes(data[OFF_PROJ_OWNER:OFF_PROJ_OWNER + 4], "little")
        return owner == my_player

    def _list_bobbers(self) -> list:
        """Fishing bobbers in slots 0..999. Dummy slot 1000 is ignored."""
        arr = self._read_u32(self._projectile_static)
        length = self._read_u32(arr + 4)
        my_player = self._my_player_id()
        n = min(PROJECTILE_LIVE_SLOTS, max(0, length))
        blob = self._read_u32_table(arr + 8, n)
        found = []
        for slot in range(n):
            ptr = int.from_bytes(blob[slot * 4:slot * 4 + 4], "little")
            if not (0x10000 < ptr < USER_SPACE_END):
                continue
            head = self._read_bytes(ptr, HYP_ACTIVE_OFF + 1)
            if len(head) <= HYP_ACTIVE_OFF or head[HYP_ACTIVE_OFF] == 0:
                continue
            data = self._read_bytes(ptr, BOBBER_OBJ_PREFIX)
            if self._parse_bobber_prefix(data, slot, my_player):
                found.append((slot, ptr))
        return found

    def _lmb_release(self):
        try:
            self._send_mouse(win32con.MOUSEEVENTF_LEFTUP)
        except Exception:
            pass
        try:
            self._write_control_use_item(0)
        except Exception:
            pass

    def _safe_stop(self, reason: str):
        self._lmb_release()
        self._phase(f"safe_stop {reason}")
        self.on_status(f"safe_stop:{reason}")
        self.stop_event.set()

    def _debug_log(self, msg: str):
        if not self._debug_path:
            return
        line = f"{time.monotonic() - self._t0:+.3f} {msg}\n"
        try:
            with self._debug_lock:
                with open(self._debug_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError:
            pass

    def _sanitize_start(self):
        """Calibrate bobber offsets, verify anim if the water is empty, cast.

        A live bobber skips the first cast. rolledItemDrop is not zeroed:
        the game restores that field, and dunk detection does not need it.
        """
        try:
            bobbers = self._local_bobbers()
        except RuntimeError:
            return
        if bobbers:
            self._bobber_verified_layout = True
            self._resolve_ai_offs_from_bobbers(bobbers)
        elif bobbers is not None:
            # Some multiplayer clients expose only an empty placeholder at
            # the historical Main.projectile static. It cannot calibrate a
            # bobber layout; do not click into a line we cannot see.
            if self._projectile_has_live_entries() is False:
                self._enable_rolled_fallback("empty_projectile_table")
                return
            self._bobber_verified_layout = self._calibrate_bobber_layout()
            if self._bobber_verified_layout:
                bobbers = self._local_bobbers()
                if bobbers:
                    self._resolve_ai_offs_from_bobbers(bobbers)
        if self._bobber_verified_layout:
            self._check_anim_detector()
        if self.stop_event.is_set():
            return
        try:
            bobbers = self._local_bobbers()
        except RuntimeError:
            bobbers = None
        if bobbers:
            self._phase(f"sanitize_bobber_present n={len(bobbers)}")
            return
        if not self.aim_client or self.stop_event.is_set():
            if bobbers is None:
                self._phase("sanitize_no_verify")
            return
        self._phase("initial_cast")
        if not self._recast_until_bobber():
            self._phase("initial_cast_failed")
            self.on_status("recast_failed")

    def _pause_then_recast(self) -> bool:
        """Catch/skip delay then a guarded recast. False if stopped."""
        self._phase(
            f"pause_before_recast "
            f"catch={CATCH_DELAY_S}s interval={RECAST_INTERVAL_S}s"
        )
        self._sleep_interruptible(CATCH_DELAY_S)
        self._sleep_interruptible(RECAST_INTERVAL_S)
        if self.stop_event.is_set():
            return False
        self._wait_animation_clear(RECAST_ANIM_TIMEOUT_S)
        if self.stop_event.is_set():
            return False
        self._phase("recast_click")
        return self._recast_until_bobber()

    def _clr_float_array_len(self, ptr: int) -> int:
        """Element count if ptr looks like a small CLR SzArray<float>, else 0.

        ai/localAI are float[3] on 1.4.5.8; an exact `== 2` test matched
        nothing and left bite detection permanently dead.
        """
        if not (0x10000 < ptr < USER_SPACE_END):
            return 0
        try:
            length = self._read_u32(ptr + CLR_SZARRAY_LEN_OFF)
        except RuntimeError:
            return 0
        return int(length) if length in AI_ARRAY_LENS else 0

    def _is_clr_float_array(self, ptr: int) -> bool:
        return self._clr_float_array_len(ptr) > 0

    def _read_f32(self, addr: int) -> float:
        raw = self._read_bytes(addr, 4)
        if len(raw) < 4:
            raise RuntimeError("short_f32")
        return struct.unpack("<f", raw)[0]

    def _ai_pair_at(self, array_ptr: int):
        """(ai[0], ai[AI_DUNK_INDEX]) from a CLR float[] , or None."""
        length = self._clr_float_array_len(array_ptr)
        if length <= AI_DUNK_INDEX:
            return None
        try:
            a0 = self._read_f32(array_ptr + CLR_SZARRAY_F0_OFF)
            a1 = self._read_f32(
                array_ptr + CLR_SZARRAY_F0_OFF + AI_DUNK_INDEX * 4
            )
        except RuntimeError:
            return None
        return (a0, a1)

    def _scan_ai_array_offs(self, blob: bytes) -> list:
        """Field offsets on a projectile blob that point at ai-sized float[]."""
        offs = []
        n = len(blob)
        for off in range(0, n - 3, 4):
            ptr = int.from_bytes(blob[off:off + 4], "little")
            if self._is_clr_float_array(ptr):
                offs.append(off)
        return offs

    def _resolve_ai_offs_from_bobbers(self, bobbers=None) -> bool:
        """Cache projectile field offsets that point at ai/localAI float[].

        Retries are rate-limited: this runs from the poll loop, and an
        unthrottled failure path re-read the object plus up to 64 pointers at
        40 Hz while writing a log line every frame.
        """
        if self._ai_offs:
            return True
        now = time.monotonic()
        if now < self._ai_offs_retry_at:
            return False
        self._ai_offs_retry_at = now + AI_OFFS_RETRY_S
        if bobbers is None:
            try:
                bobbers = self._local_bobbers()
            except RuntimeError:
                bobbers = None
        if not bobbers:
            return False
        _, ptr = bobbers[0]
        try:
            data = self._read_bytes(ptr, BOBBER_CALIB_SCAN_LEN)
        except RuntimeError:
            return False
        if len(data) < 16:
            return False
        offs = self._scan_ai_array_offs(data)
        if not offs:
            self._ai_offs_fail_count += 1
            if self._ai_offs_fail_count == 1:
                self._phase("ai_offs none")
            return False
        self._ai_offs = offs
        self._ai_offs_fail_count = 0
        stored = dict(self.bobber_layout or _DEFAULT_LAYOUT)
        stored["ai_offs"] = list(offs)
        self.bobber_layout = stored
        _HOOK_CACHE["bobber_layout"] = dict(stored)
        self._save_cached_layout(stored)
        self._phase("ai_offs " + ",".join(f"{o:#x}" for o in offs))
        return True

    def _bobber_ai1(self) -> Optional[float]:
        """Lowest ai[AI_DUNK_INDEX] across the bobber's ai-sized float arrays."""
        pair = self._bobber_ai_pair()
        if pair is None:
            return None
        return pair[1]

    def _bobber_ai_pair(self):
        """(ai0, ai1, field_off) for the array with the most-negative ai1.

        Both ai (+0x40) and localAI (+0x44) are float[3] and indistinguishable
        by shape, so every candidate is read and the most negative wins: only
        ai[1] goes below zero, and it does so exactly while the bobber is
        pulled under.
        """
        try:
            bobbers = self._local_bobbers()
        except RuntimeError:
            return None
        if not bobbers:
            return None
        _, ptr = bobbers[0]
        offs = self._ai_offs
        if not offs:
            self._resolve_ai_offs_from_bobbers(bobbers)
            offs = self._ai_offs
        best = None
        for off in offs or ():
            try:
                array_ptr = self._read_u32(ptr + off)
            except RuntimeError:
                continue
            pair = self._ai_pair_at(array_ptr)
            if pair is None:
                continue
            if best is None or pair[1] < best[1]:
                best = (pair[0], pair[1], off)
        return best

    def _bobber_dunking(self) -> bool:
        """True while the local bobber's ai[1] is negative (a fish is pulling)."""
        v = self._bobber_ai1()
        return v is not None and v < 0.0

    def _dunk_detect_ready(self) -> bool:
        """True if the dunk signal has ever been readable."""
        return bool(self._ai_offs) or self._ai_read_ok

    def _next_bite(self) -> Optional[int]:
        """rolledItemDrop of a fresh bite, or None.

        Primary path is the bobber dunk (ai[1] < 0), latched so one dunk yields
        one reel: a dunk lasts ~2s and the loop polls at 40 Hz.

        If ai[1] cannot be read at all (a future field shift), the loop would
        sit idle forever, so after DUNK_FALLBACK_AFTER_S it degrades to the
        older rule: a change of rolledItemDrop is a bite. That field is sticky,
        so the fallback re-arms only when it reads back as zero.
        """
        ai1 = self._bobber_ai1()
        if ai1 is not None:
            self._ai_read_ok = True
            if self._dunk_fallback:
                self._dunk_fallback = False
                self._fallback_last_id = 0
                self._phase("dunk_detect_recovered")
            if ai1 >= 0.0:
                self._dunk_latched = False
                return None
            if self._dunk_latched:
                return None
            self._dunk_latched = True
            return self._read_rolled()

        # ai[1] unreadable. An empty offset list is the real symptom; a missing
        # bobber is not, so a dry spell must not trip the fallback.
        if (
            not self._dunk_fallback
            and not self._ai_offs
            and not self._ai_read_ok
            and self._t0
            and time.monotonic() - self._t0 >= DUNK_FALLBACK_AFTER_S
        ):
            self._enable_rolled_fallback("after_timeout")
        if not self._dunk_fallback:
            return None
        item_id = self._read_rolled()
        if not item_id:
            self._fallback_last_id = 0
            return None
        if item_id == self._fallback_last_id:
            return None
        self._fallback_last_id = item_id
        return item_id

    def _enable_rolled_fallback(self, reason: str):
        """Use rolledItemDrop transitions when dunk state is unavailable."""
        if self._dunk_fallback:
            return
        self._dunk_fallback = True
        # rolledItemDrop is sticky: the value present at startup is old state.
        self._fallback_last_id = self._read_rolled()
        self._phase(f"dunk_fallback_on {reason} rolled_id_mode")
        self.on_status("dunk_unavailable")

    def _clear_bite_latch(self):
        """Allow the next dunk to register."""
        self._dunk_latched = False

    def _check_anim_detector(self):
        """Warn once if itemAnimation never rises after a real click (offset drift).

        On 1.4.5.8 the Player offsets may have moved; a dead detector makes
        every reel retry click actually yank the rod instead of reeling.
        Must run before any bobber is in the water: the probe click is a real
        use and would reel an existing line.
        """
        if not self.aim_client or self.stop_event.is_set():
            return
        bobbers = self._local_bobbers()
        if bobbers:
            return
        hwnd = self._find_terraria_hwnd()
        if not hwnd:
            return
        self._aim_cursor(hwnd)
        self._sleep_interruptible(AIM_SETTLE_S)
        self._anim_ever_rose = False
        before = self._item_animation()
        self._send_mouse(win32con.MOUSEEVENTF_LEFTDOWN)
        try:
            deadline = time.monotonic() + CLICK_HOLD_S
            first = True
            while time.monotonic() < deadline and not self.stop_event.is_set():
                self._write_use_item(1, edge=first)
                first = False
                if self._item_animation() != before:
                    return
                time.sleep(USE_ITEM_PULSE_S)
        finally:
            self._send_mouse(win32con.MOUSEEVENTF_LEFTUP)
            self._write_use_item(0)
        time.sleep(0.1)
        if self._bobber_verified_reel:
            # A bobber-confirmed reel already proved clicks work; a stale
            # itemAnimation offset must not raise a scary false alarm.
            return
        if not self.stop_event.is_set() and not self._anim_ever_rose:
            self._phase("anim_dead")
            self.on_status("anim_dead")

    def _log_rolled(self, value: int):
        if value == self._last_logged_rolled:
            return
        prev = self._last_logged_rolled
        self._last_logged_rolled = value
        self._phase(f"rolled {prev}->{value}")

    def _probe_failed_fishing_flag(self):
        """One-shot: dump bytes around rolledItemDrop on the first bite.

        Terraria's FishingAttempt keeps failedFishing next to rolledItemDrop;
        if a stable bool shows up in this window it can later distinguish a
        snapped line from a landed catch. Diagnostic only, logged to phases.
        """
        if self._failed_probe_done or not self._rolled_ptr:
            return
        self._failed_probe_done = True
        try:
            blob = self._read_bytes(self._rolled_ptr, 16)
        except RuntimeError:
            return
        nonzero = [
            f"+{off:#x}={blob[off]}"
            for off in range(4, 16)
            if blob[off] not in (0,)
        ]
        self._phase(f"failed_flag_probe rolled+16B nonzero={nonzero}")

    def _log_hook_addrs(self):
        layout = self.bobber_layout or _DEFAULT_LAYOUT
        self._phase(
            f"hook_addrs player={self._player_static:#x} "
            f"proj={self._projectile_static:#x} "
            f"ctx={self._context_static_addr:#x} "
            f"rolled={self._rolled_ptr:#x} "
            f"myplayer={self._my_player_id()} "
            f"type@{int(layout['type_off']):#x} "
            f"owner@{int(layout['owner_off']):#x} "
            f"active@{int(layout['active_off']):#x} "
            f"ai_offs={','.join(f'{o:#x}' for o in self._ai_offs) or '-'}"
        )

    def _refresh_rolled_ptr(self) -> bool:
        """Re-resolve rolledItemDrop from Projectile._context each poll."""
        if not self._context_static_addr:
            return bool(self._rolled_ptr)
        ctx = self._read_u32(self._context_static_addr)
        if not ctx:
            return False
        self._rolled_ptr = ctx + ROLLED_ITEM_DROP_OFF
        return True

    def _read_rolled(self) -> int:
        try:
            self._refresh_rolled_ptr()
            if not self._rolled_ptr:
                self._log_rolled(0)
                return 0
            value = self._read_u32(self._rolled_ptr)
        except RuntimeError:
            if self.stop_event.is_set():
                self._log_rolled(0)
                return 0
            try:
                _HOOK_CACHE["pid"] = None
                self._hook()
            except Exception:
                self._log_rolled(0)
                return 0
            try:
                self._refresh_rolled_ptr()
                value = self._read_u32(self._rolled_ptr)
            except RuntimeError:
                self._log_rolled(0)
                return 0
        self._log_rolled(value)
        return value

    def _zero_rolled(self):
        prev = int(self._last_logged_rolled or 0)
        if self.process_handle:
            try:
                self._refresh_rolled_ptr()
            except RuntimeError:
                pass
        if self.process_handle and self._rolled_ptr:
            zero = (ctypes.c_ubyte * 4)(0, 0, 0, 0)
            n = ctypes.c_size_t(0)
            WriteProcessMemory(
                self.process_handle, ctypes.c_void_p(self._rolled_ptr), zero, 4,
                ctypes.byref(n),
            )
        self._log_rolled(0)

    def _find_terraria_hwnd(self) -> int:
        if not self._pid:
            return 0
        hwnd = win32gui.FindWindow(None, "Terraria")
        if hwnd:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == self._pid:
                    return hwnd
            except Exception:
                pass
        best = 0
        best_area = -1

        def callback(h, _):
            nonlocal best, best_area
            if not win32gui.IsWindowVisible(h):
                return True
            try:
                if win32gui.GetWindow(h, win32con.GW_OWNER):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(h)
            except Exception:
                return True
            if pid != self._pid:
                return True
            try:
                left, top, right, bottom = win32gui.GetWindowRect(h)
                area = max(0, right - left) * max(0, bottom - top)
            except Exception:
                area = 0
            if area > best_area:
                best_area = area
                best = h
            return True

        try:
            win32gui.EnumWindows(callback, None)
        except Exception:
            pass
        return best

    def _focus_terraria(self) -> bool:
        hwnd = self._find_terraria_hwnd()
        if not hwnd:
            return False
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.05)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            fg = win32gui.GetForegroundWindow()
            fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            cur_tid = win32api.GetCurrentThreadId()
            attached_fg = False
            attached_target = False
            if fg_tid and fg_tid != cur_tid:
                attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
            if target_tid and target_tid != cur_tid:
                attached_target = bool(
                    user32.AttachThreadInput(cur_tid, target_tid, True)
                )
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            finally:
                if attached_target:
                    user32.AttachThreadInput(cur_tid, target_tid, False)
                if attached_fg:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def _emit_aim(self):
        if not self.aim_client:
            return
        x, y = self.aim_client
        self.on_status(f"aim_set:{x}:{y}")
        if self.on_aim:
            try:
                self.on_aim((x, y))
            except Exception:
                pass

    def _lmb_down(self) -> bool:
        try:
            return bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
        except Exception:
            return False

    def _hwnd_is_terraria(self, hwnd) -> bool:
        while hwnd:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return False
            if pid == self._pid:
                return True
            try:
                hwnd = win32gui.GetParent(hwnd)
            except Exception:
                return False
        return False

    def _cursor_client_in_terraria(self):
        hwnd = self._find_terraria_hwnd()
        if not hwnd:
            return None
        try:
            x, y = win32gui.GetCursorPos()
            hit = win32gui.WindowFromPoint((x, y))
            if not self._hwnd_is_terraria(hit):
                return None
            return win32gui.ScreenToClient(hwnd, (x, y))
        except Exception:
            return None

    def _capture_aim(self) -> bool:
        self.on_status("aim_prompt")
        while not self.stop_event.is_set() and self._lmb_down():
            time.sleep(0.02)
        while not self.stop_event.is_set():
            if self._need_aim.is_set() and self.aim_client is None:
                self._need_aim.clear()
            if self._lmb_down():
                pt = self._cursor_client_in_terraria()
                if pt is not None:
                    while not self.stop_event.is_set() and self._lmb_down():
                        time.sleep(0.02)
                    self.aim_client = (int(pt[0]), int(pt[1]))
                    return True
            time.sleep(0.02)
        return False

    def _send_mouse(self, flags: int) -> bool:
        down = bool(flags & win32con.MOUSEEVENTF_LEFTDOWN)
        up = bool(flags & win32con.MOUSEEVENTF_LEFTUP)
        if down:
            self._bot_lmb_depth += 1
        extra = ULONG_PTR(0)
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.mi.dx = 0
        inp.mi.dy = 0
        inp.mi.mouseData = 0
        inp.mi.dwFlags = flags
        inp.mi.time = 0
        inp.mi.dwExtraInfo = extra.value
        try:
            sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            return sent == 1
        finally:
            if up:
                self._bot_lmb_depth = max(0, self._bot_lmb_depth - 1)

    def _send_mouse_click(self) -> bool:
        """LEFTDOWN+LEFTUP in one SendInput so LMB is not held across a frame."""
        self._bot_lmb_depth += 1
        extra = ULONG_PTR(0)
        arr = (INPUT * 2)()
        for i, flags in enumerate(
            (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP)
        ):
            arr[i].type = INPUT_MOUSE
            arr[i].mi.dx = 0
            arr[i].mi.dy = 0
            arr[i].mi.mouseData = 0
            arr[i].mi.dwFlags = flags
            arr[i].mi.time = 0
            arr[i].mi.dwExtraInfo = extra.value
        try:
            sent = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
            return sent == 2
        finally:
            self._bot_lmb_depth = max(0, self._bot_lmb_depth - 1)

    def _aim_cursor(self, hwnd: int) -> bool:
        if not self.aim_client:
            return False
        try:
            x, y = self.aim_client
            sx, sy = win32gui.ClientToScreen(hwnd, (int(x), int(y)))
            win32api.SetCursorPos((int(sx), int(sy)))
            return True
        except Exception:
            return False

    def _ensure_terraria_focused(self) -> bool:
        if self._focus_terraria():
            self._focus_fail_notified = False
            return True
        time.sleep(0.05)
        if self._focus_terraria():
            self._focus_fail_notified = False
            return True
        if not self._focus_fail_notified:
            self.on_status("focus_failed")
            self._focus_fail_notified = True
        return False

    def _write_use_item(self, value: int, edge: bool = False):
        try:
            player = self._local_player()
        except RuntimeError:
            return
        if not player:
            return
        self._write_u8(player + OFF_CONTROL_USE_ITEM, value)
        # releaseUseItem must mirror controlUseItem: leaving it set after a
        # zeroed controlUseItem makes the next frame see a stale just-pressed
        # edge and start a second use (reel) instead of a cast.
        self._write_u8(player + OFF_RELEASE_USE_ITEM, 1 if (value and edge) else 0)

    def _write_control_use_item(self, value: int):
        """Set controlUseItem only. Recast must not touch releaseUseItem."""
        try:
            player = self._local_player()
        except RuntimeError:
            return
        if not player:
            return
        self._write_u8(player + OFF_CONTROL_USE_ITEM, value)

    def _click_left(self, kind: str) -> bool:
        """Short LMB tap at the saved aim. Returns True if itemAnimation rose."""
        if not self.aim_client:
            return False
        hwnd = self._find_terraria_hwnd()
        if hwnd:
            self._aim_cursor(hwnd)
            self._sleep_interruptible(AIM_SETTLE_S)
        before = self._item_animation()
        self._send_mouse(win32con.MOUSEEVENTF_LEFTDOWN)
        anim_hold = before
        first = True
        try:
            hold_deadline = time.monotonic() + CLICK_HOLD_S
            while time.monotonic() < hold_deadline and not self.stop_event.is_set():
                self._write_use_item(1, edge=first)
                first = False
                anim_hold = self._item_animation()
                if self._animation_rose(before, anim_hold):
                    break
                time.sleep(USE_ITEM_PULSE_S)
        finally:
            self._send_mouse(win32con.MOUSEEVENTF_LEFTUP)
            self._write_use_item(0)
        anim_end = self._item_animation()
        rose = (
            self._animation_rose(before, anim_hold)
            or self._animation_rose(before, anim_end)
        )
        return rose

    def _set_input_busy(self, busy: bool):
        cb = self.on_input_busy
        if cb is None:
            return
        try:
            cb(bool(busy))
        except Exception:
            pass

    def _local_bobbers(self):
        """Local bobbers, or None if Main.projectile cannot be read."""
        if not self._ensure_projectile_array():
            return None
        try:
            return self._list_bobbers()
        except RuntimeError:
            return None

    def _projectile_has_live_entries(self) -> Optional[bool]:
        """Whether the resolved Projectile array contains a real object."""
        if not self._ensure_projectile_array():
            return None
        try:
            arr = self._read_u32(self._projectile_static)
            length = self._read_u32(arr + 4)
            n = min(PROJECTILE_LIVE_SLOTS, max(0, length))
            table = self._read_u32_table(arr + 8, n)
        except RuntimeError:
            return None
        for slot in range(n):
            ptr = int.from_bytes(table[slot * 4:slot * 4 + 4], "little")
            if 0x10000 < ptr < USER_SPACE_END:
                return True
        return False

    def _wait_for_bobber(self, timeout_s: float) -> Optional[bool]:
        """True if a local bobber appears. False if none. None if unverifiable."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self.stop_event.is_set():
            found = self._local_bobbers()
            if found is None:
                return None
            if found:
                return True
            time.sleep(0.05)
        found = self._local_bobbers()
        if found is None:
            return None
        return bool(found)

    def _wait_for_stable_bobber(self, appear_s: float) -> Optional[bool]:
        """Wait for a bobber and confirm it survives a RECAST_STABLE_S window.

        False means no bobber appeared at all, so one retry is safe. None
        means verification failed *or* a bobber appeared and vanished; both
        cases must suppress a second click because it could reel a delayed
        fresh cast back in.
        """
        appeared = self._wait_for_bobber(appear_s)
        if appeared is not True:
            return appeared
        deadline = time.monotonic() + RECAST_STABLE_S
        while time.monotonic() < deadline and not self.stop_event.is_set():
            found = self._local_bobbers()
            if found is None:
                return None
            if not found:
                self._phase("bobber_vanished_after_cast")
                return None
            time.sleep(RECAST_POLL_S)
        return True

    def _recast_until_bobber(self) -> bool:
        """Send exactly one recast click; stale Projectile state is ignored."""
        if self.stop_event.is_set():
            return False
        return self._recast_click()

    def _player_frame_tick(self) -> Optional[int]:
        """Return the observed multiplayer Player update counter, if readable."""
        player = self.local_player_ptr()
        if not player:
            return None
        try:
            return self._read_u32(player + OFF_PLAYER_FRAME_TICK)
        except RuntimeError:
            return None

    def _wait_for_player_frame_tick(
        self, previous: int, timeout_s: float, pulse=None,
    ) -> tuple[Optional[int], int]:
        """Wait for one Player update, optionally keeping controlUseItem high."""
        deadline = time.monotonic() + timeout_s
        reads = 0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if pulse is not None:
                pulse()
            tick = self._player_frame_tick()
            reads += 1
            if tick is None:
                return None, reads
            if tick != previous:
                return tick, reads
        return None, reads

    def _recast_click(self) -> bool:
        """One-shot cast: LEFTDOWN for exactly one observed Player update.

        An atomic DOWN+UP in one SendInput is 0 ms and Terraria often
        ignores it. A Windows-clock sleep can span two game frames and make
        the second use reel a fresh bobber.  Synchronize the hold to the
        multiplayer Player update counter instead.
        Recast must not pulse releaseUseItem: that edge starts a second
        use and reels the fresh bobber.
        """
        if not self.aim_client:
            if not self.stop_event.is_set():
                self._safe_stop("no_aim")
            return False
        hwnd = self._find_terraria_hwnd()
        if not hwnd:
            self._phase("recast_blocked window_not_found")
            return False
        # Do not activate, restore, or click into a minimized/inactive game.
        # A paused game has no safe frame in which to consume this click, and
        # foreground stealing can unexpectedly restore a minimized window.
        try:
            iconic = int(bool(win32gui.IsIconic(hwnd)))
            visible = int(bool(win32gui.IsWindowVisible(hwnd)))
            foreground = int(win32gui.GetForegroundWindow() == hwnd)
        except Exception:
            iconic = visible = foreground = -1
        if iconic == 1 and self._restore_minimized_window:
            if not self._ensure_terraria_focused():
                self._phase("recast_blocked window_restore_failed")
                return False
            hwnd = self._find_terraria_hwnd()
            if not hwnd:
                self._phase("recast_blocked window_not_found_after_restore")
                return False
            try:
                iconic = int(bool(win32gui.IsIconic(hwnd)))
                visible = int(bool(win32gui.IsWindowVisible(hwnd)))
                foreground = int(win32gui.GetForegroundWindow() == hwnd)
            except Exception:
                iconic = visible = foreground = -1
        if iconic == 1 or visible == 0 or foreground == 0:
            self._phase(
                f"recast_blocked window_not_ready iconic={iconic} "
                f"visible={visible} foreground={foreground}"
            )
            return False
        if hwnd:
            self._aim_cursor(hwnd)
            self._sleep_interruptible(AIM_SETTLE_S)
            # The window can become inactive while the cursor settles. Check
            # again immediately before the first physical input.
            try:
                if (
                    win32gui.IsIconic(hwnd)
                    or not win32gui.IsWindowVisible(hwnd)
                    or win32gui.GetForegroundWindow() != hwnd
                ):
                    self._phase("recast_blocked window_changed")
                    return False
            except Exception:
                pass
        if self.stop_event.is_set():
            self._lmb_release()
            return False
        if self._probe is not None and self._probe_enabled:
            self._probe.request_recast()
        tick_before = self._player_frame_tick()
        if tick_before is None:
            self._phase("recast_blocked tick_unavailable")
            return False
        tick_down, align_reads = self._wait_for_player_frame_tick(
            tick_before, RECAST_TICK_TIMEOUT_S,
        )
        if tick_down is None:
            self._phase(
                f"recast_blocked tick_align_timeout tick={tick_before} "
                f"reads={align_reads}"
            )
            return False
        self.on_status("initial_cast")
        down_sent = self._send_mouse(win32con.MOUSEEVENTF_LEFTDOWN)
        pulses = 0
        up_sent = False
        tick_up = None
        hold_reads = 0

        def pulse_control() -> None:
            nonlocal pulses
            # Terraria copies its mouse state over controlUseItem every
            # frame. Keep this flag asserted for the entire one-frame hold,
            # but never write releaseUseItem: that edge would turn a fresh
            # cast into an immediate reel.
            self._write_control_use_item(1)
            pulses += 1

        hold_started = time.monotonic()
        try:
            tick_up, hold_reads = self._wait_for_player_frame_tick(
                tick_down, RECAST_TICK_TIMEOUT_S, pulse=pulse_control,
            )
        finally:
            up_sent = self._send_mouse(win32con.MOUSEEVENTF_LEFTUP)
            self._write_control_use_item(0)
        held_ms = (time.monotonic() - hold_started) * 1000.0
        if tick_up is None:
            self._phase(
                f"recast_blocked tick_hold_timeout tick={tick_down} "
                f"reads={hold_reads} held_ms={held_ms:.1f} "
                f"down={int(down_sent)} up={int(up_sent)} pulses={pulses}"
            )
        if self.stop_event.is_set() or tick_up is None:
            return False
        return True

    def _sleep_interruptible(self, seconds: float):
        deadline = time.monotonic() + seconds
        while not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))

    def _phase(self, msg: str):
        self._debug_log(f"phase {msg}")

    def _item_animation(self) -> int:
        try:
            player = self._local_player()
            if not player:
                return 0
            value = self._read_u32(player + OFF_ITEM_ANIMATION)
            if value > 0:
                self._anim_ever_rose = True
            return value
        except RuntimeError:
            return 0

    def _animation_rose(self, before: int, anim: int) -> bool:
        return (before == 0 and anim != 0) or anim > before

    def _wait_animation_clear(self, timeout_s: float = None) -> bool:
        if timeout_s is None:
            timeout_s = WAIT_ANIM_CLEAR_S
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self._item_animation() == 0:
                return True
            time.sleep(0.03)
        return self._item_animation() == 0

    def _close_handles(self):
        self._stop_probe()
        if self.pm is not None:
            try:
                self.pm.close_process()
            except Exception:
                pass
            self.pm = None
        self.process_handle = None
