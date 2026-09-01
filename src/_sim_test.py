"""Offline simulation for the memory-bot loop (no Terraria, no real input).

Stubs win32/pymem before importing memory_bot, replaces low-level memory and
input helpers with a scripted fake game, speeds up timing constants, then runs
the real _run loop: dunk (ai1 < 0) → one reel click → one recast. A sticky
rolledItemDrop without a dunk must not click.
"""
import sys
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

# --- Stub Windows-only modules before importing memory_bot. ---
types_mod = type(sys)


class _FakeFunc:
    def __call__(self, *a, **k):
        return 0


class _FakeDll:
    def __init__(self):
        self._funcs = {}

    def __getattr__(self, name):
        fn = self._funcs.get(name)
        if fn is None:
            fn = _FakeFunc()
            self._funcs[name] = fn
        return fn


class _WindllStub:
    def __getattr__(self, name):
        return _FakeDll()


import ctypes

ctypes.windll = _WindllStub()

win32api = types_mod("win32api")
win32con = types_mod("win32con")
win32gui = types_mod("win32gui")
win32process = types_mod("win32process")
pymem = types_mod("pymem")


class _PymemExc:
    ProcessNotFound = Exception


pymem.exception = _PymemExc

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
win32con.MOUSEEVENTF_LEFTDOWN = MOUSEEVENTF_LEFTDOWN
win32con.MOUSEEVENTF_LEFTUP = MOUSEEVENTF_LEFTUP
win32con.VK_LBUTTON = 0x01
win32con.GW_OWNER = 4

for name, mod in (
    ("win32api", win32api),
    ("win32con", win32con),
    ("win32gui", win32gui),
    ("win32process", win32process),
    ("pymem", pymem),
):
    sys.modules[name] = mod

import importlib.util

spec = importlib.util.spec_from_file_location("memory_bot", SRC / "memory_bot.py")
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

# Speed up every sleep-driven constant so failure rounds finish quickly.
for const, val in (
    ("CLICK_HOLD_S", 0.002),
    ("AIM_SETTLE_S", 0.001),
    ("USE_ITEM_PULSE_S", 0.001),
    ("REEL_RETRY_PAUSE_S", 0.005),
    ("CATCH_DELAY_S", 0.005),
    ("LINE_SNAP_GRACE_S", 0.01),
    ("RECAST_INTERVAL_S", 0.005),
    ("RECAST_SETTLE_S", 0.005),
    ("RECAST_ANIM_TIMEOUT_S", 0.05),
    ("WAIT_ANIM_CLEAR_S", 0.02),
    ("RECAST_TICK_TIMEOUT_S", 0.005),
    ("RECAST_STABLE_S", 0.01),
    ("RECAST_OLD_BOBBER_WAIT_S", 0.01),
    ("RECAST_POLL_S", 0.001),
    ("RECAST_RETRY_PAUSE_S", 0.005),
    ("BOBBER_WAIT_S", 0.02),
    ("BOBBER_CALIB_TIMEOUT_S", 0.4),
    ("BOBBER_CALIB_EXTRA_S", 0.02),
    ("CALIB_POLL_S", 0.001),
    ("BOBBER_CALIB_STABLE_FRAMES", 2),
):
    setattr(mb, const, val)

MemoryBot = mb.MemoryBot

BOBBER_PTR = 0x20000000
BASS_ID = 2290
UNKNOWN_ID = 3000  # not in the whitelist


