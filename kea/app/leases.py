"""Read (and delete) leases in Kea's memfile CSV lease files.

Kea appends a row on every lease change, so the *last* row per address wins,
and a row with ``valid_lifetime`` 0 means the lease was deleted. Columns are
looked up by header name, which keeps this tolerant of the column differences
between Kea versions.
"""
import csv
import ipaddress
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
        valid = _to_int(row.get("valid_lifetime"))
        state = (row.get("state") or "0").strip()
        if expire is None or expire <= now or state not in ("0", "") or valid == 0:
            continue
        leases.append({
            "address": address,
            "identifier": (row.get("hwaddr") or row.get("duid") or "").strip(),
            "hostname": (row.get("hostname") or "").strip(),
            "subnet_id": _to_int(row.get("subnet_id")),
            "valid_lifetime": valid,
            "expire": expire,
            "remaining": expire - int(now),
        })
    leases.sort(key=lambda lease: _sort_key(lease["address"]))
    return leases


def find_latest_row(path, address):
    """Return ``(fieldnames, row)`` for the newest row of *address*, or ``(None, None)``."""
    if not os.path.isfile(path):
        return None, None
    fieldnames, found = None, None
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames
            for row in reader:
                if (row.get("address") or "").strip() == address:
                    found = row
    except OSError:
        return None, None
    return (fieldnames, found) if found else (None, None)


def append_deleted_row(path, fieldnames, row, now=None):
    """Append a "deleted" row (lifetime 0) for the lease described by *row*.

    Only call this while the owning Kea daemon is stopped: Kea has the file
    open and writes to it itself.
    """
    now = int(time.time() if now is None else now)
    out = {name: row.get(name, "") for name in fieldnames}
    out["valid_lifetime"] = "0"
    out["expire"] = str(now)
    if "state" in out:
        out["state"] = "0"
    needs_newline = False
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell():
            fh.seek(-1, os.SEEK_END)
            needs_newline = fh.read(1) != b"\n"
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")
        csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n").writerow(out)


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sort_key(address):
    try:
        ip = ipaddress.ip_address(address)
        return (ip.version, int(ip))
    except ValueError:
        return (9, 0)
