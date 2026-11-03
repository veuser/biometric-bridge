"""
eSSL Biometric Device — ZK protocol client
Two devices:
  172.20.200.26 → Check-in
  172.20.200.25 → Check-out

Install:
    pip install pyzk

Usage:
    python test_biometric.py
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Generator

try:
    from zk import ZK
except ImportError:
    print("pyzk not installed. Run:  pip install pyzk")
    sys.exit(1)


# ── Device registry ───────────────────────────────────────────────────────────

@dataclass
class DeviceConfig:
    ip:    str
    port:  int
    role:  str          # "Check In" | "Check Out"
    label: str

DEVICES = [
    DeviceConfig(ip="172.20.200.26", port=4370, role="Check In",  label="Entry"),
    DeviceConfig(ip="172.20.200.25", port=4370, role="Check Out", label="Exit"),
]

TIMEOUT = 10


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AttendanceRecord:
    user_id:    str
    name:       str
    timestamp:  datetime
    event:      str          # "Check In" / "Check Out"
    method:     str          # "Fingerprint" / "Password" / "Card"
    device_ip:  str
    device_role: str


VERIFY_TYPE = {
    0: "Fingerprint",
    1: "Password",
    2: "Card",
}


# ── Device helpers ────────────────────────────────────────────────────────────

@contextmanager
def device_connection(device: DeviceConfig) -> Generator:
    """Open a ZK connection to a device and close it cleanly on exit."""
    zk   = ZK(device.ip, port=device.port, timeout=TIMEOUT, password=0, force_udp=False)
    conn = None
    try:
        conn = zk.connect()
        yield conn
    finally:
        if conn:
            conn.disconnect()


def _fetch_from_device(device: DeviceConfig, dt_from: datetime, dt_to: datetime) -> list[AttendanceRecord]:
    """Pull and filter records from a single device."""
    records = []
    try:
        with device_connection(device) as conn:
            users    = conn.get_users()
            name_map = {u.user_id: u.name for u in users}
            raw      = conn.get_attendance()

        for a in raw:
            if not (dt_from <= a.timestamp <= dt_to):
                continue
            records.append(AttendanceRecord(
                user_id     = a.user_id,
                name        = name_map.get(a.user_id, ""),
                timestamp   = a.timestamp,
                event       = device.role,                              # role drives event type
                method      = VERIFY_TYPE.get(a.punch, f"punch={a.punch}"),
                device_ip   = device.ip,
                device_role = device.role,
            ))
    except Exception as e:
        print(f"  [WARN] Could not fetch from {device.label} ({device.ip}): {e}")

    return records


# ── Core function (call this from anywhere) ───────────────────────────────────

def fetch_attendance(from_date: date, to_date: date) -> list[AttendanceRecord]:
    """
    Pull attendance logs from all devices for the given date range.
    Returns a unified list sorted by timestamp ascending.
    """
    dt_from = datetime(from_date.year, from_date.month, from_date.day, 22,  0,  0)
    dt_to   = datetime(to_date.year,   to_date.month,   to_date.day,   23, 59, 59)

    all_records: list[AttendanceRecord] = []

    for device in DEVICES:
        print(f"  Fetching from {device.label} ({device.ip}) ...")
        records = _fetch_from_device(device, dt_from, dt_to)
        print(f"  → {len(records)} record(s)")
        all_records.extend(records)

    all_records.sort(key=lambda r: r.timestamp)
    return all_records


# ── Entry point (hardcoded dates for now) ─────────────────────────────────────

def main() -> None:
    from_date = date.today()   # ← swap these when wiring elsewhere
    to_date   = date.today()

    print(f"Fetching attendance: {from_date}  →  {to_date}\n")
    records = fetch_attendance(from_date, to_date)

    if not records:
        print("\nNo records found.")
        return

    print(f"\n{'Timestamp':<22} {'User ID':<22} {'Name':<25} {'Event':<12} {'Via':<14} Device")
    print("-" * 105)
    for r in records:
        print(f"  {r.timestamp}  {r.user_id:<20}  {r.name:<25}  {r.event:<12}  {r.method:<14}  {r.device_role}")

    print(f"\nTotal: {len(records)} record(s)")


if __name__ == "__main__":
    main()
