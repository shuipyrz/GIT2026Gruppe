"""Bounded runtime evidence without chat text, attributes, or coordinates."""
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from threading import RLock
from time import perf_counter


def process_rss_bytes():
    """Current process working set on Windows; unavailable elsewhere."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]

        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize
    except (OSError, AttributeError):
        pass
    return None


class RuntimeDiagnostics:
    """Keep lifetime counters and only the latest 200 structural events."""
    def __init__(self):
        self._lock = RLock()
        self._events = deque(maxlen=200)
        self._counts = Counter()

    def record(self, event, **fields):
        with self._lock:
            self._counts[event] += 1
            entry = {"event": event, "sequence": sum(self._counts.values()),
                     "time_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields}
            self._events.append(entry)

    @contextmanager
    def measure(self, event, **fields):
        started = perf_counter()
        try:
            yield
        finally:
            self.record(event, duration_ms=round((perf_counter() - started) * 1000, 2), **fields)

    def snapshot(self):
        with self._lock:
            return {"process_id": os.getpid(), "process_rss_bytes": process_rss_bytes(),
                    "counts": dict(self._counts), "events": [dict(event) for event in self._events]}

    def export(self, *, session_stats, versions):
        return json.dumps({"schema_version": 1, "runtime": self.snapshot(),
                           "session": session_stats, "versions": versions},
                          ensure_ascii=False, indent=2).encode("utf-8")
