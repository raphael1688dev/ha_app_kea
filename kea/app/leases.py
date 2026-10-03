"""Read active leases from Kea's memfile CSV lease files.

Kea appends a row on every lease change, so the *last* row per address wins.
Columns are looked up by header name, which keeps this tolerant of the
column differences between Kea versions.
"""
import csv
import os
import time


def read_leases(path, now=None):
    """Return a list of currently active leases (newest row per address)."""
    if not os.path.isfile(path):
        return []
    now = time.time() if now is None else now

    latest = {}
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                address = (row.get("address") or "").strip()
                if address:
                    latest[address] = row
    except OSError:
        return []

    leases = []
    for address, row in latest.items():
        expire = _to_int(row.get("expire"))
        state = (row.get("state") or "0").strip()
        if expire is None or expire <= now or state not in ("0", ""):
            continue
        leases.append({
            "address": address,
            "identifier": (row.get("hwaddr") or row.get("duid") or "").strip(),
            "hostname": (row.get("hostname") or "").strip(),
            "subnet_id": _to_int(row.get("subnet_id")),
            "valid_lifetime": _to_int(row.get("valid_lifetime")),
            "expire": expire,
            "remaining": expire - int(now),
        })
    leases.sort(key=lambda lease: _sort_key(lease["address"]))
    return leases


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sort_key(address):
    import ipaddress
    try:
        ip = ipaddress.ip_address(address)
        return (ip.version, int(ip))
    except ValueError:
        return (9, 0)
