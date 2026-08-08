"""Live hardware check — run this on the customer LAN before go-live.

Confirms empirically what PLAN.md §1.1 asserts from the protocol layout: that
``status`` is the verification mode and ``punch`` is the attendance state. eSSL ships
many firmware variants, and the reference spike had these two swapped, so the mapping
must be verified against the actual terminals rather than trusted.

It also measures the full-buffer read that every poll pays for (PLAN.md §1.2), which
is the number that decides whether a 5-minute interval is comfortable.

This is **read-only**. It never writes to a device.

Usage, from ``backend/``::

    ../.venv/Scripts/python.exe tests/manual/live_device_check.py
    ../.venv/Scripts/python.exe tests/manual/live_device_check.py --ip 172.20.200.26

Nothing here runs in the normal test suite — it needs hardware.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.biometric.essl_client import EsslBiometricDevice
from app.biometric.mapping import (
    PUNCH_TYPE_BY_PUNCH,
    VERIFICATION_BY_STATUS,
)

DEFAULT_DEVICES = [
    ("172.20.200.26", 4370, "Entry (expected CHECK_IN)"),
    ("172.20.200.25", 4370, "Exit (expected CHECK_OUT)"),
]


def check(ip: str, port: int, label: str, hours: int) -> None:
    print(f"\n{'=' * 78}\n{label} — {ip}:{port}\n{'=' * 78}")
    device = EsslBiometricDevice(
        ip_address=ip, port=port, timeout_seconds=15, label=label
    )

    result = device.test_connection()
    print(f"reachable : {result.reachable}  ({result.message})")
    if not result.reachable:
        return

    info = result.info
    if info:
        print(f"serial    : {info.serial_number}")
        print(f"firmware  : {info.firmware_version}   platform: {info.platform}")
        print(f"users     : {info.user_count}     buffered records: {info.record_count}")
        print(f"devicetime: {info.device_time}   bridge time: {datetime.now()}")

    offset = result.clock_offset_seconds
    if offset is not None:
        flag = "  <-- DRIFT, fix the device clock" if abs(offset) > 120 else ""
        print(f"clock skew: {offset:+.0f}s{flag}")

    to_time = info.device_time if info and info.device_time else datetime.now()
    from_time = to_time - timedelta(hours=hours)
    print(f"\nreading   : {from_time}  ->  {to_time}")

    read = device.get_attendance_logs(from_time, to_time)
    print(
        f"buffer    : {read.buffer_records} records transferred in "
        f"{read.read_duration_ms} ms   <-- cost of every poll"
    )
    print(f"in window : {read.window_records}")

    if not read.punches:
        print("\nNo records in the window. Punch on the device and re-run to verify "
              "the field mapping.")
        return

    pairs = Counter((p.status, p.punch) for p in read.punches)
    print("\nobserved (status, punch) pairs — THIS IS THE MAPPING TO CONFIRM:")
    for (status, punch), count in sorted(pairs.items()):
        verification = VERIFICATION_BY_STATUS.get(status)
        punch_type = PUNCH_TYPE_BY_PUNCH.get(punch)
        warn = "" if verification and punch_type else "   <-- UNRECOGNISED"
        print(
            f"  status={status:<3} -> {verification.value if verification else '?':<12}"
            f"  punch={punch:<3} -> {punch_type.value if punch_type else '?':<10}"
            f"  x{count}{warn}"
        )

    print("\nsample records:")
    for punch in read.punches[-10:]:
        print(
            f"  {punch.timestamp}  {punch.employee_code:<16} "
            f"status={punch.status} punch={punch.punch}"
        )

    print(
        "\nCHECK: on an entry-only terminal every punch should show the same `punch` "
        "value,\n and `status` should vary with how people identified "
        "(finger/card/password)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", help="probe a single device instead of the defaults")
    parser.add_argument("--port", type=int, default=4370)
    parser.add_argument("--hours", type=int, default=8, help="how far back to read")
    args = parser.parse_args()

    targets = (
        [(args.ip, args.port, "Device")] if args.ip else DEFAULT_DEVICES
    )
    for ip, port, label in targets:
        try:
            check(ip, port, label, args.hours)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
