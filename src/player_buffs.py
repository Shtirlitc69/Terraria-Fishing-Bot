"""Read-only scan of Player.buffType / Player.buffTime in Terraria 1.4.5.x.

Does not write process memory. Fishing-bot click / use-item paths stay in
memory_bot.py and must not be used from here.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

BUFF_FISHING = 121
BUFF_SONAR = 122
BUFF_CRATE = 123
FISHING_BUFF_IDS = frozenset((BUFF_FISHING, BUFF_SONAR, BUFF_CRATE))

BUFF_ARRAY_LENS = frozenset((22, 44))
PLAYER_SCAN_END = 0x1000
USER_SPACE_END = 0x7FFFFFFF
MAX_BUFF_ID = 512
CLR_ARRAY_DATA = 8

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
ReadProcessMemory.restype = wt.BOOL


def _read_u32(handle, addr: int) -> int | None:
    if not handle or not addr:
        return None
    buf = (ctypes.c_ubyte * 4)()
    n = ctypes.c_size_t(0)
    if not ReadProcessMemory(
        handle, ctypes.c_void_p(addr), buf, 4, ctypes.byref(n)
    ) or n.value != 4:
        return None
    return int.from_bytes(buf, "little")


def _read_ints(handle, addr: int, count: int) -> list[int] | None:
    if not handle or not addr or count <= 0 or count > 128:
        return None
    size = count * 4
    buf = (ctypes.c_ubyte * size)()
    n = ctypes.c_size_t(0)
    if not ReadProcessMemory(
        handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)
    ) or n.value != size:
        return None
    return [
        int.from_bytes(buf[i:i + 4], "little") for i in range(0, size, 4)
    ]


def _heap_ptr(value: int) -> bool:
    return 0x10000 < value < USER_SPACE_END


def _array_len(handle, ptr: int) -> int | None:
    if not _heap_ptr(ptr):
        return None
    length = _read_u32(handle, ptr + 4)
    if length in BUFF_ARRAY_LENS:
        return length
    return None


def _looks_like_types(values: list[int]) -> bool:
    return all(0 <= v <= MAX_BUFF_ID for v in values)


class PlayerBuffs:
    """Locate buffType/buffTime field offsets on a Player instance and read IDs."""

    def __init__(self):
        self._type_off = 0
        self._time_off = 0
        self._length = 0

    def bound(self) -> bool:
        return bool(self._type_off and self._length)

    def bind(self, handle, player: int) -> bool:
        self._type_off = 0
        self._time_off = 0
        self._length = 0
        if not handle or not _heap_ptr(player):
            return False
        best = None
        best_key = None
        for off in range(4, PLAYER_SCAN_END, 4):
            p_type = _read_u32(handle, player + off)
            p_time = _read_u32(handle, player + off + 4)
            if p_type is None or p_time is None:
                continue
            n_type = _array_len(handle, p_type)
            n_time = _array_len(handle, p_time)
            if not n_type or n_type != n_time:
                continue
            types = _read_ints(handle, p_type + CLR_ARRAY_DATA, n_type)
            times = _read_ints(handle, p_time + CLR_ARRAY_DATA, n_time)
            if types is None or times is None:
                continue
            type_off, time_off = off, off + 4
            if not _looks_like_types(types) and _looks_like_types(times):
                type_off, time_off = time_off, type_off
                types, times = times, types
            elif not _looks_like_types(types):
                continue
            score = n_type
            if FISHING_BUFF_IDS & set(types):
                score += 100
            if any(t > MAX_BUFF_ID for t in times):
                score += 10
            key = (-score, off)
            if best_key is None or key < best_key:
                best_key = key
                best = (type_off, time_off, n_type)
        if best is None:
            return False
        self._type_off, self._time_off, self._length = best
        return True

    def refine(self, handle, player: int) -> None:
        if not self.bound() or not handle or not _heap_ptr(player):
            return
        types = self._read_type_values(handle, player)
        times = self._read_time_values(handle, player)
        if types is None or times is None:
            return
        types_hit = bool(FISHING_BUFF_IDS & set(types))
        times_hit = bool(FISHING_BUFF_IDS & set(v for v in times if v <= MAX_BUFF_ID))
        if times_hit and not types_hit:
            self._type_off, self._time_off = self._time_off, self._type_off

    def active_ids(self, handle, player: int) -> frozenset[int]:
        values = self._read_type_values(handle, player)
        if not values:
            return frozenset()
        times = self._read_time_values(handle, player)
        found = []
        for i, buff_id in enumerate(values):
            if buff_id <= 0:
                continue
            if times is not None and i < len(times) and times[i] <= 0:
                continue
            found.append(buff_id)
        return frozenset(found)

    def _read_type_values(self, handle, player: int) -> list[int] | None:
        return self._read_array(handle, player, self._type_off)

    def _read_time_values(self, handle, player: int) -> list[int] | None:
        return self._read_array(handle, player, self._time_off)

    def _read_array(self, handle, player: int, field_off: int) -> list[int] | None:
        if not self._length or not field_off:
            return None
        ptr = _read_u32(handle, player + field_off)
        if not ptr:
            return None
        length = _array_len(handle, ptr)
        if length != self._length:
            return None
        return _read_ints(handle, ptr + CLR_ARRAY_DATA, self._length)