class FakeGame:
    """Scripted Terraria memory state shared by one scenario."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rolled = 0
        self.bobbers = 1
        self.anim = 0
        self.dead_anim = False
        self.ai1 = 0.0
        # After a recast, optionally start another dunk (same or new id).
        self.redunk_ai1 = 0.0
        self.redunk_rolled = 0
        self.land_from_click = None
        self.reel_clicks = 0
        self.recasts = 0

    def on_reel_click(self):
        self.reel_clicks += 1
        self.ai1 = 0.0
        if self.land_from_click is None or self.reel_clicks < self.land_from_click:
            return
        self.bobbers = 0
        self.rolled = 0
        if not self.dead_anim:
            self.anim = 0


GAME = FakeGame()


def make_bot(statuses, verified=True):
    bot = MemoryBot(
        on_catch=lambda i: statuses.append(f"catch:{i}"),
        on_status=lambda s: statuses.append(s),
        on_error=lambda e: statuses.append(f"error:{e}"),
        whitelist_ids={BASS_ID},
        poll_interval=0.001,
        aim_client=(100, 100),
    )
    # Simulated hook result: skip the real JIT scan entirely.
    bot.process_handle = -1
    bot._pid = 4242
    bot._rolled_ptr = 0x10000000
    bot._player_static = 0x30000000
    bot._projectile_static = 0x40000000
    bot._t0 = time.monotonic()
    bot._hook = lambda: None

    def ensure_projectile_array():
        return True

    bot._ensure_projectile_array = ensure_projectile_array

    def read_rolled():
        with GAME.lock:
            return GAME.rolled

    def local_bobbers():
        with GAME.lock:
            n = GAME.bobbers
        return [(0, BOBBER_PTR)] * n if n else []

    def item_animation():
        with GAME.lock:
            value = GAME.anim
            GAME.anim = 0
            return value

    def click_left(kind):
        with GAME.lock:
            GAME.on_reel_click()
            rose = GAME.anim != 0
        return rose

    def recast_until_bobber():
        with GAME.lock:
            GAME.recasts += 1
            GAME.bobbers = max(1, GAME.bobbers)
            if GAME.redunk_ai1:
                GAME.ai1 = GAME.redunk_ai1
                if GAME.redunk_rolled:
                    GAME.rolled = GAME.redunk_rolled
                GAME.redunk_ai1 = 0.0
            else:
                GAME.ai1 = 0.0
        return True

    def bobber_ai1():
        with GAME.lock:
            return GAME.ai1

    bot._read_rolled = read_rolled
    bot._local_bobbers = local_bobbers
    bot._item_animation = item_animation
    bot._click_left = click_left
    bot._recast_until_bobber = recast_until_bobber
    bot._bobber_ai1 = bobber_ai1
    bot._resolve_ai_offs_from_bobbers = lambda bobbers=None: False
    bot._write_use_item = lambda v, edge=False: None
    bot._write_control_use_item = lambda v: None
    bot._send_mouse_click = lambda: True
    bot._ensure_terraria_focused = lambda: True
    bot._find_terraria_hwnd = lambda: 0
    bot._aim_cursor = lambda hwnd: None
    bot._probe_failed_fishing_flag = lambda: None

    lmb_events = []

    def send_mouse(flags):
        if flags & MOUSEEVENTF_LEFTUP:
            lmb_events.append("up")

    bot._send_mouse = send_mouse
    bot._lmb_events = lmb_events

    # Apply scripted state only after _sanitize_start cleared the world.
    orig_sanitize = bot._sanitize_start

    def sanitize_then_seed():
        orig_sanitize()
        if verified is False:
            # Degrade path: keep the layout unverified even if a bobber
            # was already in the water during sanitize.
            bot._bobber_verified_layout = False
        seed = getattr(bot, "_seed_state", None)
        if seed is not None:
            with GAME.lock:
                seed(GAME)

    bot._sanitize_start = sanitize_then_seed
    # Calibration is exercised separately; loop scenarios start calibrated
    # unless the test is covering the unverified-degrade path.
    # verified=None: leave the flag to _sanitize_start (live bobber → True).
    bot._calibrate_bobber_layout = lambda: bool(verified)
    if verified is not None:
        bot._bobber_verified_layout = bool(verified)
    return bot


def run_scenario(
    seed, on_status=None, until=None, timeout_s=10.0, verified=True
):
    statuses = []
    bot = make_bot(statuses, verified=verified)
    bot._seed_state = seed
    if on_status is not None:
        orig = bot.on_status

        def wrapped(msg):
            on_status(msg, GAME)
            orig(msg)

        bot.on_status = wrapped
    thread = threading.Thread(target=bot._run, daemon=True)
    thread.start()

    def satisfied():
        if until is None:
            return False
        return until(statuses)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if satisfied():
            break
        time.sleep(0.005)
    bot.stop()
    thread.join(timeout=3)
    return statuses, bot._lmb_events


def test_normal_catch():
    """Whitelisted dunk → one reel click, catch, one recast."""

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.dead_anim = False
        g.land_from_click = 1

    statuses, _ = run_scenario(
        seed,
        until=lambda s: f"catch:{BASS_ID}" in s and GAME.recasts >= 1,
    )
    assert f"catch:{BASS_ID}" in statuses, statuses
    assert f"caught_recovery:{BASS_ID}" not in statuses, statuses
    assert GAME.reel_clicks == 1, GAME.reel_clicks
    assert GAME.recasts == 1, GAME.recasts
    assert not any(x.startswith("error:") for x in statuses), statuses
    assert not any(x.startswith("reel_retrying:") for x in statuses), statuses


def test_dunk_one_click_dead_anim():
    """Dunk is enough: a dead itemAnimation offset still reels once."""

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.dead_anim = True
        g.land_from_click = 1

    statuses, _ = run_scenario(
        seed,
        until=lambda s: f"catch:{BASS_ID}" in s and GAME.recasts >= 1,
    )
    assert f"catch:{BASS_ID}" in statuses, statuses
    assert GAME.reel_clicks == 1, GAME.reel_clicks
    assert GAME.recasts == 1, GAME.recasts
    assert not any(x.startswith("caught_recovery:") for x in statuses), statuses


def test_skip_then_bass_dunk():
    """Unknown dunk is yanked and recast; a later bass dunk still lands."""

    def seed(g):
        g.rolled = UNKNOWN_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.redunk_ai1 = -8.0
        g.redunk_rolled = BASS_ID
        g.land_from_click = 2

    statuses, _ = run_scenario(
        seed,
        until=lambda s: f"catch:{BASS_ID}" in s and GAME.recasts >= 2,
        timeout_s=8.0,
    )
    assert f"bite:{UNKNOWN_ID}:skip" in statuses, statuses
    assert f"catch:{BASS_ID}" in statuses, statuses
    assert GAME.reel_clicks == 2, GAME.reel_clicks
    assert GAME.recasts >= 2, GAME.recasts
    assert not any(x.startswith("error:") for x in statuses), statuses


def test_stale_id_without_dunk_does_not_reel():
    """Sticky rolledItemDrop with ai1>=0 is not a bite."""

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = 0.0

    statuses, _ = run_scenario(seed, timeout_s=0.25)
    assert GAME.reel_clicks == 0, (GAME.reel_clicks, statuses)
    assert not any(x.startswith("catch:") for x in statuses), statuses


def test_two_bass_dunks():
    """Two dunks with the same id are two catches."""

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.redunk_ai1 = -8.0
        g.redunk_rolled = BASS_ID
        g.land_from_click = 1

    statuses, _ = run_scenario(
        seed,
        until=lambda s: s.count(f"catch:{BASS_ID}") >= 2 and GAME.recasts >= 2,
        timeout_s=8.0,
    )
    assert statuses.count(f"catch:{BASS_ID}") >= 2, statuses
    assert GAME.reel_clicks == 2, GAME.reel_clicks
    assert GAME.recasts == 2, GAME.recasts


def test_stop_releases_mouse():
    """stop() must emit LEFTUP so LMB is never stuck down."""
    statuses, ups = run_scenario(lambda g: None, timeout_s=0.2)
    assert ups, "expected at least one LEFTUP from stop()"


def test_calibration_consensus():
    """Consensus picks offsets closest to the known layout, active gated."""
    bobber = bytes(0x100)
    # Shifted layout: type at 0x80, owner at 0x60, active at 0x0A.
    blob = bytearray(bobber)
    blob[0x80:0x84] = (360).to_bytes(4, "little")
    blob[0x60:0x64] = (0).to_bytes(4, "little")
    blob[0x0A] = 1
    # A decoy: same value 360 also appears at 0xC4.
    blob[0xC4:0xC8] = (360).to_bytes(4, "little")
    idle = bytearray(bytes(0x100))
    idle[0x80:0x84] = (999).to_bytes(4, "little")  # some other type value
    scored = [mb.MemoryBot._score_bobber_blob(bytes(blob), 0)]
    bot_stub = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    layout = bot_stub._consensus_layout(
        scored, [bytes(idle), bytes(idle)]
    )
    assert layout is not None, layout
    assert layout["type_off"] == 0x80, layout
    assert layout["owner_off"] == 0x5C or layout["owner_off"] == 0x60, layout
    assert layout["active_off"] == 0x08 or layout["active_off"] == 0x0A, layout


def test_whoami_not_counted_as_bobber_type():
    """Slot 360's whoAmI==360 must not look like a bobber type field."""
    blob = bytearray(bytes(0x100))
    blob[0x04:0x08] = (360).to_bytes(4, "little")
    blob[0x08] = 0
    scored = MemoryBot._score_bobber_blob(bytes(blob), 0)
    assert 4 not in scored["type_off"], scored
    assert MemoryBot._blob_has_bobber_type(bytes(blob)) is False


