"""Standalone diagnostic probe for memory_bot on the live Terraria process.

Run it while Terraria is open and you've cast at least one line (so
Projectile.FishingCheck gets JIT-compiled). It prints:
  - whether Terraria.exe was found and its bitness
  - whether the FishingCheck JIT signature matched
  - the resolved static _context address
  - a sample of read `rolledItemDrop` values every 0.2s for ~10 seconds

This is for debugging the memory-reader path only — it does not interact
with the GUI or click anything.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

import pymem

import memory_bot


def main():
    print("mem_probe: looking for Terraria.exe ...")
    try:
        pm = pymem.Pymem("Terraria.exe")
    except pymem.exception.ProcessNotFound as e:
        print(f"  FAIL: {e}", file=sys.stderr)
        print("  Start Terraria and enter a world, then re-run this probe.")
        return 1

    print(f"  pid={pm.process_id}  handle={pm.process_handle:#x}")
    base = pm.process_base.lpBaseOfDll
    size = pm.process_base.SizeOfImage
    print(f"  module @ {base:#x}  size={size:#x} ({size/1024/1024:.1f} MiB)")

    # bitness sniff: IsWow64Process lives in kernel32 (not user32 — earlier
    # bug). A WoW64 process on x64 Windows is a 32-bit process, which is what
    # we expect for Terraria's CLR host.
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
    k32.IsWow64Process.restype = wt.BOOL
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.OpenProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL

    is_wow64 = ctypes.c_int(0)
    try:
        if k32.IsWow64Process(pm.process_handle, ctypes.byref(is_wow64)):
            print(f"  WoW64 (32-bit process on 64-bit OS): {bool(is_wow64.value)}")
        else:
            print(f"  IsWow64Process returned False (err={ctypes.get_last_error()})")
    except AttributeError:
        print("  IsWow64Process unavailable on this OS — assuming 32-bit process")

    # Re-open with explicit PROCESS_VM_READ so subsequent ReadProcessMemory
    # calls don't fail on missing access rights. (pymem's default already
    # requests PROCESS_ALL_ACCESS, but explicitly opening is more robust and
    # isolates handle permission as a hypothesis.)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010
    desired = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    expl_handle = k32.OpenProcess(desired, False, pm.process_id)
    if not expl_handle:
        print(f"  WARN: explicit OpenProcess(PROCESS_VM_READ) failed: "
              f"err={ctypes.get_last_error()}")
        expl_handle = pm.process_handle
    else:
        print(f"  explicit VM-READ handle opened: {expl_handle:#x}")

    handle = expl_handle
    print("  scanning executable pages for FishingCheck pattern ...")
    t0 = time.monotonic()
    match = memory_bot.pattern_scan(handle, memory_bot.FISHING_CHECK_PATTERN)
    t1 = time.monotonic()
    print(f"    executable scan took {t1 - t0:.2f}s, match={match}")

    if match is None:
        print("  executable miss — scanning JIT heap (RW/RX, 0x10000000-0x50000000) ...")
        t0 = time.monotonic()
        match = memory_bot.pattern_scan(
            handle, memory_bot.FISHING_CHECK_PATTERN,
            base=memory_bot.JIT_HEAP_START, end=memory_bot.JIT_HEAP_END,
            protect_set=memory_bot.READABLE_PROTECT,
        )
        t1 = time.monotonic()
        print(f"    heap scan took {t1 - t0:.2f}s, match={match}")

    if match is None:
        print("  heap miss — scanning all readable 32-bit pages ...")
        t0 = time.monotonic()
        match = memory_bot.pattern_scan(
            handle, memory_bot.FISHING_CHECK_PATTERN,
            protect_set=memory_bot.READABLE_PROTECT,
        )
        t1 = time.monotonic()
        print(f"    full readable scan took {t1 - t0:.2f}s, match={match}")

    if match is None:
        print("  FAIL: FishingCheck pattern NOT found.")
        print("  Hypothesis M1 REJECTED — pattern must be adjusted for 1.4.5.6.")
        if expl_handle != pm.process_handle:
            k32.CloseHandle(expl_handle)
        pm.close_process()
        return 2

    # Dump 16 bytes around match+11 to verify the static-address bytes land
    # inside the immediate of `mov esi, ds:[imm32]` (offset 11..14 inclusive).
    print(f"  raw bytes at match+8 .. match+20 (hex):")
    dump_buf = (ctypes.c_ubyte * 16)()
    n = ctypes.c_size_t(0)  # initialised up here so the dump and all
                            # subsequent ReadProcessMemory calls share it
    if memory_bot.ReadProcessMemory(
        handle, ctypes.c_void_p(match + 8), dump_buf, 16, ctypes.byref(n)
    ) and n.value == 16:
        print("    " + " ".join(f"{b:02x}" for b in dump_buf))
    else:
        print(f"    FAIL reading 16-byte dump @ match+8")

    static_addr = match + 10
    buf = (ctypes.c_ubyte * 4)()
    if not memory_bot.ReadProcessMemory(
        handle, ctypes.c_void_p(static_addr), buf, 4, ctypes.byref(n)
    ):
        print(f"  FAIL: could not read _context static immediate @ {static_addr:#x} "
              f"(err={ctypes.get_last_error()})")
        if expl_handle != pm.process_handle:
            k32.CloseHandle(expl_handle)
        pm.close_process()
        return 3
    ctx_static = int.from_bytes(buf, "little")
    print(f"  match @ {match:#x}  → _context static field addr (immediate) = {ctx_static:#x}")

    # The immediate in `mov esi, ds:[imm32]` IS the static field's address —
    # in WoW64 the .NET CLR writes managed-type static fields into the low
    # 4 GiB of the process address space, so 32-bit reads must work here.
    # Single indirection gives the GC pointer to the FishingContext object.
    ptr_buf = (ctypes.c_ubyte * 4)()
    if not memory_bot.ReadProcessMemory(
        handle, ctypes.c_void_p(ctx_static), ptr_buf, 4, ctypes.byref(n)
    ):
        # Dump 32 bytes around ctx_static so we can inspect nearby slots —
        # the address might be one of: a "header", a method-table pointer,
        # or a real pointer with garbage around it.
        print(f"  WARN: deref @ {ctx_static:#x} failed (err={ctypes.get_last_error()}).")
        print(f"  dumping +-16 bytes around {ctx_static:#x}:")
        window_buf = (ctypes.c_ubyte * 64)()
        for off_base in range(-16, 17, 16):
            target = ctx_static + off_base
            if target < 0:
                continue
            if memory_bot.ReadProcessMemory(
                handle, ctypes.c_void_p(target), window_buf, 32,
                ctypes.byref(n)
            ) and n.value == 32:
                print(f"    +{off_base:+d} @ {target:#x}: "
                      + " ".join(f"{b:02x}" for b in window_buf[:32]))
            else:
                print(f"    +{off_base:+d} @ {target:#x}: <unreadable>")
        ctx_obj = 0
    else:
        ctx_obj = int.from_bytes(ptr_buf, "little")
    print(f"  FishingContext obj @ {ctx_obj:#x}")
    if ctx_obj == 0:
        print("  FishingContext is null. Cast a line first, then re-run probe.")
        if expl_handle != pm.process_handle:
            k32.CloseHandle(expl_handle)
        pm.close_process()
        return 0

    rolled_addr = ctx_obj + 0x68
    print(f"  polling rolledItemDrop @ {rolled_addr:#x} for ~10s...")
    print("  (cast a line and wait for a bite to see the value change)")
    end = time.monotonic() + 10.0
    last = 0
    while time.monotonic() < end:
        vbuf = (ctypes.c_ubyte * 4)()
        if memory_bot.ReadProcessMemory(
            handle, ctypes.c_void_p(rolled_addr), vbuf, 4, ctypes.byref(n)
        ) and n.value == 4:
            val = int.from_bytes(vbuf, "little")
            if val != last:
                print(f"    +{time.monotonic() - (end - 10.0):5.2f}s  "
                      f"rolledItemDrop = {val}")
                last = val
        time.sleep(0.1)
    print("  done.")
    if expl_handle != pm.process_handle:
        k32.CloseHandle(expl_handle)
    pm.close_process()
    return 0


if __name__ == "__main__":
    sys.exit(main())
