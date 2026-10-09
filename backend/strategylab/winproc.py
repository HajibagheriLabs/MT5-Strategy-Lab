"""Stopping terminal processes: politely first, by force only when that fails.

A terminal asked to close through its window flushes its history databases and logs ("shutdown
with 0" in its journal). Killing it outright can leave those files half written, so force is the
last resort after a grace period.
"""

from __future__ import annotations

import sys
import time
from typing import Literal

import psutil

StopOutcome = Literal["gone", "closed", "killed"]

WM_CLOSE = 0x0010
GW_OWNER = 4


def _top_level_windows(pid: int) -> list[int]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND

    found: list[int] = []

    def visit(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        # The main window is the visible one with no owner; dialogs are owned by it.
        if (
            owner.value == pid
            and user32.IsWindowVisible(hwnd)
            and not user32.GetWindow(hwnd, GW_OWNER)
        ):
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return found


def request_close(pid: int) -> bool:
    """Ask a process to close its main window. False if it has no window to ask."""
    windows = _top_level_windows(pid)
    if not windows:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return any(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0) for hwnd in windows)


def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        processes = [*root.children(recursive=True), root]
    except psutil.NoSuchProcess:
        return
    for process in processes:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    psutil.wait_procs(processes, timeout=10)


def stop_process(pid: int, grace_seconds: float = 30.0) -> StopOutcome:
    """Close a process window and wait for it; kill the process tree if it does not exit."""
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return "gone"
    deadline = time.monotonic() + grace_seconds
    # A terminal that has just been started may not have created its window yet.
    while time.monotonic() < deadline:
        if request_close(pid):
            break
        if not process.is_running():
            return "gone"
        time.sleep(0.5)
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return "closed"
    except psutil.TimeoutExpired:
        kill_tree(pid)
        return "killed"
    except psutil.NoSuchProcess:
        return "closed"