def test_sanitize_bobber_present_sets_verified():
    """A bobber already in the water must mark the layout verified.

    Calibration is skipped. The first recast must not fire.
    """
    statuses = []
    bot = make_bot(statuses, verified=None)

    def must_not_calibrate():
        raise AssertionError("calibration must be skipped when a bobber is visible")

    bot._calibrate_bobber_layout = must_not_calibrate
    GAME.bobbers = 1
    GAME.rolled = BASS_ID
    GAME.ai1 = 0.0
    MemoryBot._sanitize_start(bot)
    assert bot._bobber_verified_layout is True, bot._bobber_verified_layout
    assert GAME.recasts == 0, GAME.recasts
    assert "initial_cast" not in statuses, statuses


def test_sanitize_live_bobber_dunks_once():
    """Start with a live bobber: sanitize verifies layout, dunk reels once."""

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.dead_anim = True
        g.land_from_click = 1

    statuses, _ = run_scenario(
        seed,
        until=lambda s: f"catch:{BASS_ID}" in s and GAME.recasts >= 1,
        verified=None,
    )
    assert f"catch:{BASS_ID}" in statuses, statuses
    assert GAME.reel_clicks == 1, GAME.reel_clicks
    assert GAME.recasts == 1, GAME.recasts
    assert not any(x.startswith("error:") for x in statuses), statuses


