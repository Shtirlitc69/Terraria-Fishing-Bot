import threading
import time

import win32api
import win32con

from gui.prefs import normalize_quick_buff_key, normalize_auto_drink_watch
from player_buffs import BUFF_CRATE, BUFF_FISHING, BUFF_SONAR, PlayerBuffs

VK_MAP = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"}}

POLL_S = 0.25
APPLY_WAIT_S = 1.5
BIND_RETRY_S = 8.0
WATCH_LONG = frozenset((BUFF_FISHING, BUFF_SONAR))

WATCH_IDS = {
    "all": WATCH_LONG,
    "crate": frozenset((BUFF_CRATE,)),
}


def press_key(key: str):
    vk = VK_MAP.get(key.lower())
    if vk is None:
        return
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


class AutoDrink:
    def __init__(self):
        self.stop_event = threading.Event()
        self.suspended = threading.Event()
        self._thread = None
        self._watch = "all"

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_suspended(self, busy: bool):
        if busy:
            self.suspended.set()
        else:
            self.suspended.clear()

    def start(self, quick_buff_key, player_source, watch="all", on_status=None):
        self.stop()
        self.stop_event.clear()
        self.suspended.clear()
        key = normalize_quick_buff_key(quick_buff_key)
        self._watch = normalize_auto_drink_watch(watch)
        self._thread = threading.Thread(
            target=self._run,
            args=(key, player_source, on_status),
            daemon=True,
            name="AutoDrink",
        )
        self._thread.start()

    def set_watch(self, watch: str):
        self._watch = normalize_auto_drink_watch(watch)

    def stop(self):
        self.stop_event.set()
        self.suspended.clear()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def _watched(self) -> frozenset:
        return WATCH_IDS.get(self._watch, WATCH_LONG)

    def _wait_unsuspended(self) -> bool:
        while self.suspended.is_set():
            if self.stop_event.is_set():
                return False
            time.sleep(0.05)
        return not self.stop_event.is_set()

    def _sleep(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                return False
            time.sleep(min(0.1, deadline - time.monotonic()))
        return not self.stop_event.is_set()

    def _player(self, player_source):
        try:
            handle, player = player_source()
        except Exception:
            return None, 0
        return handle, int(player or 0)

    def _run(self, key, player_source, on_status):
        buffs = PlayerBuffs()
        deadline = time.monotonic() + BIND_RETRY_S
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            handle, player = self._player(player_source)
            if handle and player and buffs.bind(handle, player):
                break
            time.sleep(0.2)
        else:
            if not self.stop_event.is_set() and on_status:
                on_status("auto_drink_buffs_missing")
            return

        press_key(key)
        if not self._sleep(APPLY_WAIT_S):
            return
        handle, player = self._player(player_source)
        if handle and player:
            buffs.refine(handle, player)
        present = self._snapshot(buffs, player_source)

        while not self.stop_event.is_set():
            if not self._wait_unsuspended():
                return
            now = self._snapshot(buffs, player_source)
            expired = present - now
            if expired:
                if not self._wait_unsuspended():
                    return
                press_key(key)
                if not self._sleep(APPLY_WAIT_S):
                    return
                present = self._snapshot(buffs, player_source)
                continue
            present = now
            if not self._sleep(POLL_S):
                return

    def _snapshot(self, buffs: PlayerBuffs, player_source) -> frozenset:
        handle, player = self._player(player_source)
        if not handle or not player:
            return frozenset()
        return buffs.active_ids(handle, player) & self._watched()
