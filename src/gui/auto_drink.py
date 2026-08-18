import threading
import time

import win32api
import win32con

from gui.prefs import normalize_quick_buff_key

VK_MAP = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"}}


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
        self._thread = None

    def start(self, quick_buff_key):
        self.stop_event.clear()
        key = normalize_quick_buff_key(quick_buff_key)
        self._thread = threading.Thread(
            target=self.run,
            args=(time.monotonic(), key),
            daemon=True,
            name="AutoDrink",
        )
        self._thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self, current_time, quick_buff_key):
        key = normalize_quick_buff_key(quick_buff_key)
        while not self.stop_event.is_set():
            press_key(key)
            sleep_for = 61.0 - ((time.monotonic() - current_time) % 61.0)
            end = time.monotonic() + sleep_for
            while time.monotonic() < end:
                if self.stop_event.is_set():
                    return
                time.sleep(0.2)