def test_recast_click_holds_for_one_game_tick():
    """Recast must press and release once, bounded by two Player updates."""
    events = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._ensure_terraria_focused = lambda: True
    bot._find_terraria_hwnd = lambda: 0x1234
    bot._aim_cursor = lambda hwnd: True
    bot._write_use_item = lambda v, edge=False: None
    bot._write_control_use_item = lambda v: None
    bot._lmb_release = lambda: None
    ticks = iter((100, 100, 101, 101, 102))
    bot._player_frame_tick = lambda: next(ticks)

    def send_mouse(flags):
        events.append((flags, time.perf_counter()))
        return True

    bot._send_mouse = send_mouse
    assert bot._recast_click() is True
    downs = [t for flags, t in events if flags == MOUSEEVENTF_LEFTDOWN]
    ups = [t for flags, t in events if flags == MOUSEEVENTF_LEFTUP]
    assert len(downs) == 1 and len(ups) == 1, events
    assert ups[0] >= downs[0], ups[0] - downs[0]


def test_recast_click_does_not_pulse_release_use_item():
    """Recast must write controlUseItem only, never the releaseUseItem edge."""
    control = []
    use_item = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._ensure_terraria_focused = lambda: True
    bot._find_terraria_hwnd = lambda: 0x1234
    bot._aim_cursor = lambda hwnd: True
    bot._write_use_item = lambda v, edge=False: use_item.append((v, edge))
    bot._write_control_use_item = lambda v: control.append(v)
    bot._send_mouse = lambda flags: True
    bot._lmb_release = lambda: None
    ticks = iter((100, 100, 101, 101, 102))
    bot._player_frame_tick = lambda: next(ticks)
    assert bot._recast_click() is True
    assert use_item == [], use_item
    assert control[0] == 1 and control[-1] == 0, control
    assert set(control[:-1]) == {1}, control


def test_recast_releases_after_stalled_tick_without_retry():
    """A stalled game must release the one click and never inject a second one."""
    events = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._ensure_terraria_focused = lambda: True
    bot._find_terraria_hwnd = lambda: 0x1234
    bot._aim_cursor = lambda hwnd: True
    bot._write_control_use_item = lambda v: None
    bot._lmb_release = lambda: None
    bot._player_frame_tick = lambda: 100
    waits = iter(((101, 1), (None, 2)))
    bot._wait_for_player_frame_tick = lambda previous, timeout_s, pulse=None: next(waits)
    bot._send_mouse = lambda flags: events.append(flags) or True

    assert bot._recast_click() is False
    assert events == [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP], events


