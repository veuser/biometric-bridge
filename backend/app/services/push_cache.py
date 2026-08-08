"""Suppression cache for punches already pushed this process lifetime.

The overlap window means the boundary of the previous window is re-read on every
cycle. Without suppression, an idle device re-pushes its last punch roughly 150
times overnight at a 5-minute interval, and the "no records" path almost never
fires.

Deliberately **in memory only** — persisting punch identifiers would be storing
biometric-derived data, which RULE 1 forbids. The consequence is that a restart
empties the cache and the overlap window is re-sent once more, so this reduces but
does not remove the dependence on HRMS idempotency (PLAN.md §5.5).

Bounded so a busy device cannot grow it without limit.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

DEFAULT_CAPACITY_PER_DEVICE = 5000


class PushedRecordCache:
    """Per-device bounded LRU of ``client_record_id`` values already accepted."""

    def __init__(self, capacity_per_device: int = DEFAULT_CAPACITY_PER_DEVICE) -> None:
        self._capacity = capacity_per_device
        self._by_device: dict[int, OrderedDict[str, None]] = {}
        self._guard = threading.Lock()

    def _bucket(self, device_id: int) -> OrderedDict[str, None]:
        return self._by_device.setdefault(device_id, OrderedDict())

    def contains(self, device_id: int, record_id: str) -> bool:
        with self._guard:
            bucket = self._bucket(device_id)
            if record_id in bucket:
                bucket.move_to_end(record_id)
                return True
            return False

    def add_many(self, device_id: int, record_ids: list[str]) -> None:
        """Record ids as pushed. Called only after HRMS has accepted them."""
        with self._guard:
            bucket = self._bucket(device_id)
            for record_id in record_ids:
                bucket[record_id] = None
                bucket.move_to_end(record_id)
            while len(bucket) > self._capacity:
                bucket.popitem(last=False)

    def filter_unsent(self, device_id: int, record_ids: list[str]) -> list[str]:
        return [rid for rid in record_ids if not self.contains(device_id, rid)]

    def forget_device(self, device_id: int) -> None:
        """Called when a device is deleted or reconfigured."""
        with self._guard:
            self._by_device.pop(device_id, None)

    def size(self, device_id: int) -> int:
        with self._guard:
            return len(self._bucket(device_id))

    def clear(self) -> None:
        with self._guard:
            self._by_device.clear()
