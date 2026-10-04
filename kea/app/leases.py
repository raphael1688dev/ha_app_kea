"""Read (and delete) leases in Kea's memfile CSV lease files.

Kea appends a row on every lease change, so the *last* row per address wins,
and a row with ``valid_lifetime`` 0 means the lease was deleted. Columns are
looked up by header name, which keeps this tolerant of the column differences
between Kea versions.

The lease state is not always in one file. Kea's lease file cleanup (LFC, every
``lfc-interval`` seconds) moves the current file to ``<file>.1`` and starts a new
one, so after the first cleanup the main file only holds changes since then.
:func:`lease_file_chain` lists the files in the order Kea itself loads them
(memfile_lease_mgr.cc, ``loadLeasesFromFiles``).
"""
import ipaddress
import os
import time

# DHCPv6 lease_type column: 0 = address (IA_NA), 1 = temporary address (IA_TA), 2 = delegated prefix (IA_PD)
_V6_KINDS = {"0": "address", "1": "temporary", "2": "prefix"}


def lease_file_chain(path):
    """Existing files that together hold the lease state, oldest first.

    Kea loads ``<file>.completed`` if it exists (a finished cleanup), otherwise ``<file>.2`` then
    ``<file>.1`` (previous cleanup result, then the file moved aside when this cleanup started), and
    always the main file last.
    """
    completed = path + ".completed"
    older = [completed] if os.path.isfile(completed) else [path + ".2", path + ".1"]
    return [p for p in older + [path] if os.path.isfile(p)]


def _split(line):
    # Kea does not quote CSV fields (it escapes commas inside a field as "&#x2c"), so a plain split is exact.
    return line.rstrip("\r\n").split(",")


def _rows(path):
    """Yield ``(fieldnames, row_dict)`` for every data row of one file; unreadable files yield nothing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            header = fh.readline()
            if not header.strip():
                return
            fieldnames = _split(header)
            for line in fh:
                if not line.strip():
                    continue
                values = _split(line)
                yield fieldnames, {name: (values[i] if i < len(values) else "")
                                   for i, name in enumerate(fieldnames)}
    except OSError:
        return


def read_leases(path, now=None):
    """Return a list of currently active leases (newest row per address)."""
    now = time.time() if now is None else now

    latest = {}
    for file in lease_file_chain(path):
        for _fieldnames, row in _rows(file):
            address = (row.get("address") or "").strip()
            if address:
                latest[address] = row

    leases = []
    for address, row in latest.items():
        expire = _to_int(row.get("expire"))
        valid = _to_int(row.get("valid_lifetime"))
        state = (row.get("state") or "0").strip()
        if expire is None or expire <= now or state not in ("0", "") or valid == 0:
            continue
        is_v6 = "duid" in row                       # only the DHCPv6 file has a duid column
        kind = _V6_KINDS.get((row.get("lease_type") or "0").strip(), "address") if is_v6 else "address"
        identifier = (row.get("duid") if is_v6 else row.get("hwaddr")) or ""
        leases.append({
            "address": address,
            "kind": kind,
            "prefix_len": _to_int(row.get("prefix_len")) if kind == "prefix" else None,
            "identifier": identifier.strip(),
            "hostname": (row.get("hostname") or "").strip(),
            "subnet_id": _to_int(row.get("subnet_id")),
            "valid_lifetime": valid,
            "expire": expire,
            "remaining": expire - int(now),
        })
    leases.sort(key=lambda lease: _sort_key(lease["address"]))
    return leases


def find_latest_row(path, address):
    """Return ``(fieldnames, row)`` for the newest row of *address*, or ``(None, None)``.

    ``fieldnames`` are the columns of the main file (where a new row will be appended) when it exists.
    """
    found, found_fields, main_fields = None, None, None
    for file in lease_file_chain(path):
        for fieldnames, row in _rows(file):
            if file == path:
                main_fields = fieldnames
            if (row.get("address") or "").strip() == address:
                found, found_fields = row, fieldnames
    if found is None:
        return None, None
    return (main_fields or found_fields), found


def append_deleted_row(path, fieldnames, row, now=None):
    """Append a "deleted" row (lifetime 0) for the lease described by *row* to the main lease file.

    Only call this while the owning Kea daemon is stopped: Kea has the file open and writes to it itself.
    The row is written the way Kea writes rows: no CSV quoting, commas inside a field escaped as "&#x2c".
    """
    now = int(time.time() if now is None else now)
    out = {name: row.get(name, "") for name in fieldnames}
    out["valid_lifetime"] = "0"
    out["expire"] = str(now)
    if "state" in out:
        out["state"] = "0"
    line = ",".join(str(out[name]).replace(",", "&#x2c").replace("\r", "").replace("\n", "") for name in fieldnames)

    needs_newline = False
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell():
                fh.seek(-1, os.SEEK_END)
                needs_newline = fh.read(1) != b"\n"
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if needs_newline:
            fh.write("\n")
        fh.write(line + "\n")


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