def test_recast_does_not_restore_or_click_minimized_game():
    """A minimized single-player game is a fail-closed, no-input path."""
    events = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._find_terraria_hwnd = lambda: 0x1234
    bot._ensure_terraria_focused = lambda: (_ for _ in ()).throw(
        AssertionError("focus must not restore a minimized window")
    )
    bot._send_mouse = lambda flags: events.append(flags) or True
    old_iconic = getattr(mb.win32gui, "IsIconic", None)
    old_visible = getattr(mb.win32gui, "IsWindowVisible", None)
    old_foreground = getattr(mb.win32gui, "GetForegroundWindow", None)
    mb.win32gui.IsIconic = lambda hwnd: True
    mb.win32gui.IsWindowVisible = lambda hwnd: True
    mb.win32gui.GetForegroundWindow = lambda: 0
    try:
        assert bot._recast_click() is False
        assert events == [], events
    finally:
        if old_iconic is None:
            del mb.win32gui.IsIconic
        else:
            mb.win32gui.IsIconic = old_iconic
        if old_visible is None:
            del mb.win32gui.IsWindowVisible
        else:
            mb.win32gui.IsWindowVisible = old_visible
        if old_foreground is None:
            del mb.win32gui.GetForegroundWindow
        else:
            mb.win32gui.GetForegroundWindow = old_foreground


def test_recast_does_not_activate_or_click_inactive_game():
    """An inactive Terraria window must stay inactive and receive no input."""
    events = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._find_terraria_hwnd = lambda: 0x1234
    bot._ensure_terraria_focused = lambda: (_ for _ in ()).throw(
        AssertionError("recast must not activate an inactive game")
    )
    bot._send_mouse = lambda flags: events.append(flags) or True
    old_iconic = getattr(mb.win32gui, "IsIconic", None)
    old_visible = getattr(mb.win32gui, "IsWindowVisible", None)
    old_foreground = getattr(mb.win32gui, "GetForegroundWindow", None)
    mb.win32gui.IsIconic = lambda hwnd: False
    mb.win32gui.IsWindowVisible = lambda hwnd: True
    mb.win32gui.GetForegroundWindow = lambda: 0
    try:
        assert bot._recast_click() is False
        assert events == [], events
    finally:
        if old_iconic is None:
            del mb.win32gui.IsIconic
        else:
            mb.win32gui.IsIconic = old_iconic
        if old_visible is None:
            del mb.win32gui.IsWindowVisible
        else:
            mb.win32gui.IsWindowVisible = old_visible
        if old_foreground is None:
            del mb.win32gui.GetForegroundWindow
        else:
            mb.win32gui.GetForegroundWindow = old_foreground


def test_recast_restores_minimized_game_only_when_enabled():
    """Opt-in restoration may activate an iconic game before the one recast."""
    events = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
        restore_minimized_window=True,
    )
    bot._find_terraria_hwnd = lambda: 0x1234
    focused = []
    bot._ensure_terraria_focused = lambda: focused.append(True) or True
    bot._aim_cursor = lambda hwnd: True
    bot._write_control_use_item = lambda value: None
    ticks = iter((100, 100, 101, 101, 102))
    bot._player_frame_tick = lambda: next(ticks)
    bot._send_mouse = lambda flags: events.append(flags) or True
    old_iconic = getattr(mb.win32gui, "IsIconic", None)
    old_visible = getattr(mb.win32gui, "IsWindowVisible", None)
    old_foreground = getattr(mb.win32gui, "GetForegroundWindow", None)
    iconics = iter((True, False, False))
    mb.win32gui.IsIconic = lambda hwnd: next(iconics)
    mb.win32gui.IsWindowVisible = lambda hwnd: True
    mb.win32gui.GetForegroundWindow = lambda: 0x1234
    try:
        assert bot._recast_click() is True
        assert focused == [True], focused
        assert events == [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP], events
    finally:
        if old_iconic is None:
            del mb.win32gui.IsIconic
        else:
            mb.win32gui.IsIconic = old_iconic
        if old_visible is None:
            del mb.win32gui.IsWindowVisible
        else:
            mb.win32gui.IsWindowVisible = old_visible
        if old_foreground is None:
            del mb.win32gui.GetForegroundWindow
        else:
            mb.win32gui.GetForegroundWindow = old_foreground


def test_stale_roll_after_recast_does_not_reel():
    """After a catch the game restores the old id: no dunk → no second reel."""
    statuses = []
    bot = make_bot(statuses, verified=True)

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.dead_anim = False
        g.land_from_click = 1

    bot._seed_state = seed
    thread = threading.Thread(target=bot._run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if f"catch:{BASS_ID}" in statuses:
            break
        time.sleep(0.005)
    assert f"catch:{BASS_ID}" in statuses, statuses
    clicks_after_catch = GAME.reel_clicks
    with GAME.lock:
        GAME.rolled = BASS_ID
        GAME.bobbers = 1
        GAME.ai1 = 0.0
    time.sleep(0.15)
    assert GAME.reel_clicks == clicks_after_catch, (
        GAME.reel_clicks, clicks_after_catch, statuses
    )
    bot.stop()
    thread.join(timeout=3)


def test_dunk_does_not_invalidate_layout():
    """A dunk catch must leave the bobber layout verified."""
    statuses = []
    bot = make_bot(statuses, verified=True)

    def seed(g):
        g.rolled = BASS_ID
        g.bobbers = 1
        g.ai1 = -8.0
        g.dead_anim = True
        g.land_from_click = 1

    bot._seed_state = seed
    thread = threading.Thread(target=bot._run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if f"catch:{BASS_ID}" in statuses:
            break
        time.sleep(0.005)
    bot.stop()
    thread.join(timeout=3)
    assert f"catch:{BASS_ID}" in statuses, statuses
    assert bot._bobber_verified_layout is True, bot._bobber_verified_layout


def test_recast_clicks_once_even_with_stale_bobber():
    """A stale Projectile entry must not suppress the one recast click."""
    clicks = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._ensure_projectile_array = lambda: True
    bot._bobber_verified_layout = True
    bot._local_bobbers = lambda: [(0, BOBBER_PTR)]
    bot._recast_click = lambda: clicks.append(1) or True
    assert bot._recast_until_bobber() is True
    assert clicks == [1], clicks


def test_recast_never_retries_after_confirmed_absence():
    """One recast stays one click even when no bobber is visible."""
    clicks = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
        aim_client=(100, 100),
    )
    bot._ensure_projectile_array = lambda: True
    bot._bobber_verified_layout = True
    bot._local_bobbers = lambda: []
    bot._recast_click = lambda: clicks.append(1) or True
    assert bot._recast_until_bobber() is True
    assert clicks == [1], clicks


def test_scan_ai_array_offs_finds_pointers():
    """A projectile field pointing at an ai-sized CLR float[] is a candidate."""
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    blob = bytearray(0x100)
    blob[0x40:0x44] = (0x50000000).to_bytes(4, "little")
    blob[0x44:0x48] = (0x50001000).to_bytes(4, "little")
    bot._is_clr_float_array = lambda p: p in (0x50000000, 0x50001000)
    offs = bot._scan_ai_array_offs(bytes(blob))
    assert offs == [0x40, 0x44], offs


def test_ai_array_len_accepts_float3():
    """ai/localAI are float[3] on 1.4.5.8; an exact ==2 test found nothing."""
    lengths = {0x50000000: 3, 0x50001000: 2, 0x50002000: 7, 0x50003000: 4}
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    bot._read_u32 = lambda addr: lengths[addr - mb.CLR_SZARRAY_LEN_OFF]
    assert bot._clr_float_array_len(0x50000000) == 3
    assert bot._clr_float_array_len(0x50001000) == 2
    assert bot._clr_float_array_len(0x50003000) == 4
    assert bot._clr_float_array_len(0x50002000) == 0, "length 7 is not ai"
    assert bot._clr_float_array_len(0x100) == 0, "low pointer rejected"


def test_ai_pair_reads_dunk_index_of_float3():
    """ai[1] must be read from a float[3] at element 1, not a fixed +0xc."""
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    arr = 0x50000000
    floats = {
        arr + mb.CLR_SZARRAY_F0_OFF: 0.0,
        arr + mb.CLR_SZARRAY_F0_OFF + 4: -14.0,
        arr + mb.CLR_SZARRAY_F0_OFF + 8: 0.0,
    }
    bot._clr_float_array_len = lambda p: 3 if p == arr else 0
    bot._read_f32 = lambda addr: floats[addr]
    assert bot._ai_pair_at(arr) == (0.0, -14.0)
    # A float[1] cannot hold index 1 and must be rejected, not read OOB.
    bot._clr_float_array_len = lambda p: 1
    assert bot._ai_pair_at(arr) is None


def test_bobber_dunking_negative_ai1():
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    bot._bobber_ai1 = lambda: -12.5
    assert bot._bobber_dunking() is True
    bot._bobber_ai1 = lambda: 0.0
    assert bot._bobber_dunking() is False
    bot._bobber_ai1 = lambda: None
    assert bot._bobber_dunking() is False


def test_ai_offs_retry_is_throttled():
    """The failure path runs from the poll loop; it must not re-read at 40 Hz."""
    reads = {"n": 0}
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    bot._local_bobbers = lambda: [(0, BOBBER_PTR)]

    def read_bytes(addr, size):
        reads["n"] += 1
        return bytes(size)  # all zeros: no pointer resolves to an array

    bot._read_bytes = read_bytes
    bot._scan_ai_array_offs = lambda blob: []
    for _ in range(50):
        assert bot._resolve_ai_offs_from_bobbers() is False
    assert reads["n"] == 1, reads
    assert bot._ai_offs_fail_count == 1, bot._ai_offs_fail_count
    # Once the throttle expires it tries again, and success is sticky.
    bot._ai_offs_retry_at = 0.0
    bot._scan_ai_array_offs = lambda blob: [0x40, 0x44]
    bot._save_cached_layout = lambda layout: None
    assert bot._resolve_ai_offs_from_bobbers() is True
    assert bot._ai_offs == [0x40, 0x44], bot._ai_offs
    before = reads["n"]
    assert bot._resolve_ai_offs_from_bobbers() is True
    assert reads["n"] == before, "cached offsets must not re-read"


def test_dunk_fallback_after_timeout():
    """Unresolvable ai offsets degrade to the rolled-id bite rule."""
    statuses = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: statuses.append(s),
        on_error=lambda e: None,
    )
    bot._resolve_ai_offs_from_bobbers = lambda bobbers=None: False
    bot._t0 = time.monotonic()
    # Before the grace period: no bite, no fallback.
    assert bot._next_bite() is None
    assert bot._dunk_fallback is False
    assert statuses == [], statuses

    bot._t0 = time.monotonic() - mb.DUNK_FALLBACK_AFTER_S - 1.0
    assert bot._next_bite() is None  # this poll arms the fallback
    assert bot._dunk_fallback is True
    assert "dunk_unavailable" in statuses, statuses

    rolled = {"v": BASS_ID}
    bot._read_rolled = lambda: rolled["v"]
    assert bot._next_bite() == BASS_ID
    assert bot._next_bite() is None, "sticky id must not re-fire"
    rolled["v"] = 0
    assert bot._next_bite() is None
    rolled["v"] = BASS_ID
    assert bot._next_bite() == BASS_ID, "re-arms after rolled returns to 0"


def test_dunk_fallback_recovers_when_offsets_appear():
    """If ai offsets resolve later, the loop returns to dunk detection."""
    statuses = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: statuses.append(s),
        on_error=lambda e: None,
    )
    bot._resolve_ai_offs_from_bobbers = lambda bobbers=None: False
    bot._t0 = time.monotonic() - mb.DUNK_FALLBACK_AFTER_S - 1.0
    bot._next_bite()
    assert bot._dunk_fallback is True

    bot._ai_offs = [0x40, 0x44]
    bot._bobber_ai1 = lambda: -9.0
    bot._read_rolled = lambda: BASS_ID
    assert bot._next_bite() == BASS_ID
    assert bot._dunk_fallback is False, "fallback must clear once ai works"
    assert bot._next_bite() is None, "dunk stays latched until cleared"
    bot._clear_bite_latch()
    assert bot._next_bite() == BASS_ID


def test_one_dunk_yields_one_bite():
    """A dunk lasts ~2s at 40 Hz; it must reel once, not once per poll."""
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    bot._ai_offs = [0x40]
    ai1 = {"v": -14.0}
    bot._bobber_ai1 = lambda: ai1["v"]
    bot._read_rolled = lambda: BASS_ID
    assert bot._next_bite() == BASS_ID
    for _ in range(60):
        assert bot._next_bite() is None
    ai1["v"] = 0.0  # bobber surfaces
    assert bot._next_bite() is None
    ai1["v"] = -12.0  # next fish
    assert bot._next_bite() == BASS_ID


def test_calibration_picks_new_bobber_among_junk():
    """A reused projectile slot that newly gains a bobber type is the candidate.

    Pre-existing junk blobs (no bobber id) must not enter consensus even
    if default owner/active offsets would have matched them.
    """
    mb._HOOK_CACHE["bobber_layout"] = None
    junk = bytes(0x100)
    bobber = bytearray(bytes(0x100))
    bobber[0x80:0x84] = (360).to_bytes(4, "little")
    bobber[0x60:0x64] = (0).to_bytes(4, "little")
    bobber[0x0A] = 1
    idle = bytes(0x100)
    junk_ptr = 0x21000000
    bob_ptr = 0x21000100
    polls = {"n": 0}

    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: None,
        on_error=lambda e: None,
    )
    bot._load_cached_layout = lambda: None
    bot._save_cached_layout = lambda layout: None
    bot._ensure_projectile_array = lambda: True
    bot._my_player_id = lambda: 0
    bot._local_bobbers = (
        lambda: [(7, bob_ptr)] if bot.bobber_layout else []
    )
    bot._sample_idle_projectiles = lambda excl, limit=12: [idle]
    auto_casts = []
    bot._recast_click = lambda: auto_casts.append(1) or True
    bot._resolve_ai_offs_from_bobbers = lambda bobbers=None: False

    def raw_blobs():
        polls["n"] += 1
        entries = [(1, junk_ptr, junk)]
        if polls["n"] >= 3:
            entries.append((7, bob_ptr, bytes(bobber)))
        return entries

    bot._raw_projectile_blobs = raw_blobs
    try:
        assert bot._calibrate_bobber_layout() is True
        layout = bot.bobber_layout
        assert layout is not None, layout
        assert layout["type_off"] == 0x80, layout
        assert auto_casts == [], auto_casts
    finally:
        bot._apply_bobber_layout(None)
        mb._HOOK_CACHE["bobber_layout"] = None


def test_calib_stop_does_not_emit_timeout():
    mb._HOOK_CACHE["bobber_layout"] = None
    statuses = []
    bot = MemoryBot(
        on_catch=lambda i: None,
        on_status=lambda s: statuses.append(s),
        on_error=lambda e: None,
    )
    bot._load_cached_layout = lambda: None
    bot._save_cached_layout = lambda layout: None
    bot._ensure_projectile_array = lambda: True
    bot._my_player_id = lambda: 0
    bot._local_bobbers = lambda: []
    bot._raw_projectile_blobs = lambda: [(0, 0x21000000, bytes(0x100))]
    bot._recast_click = lambda: True
    bot.stop_event.set()
    try:
        assert bot._calibrate_bobber_layout() is False
        assert "calibrate_timeout" not in statuses, statuses
    finally:
        bot._apply_bobber_layout(None)
        mb._HOOK_CACHE["bobber_layout"] = None


SCENARIOS = [
    test_normal_catch,
    test_dunk_one_click_dead_anim,
    test_skip_then_bass_dunk,
    test_stale_id_without_dunk_does_not_reel,
    test_two_bass_dunks,
    test_stop_releases_mouse,
    test_calibration_consensus,
    test_whoami_not_counted_as_bobber_type,
    test_scan_ai_array_offs_finds_pointers,
    test_ai_array_len_accepts_float3,
    test_ai_pair_reads_dunk_index_of_float3,
    test_bobber_dunking_negative_ai1,
    test_ai_offs_retry_is_throttled,
    test_dunk_fallback_after_timeout,
    test_dunk_fallback_recovers_when_offsets_appear,
    test_one_dunk_yields_one_bite,
    test_sanitize_bobber_present_sets_verified,
    test_sanitize_live_bobber_dunks_once,
    test_recast_click_holds_for_one_game_tick,
    test_recast_click_does_not_pulse_release_use_item,
    test_recast_releases_after_stalled_tick_without_retry,
    test_recast_does_not_restore_or_click_minimized_game,
    test_recast_does_not_activate_or_click_inactive_game,
    test_recast_restores_minimized_game_only_when_enabled,
    test_stale_roll_after_recast_does_not_reel,
    test_dunk_does_not_invalidate_layout,
    test_recast_clicks_once_even_with_stale_bobber,
    test_recast_never_retries_after_confirmed_absence,
    test_calibration_picks_new_bobber_among_junk,
    test_calib_stop_does_not_emit_timeout,
]


def main():
    failures = []
    for fn in SCENARIOS:
        GAME.__dict__.update(FakeGame().__dict__)
        GAME.lock = threading.Lock()
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failures.append((fn.__name__, exc))
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"ERROR {fn.__name__}: {exc!r}")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nAll offline scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
