"""Check that an address really is a usable NTP server, and pick good servers from host names.

A DNS answer does not make an address an NTP server: volunteer pool servers come and go, some answer "stop asking me",
some are not synchronised, a few have a wrong clock. :func:`query` sends one SNTP request (RFC 5905) and judges the
answer; :func:`check_servers` resolves host names, asks every address in parallel and drops the ones that are not
usable or that disagree with the rest. Standard library only.
"""
import concurrent.futures
import ipaddress
import os
import socket
import statistics
import struct
import time

NTP_PORT = 123
NTP_EPOCH = 2208988800                  # seconds from 1900-01-01 (NTP) to 1970-01-01 (Unix)
DISAGREEMENT_SECONDS = 1.0              # a server this far from the median of the others is a falseticker
CLOCK_WARNING_SECONDS = 1.0             # report when the whole set disagrees with this machine's clock


def unusable_address(address):
    """Why *address* cannot be handed to clients as an NTP server, or None if it is fine."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "not an IP address"
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return "not a usable unicast address"
    if ip.version == 4 and str(ip) == "255.255.255.255":
        return "not a usable unicast address"
    return None


def _ntp_to_float(raw):
    seconds, fraction = struct.unpack("!II", raw)
    return seconds - NTP_EPOCH + fraction / 2 ** 32


def query(address, port=NTP_PORT, timeout=2.0, retries=1, clock=time.time):
    """Ask one server. Returns ``{address, ok, reason, stratum, offset, rtt}`` (offset and rtt in seconds)."""
    result = {"address": address, "ok": False, "reason": "", "stratum": None, "offset": None, "rtt": None}
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    nonce = os.urandom(8)                                # echoed back as the "originate" field: proves it is our reply
    request = b"\x23" + b"\x00" * 39 + nonce             # LI 0, version 4, mode 3 (client)
    reason = "no answer"
    for _ in range(retries + 1):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.connect((address, port))            # the kernel then only delivers packets from that server
                sent = clock()
                sock.send(request)
                data = sock.recv(1024)
                received = clock()
        except socket.timeout:
            reason = "no answer"
            continue
        except OSError as exc:
            result["reason"] = f"network error: {exc.strerror or exc}"
            return result
        return _judge(result, data, nonce, sent, received)
    result["reason"] = reason
    return result


def _judge(result, data, nonce, sent, received):
    if len(data) < 48:
        result["reason"] = "reply too short"
        return result
    flags, stratum = data[0], data[1]
    leap, version, mode = flags >> 6, (flags >> 3) & 7, flags & 7
    result["stratum"] = stratum
    if mode != 4 or version < 3:
        result["reason"] = "not an NTP server reply"
    elif data[24:32] != nonce:
        result["reason"] = "reply does not match the request"
    elif stratum == 0:
        code = data[12:16].rstrip(b"\x00").decode("ascii", "replace")
        result["reason"] = f"refused the request (kiss-o'-death {code or '?'})"
    elif stratum > 15 or leap == 3:
        result["reason"] = "server clock is not synchronised"
    elif data[40:48] == b"\x00" * 8:
        result["reason"] = "reply has no time"
    else:
        t2, t3 = _ntp_to_float(data[32:40]), _ntp_to_float(data[40:48])
        result.update(ok=True, reason="ok", offset=((t2 - sent) + (t3 - received)) / 2,
                      rtt=max(0.0, (received - sent) - (t3 - t2)))
    return result


def resolve(name, families=(socket.AF_INET,), resolver=socket.getaddrinfo):
    """All addresses of *name* in the wanted families, without duplicates. Returns ``(addresses, error)``."""
    found, error = [], ""
    for family in families:
        try:
            for info in resolver(name, None, family, socket.SOCK_DGRAM):
                address = info[4][0]
                if address not in found:
                    found.append(address)
        except socket.gaierror as exc:
            error = f"cannot be resolved ({exc.strerror or exc})"
        except OSError as exc:
            error = f"cannot be resolved ({exc})"
    return found, ("" if found else error or "has no address")


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def check_servers(entries, families=(socket.AF_INET,), probe=query, resolver=socket.getaddrinfo, max_workers=16):
    """Resolve host names, query every address and mark the unusable ones.

    ``entries`` are IP addresses or host names. Returns::

        {"servers": [{"server": entry, "kind": "address"|"hostname", "error": "", "addresses": [result, ...]}],
         "clock_offset": median offset of the good servers in seconds, or None}

    Each result is what :func:`query` returns; a result judged a falseticker is turned into ``ok: False``.
    """
    servers, jobs, seen = [], [], set()
    entries = [e for e in (str(entry).strip() for entry in entries) if e]
    # Look the names up side by side: with a DNS server that does not answer, one lookup after the other would make
    # "Save & apply" wait for the sum of all the timeouts.
    names = list(dict.fromkeys(e for e in entries if not _is_ip(e)))
    resolved = {}
    if names:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(names))) as pool:
            resolved = dict(zip(names, pool.map(lambda name: resolve(name, families, resolver), names)))
    for entry in entries:
        item = {"server": entry, "kind": "address" if _is_ip(entry) else "hostname", "error": "", "addresses": []}
        if item["kind"] == "address":
            addresses = [str(ipaddress.ip_address(entry))]
        else:
            addresses, item["error"] = resolved[entry]
        servers.append(item)
        for address in addresses:
            problem = unusable_address(address)
            if problem:
                item["addresses"].append({"address": address, "ok": False, "reason": problem,
                                          "stratum": None, "offset": None, "rtt": None})
            elif address in seen:
                item["addresses"].append({"address": address, "ok": False, "reason": "already listed above",
                                          "stratum": None, "offset": None, "rtt": None, "duplicate": True})
            else:
                seen.add(address)
                jobs.append((item, address))

    if jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
            futures = [(item, pool.submit(probe, address)) for item, address in jobs]
            for item, future in futures:
                item["addresses"].append(future.result())

    good = [r for s in servers for r in s["addresses"] if r["ok"]]
    clock_offset = None
    if len(good) >= 3:
        median = statistics.median(r["offset"] for r in good)
        for r in good:
            if abs(r["offset"] - median) > DISAGREEMENT_SECONDS:
                r["ok"] = False
                r["reason"] = f"disagrees with the other servers by {r['offset'] - median:+.1f} s"
        clock_offset = median
    elif good:
        clock_offset = statistics.median(r["offset"] for r in good)
    return {"servers": servers, "clock_offset": clock_offset}


def usable_addresses(report, limit=None):
    """Addresses that passed, best first (lowest stratum, then fastest), at most *limit*."""
    good = [r for s in report["servers"] for r in s["addresses"] if r["ok"]]
    good.sort(key=lambda r: (r["stratum"], r["rtt"]))
    return [r["address"] for r in (good[:limit] if limit else good)]


def clock_warning(report):
    """A sentence when the good servers all disagree with this machine's clock, else ''."""
    offset = report.get("clock_offset")
    if offset is not None and abs(offset) > CLOCK_WARNING_SECONDS:
        return (f"This machine's clock differs from the NTP servers by {offset:+.1f} s. "
                "Fix the clock first (the check compares against it).")
    return ""


# --------------------------------------------------------------------------
# Host names kept in the settings, resolved by the app (what ISC dhcpd did per request)
# --------------------------------------------------------------------------
REFRESH_SECONDS = 3600                  # how long resolved addresses are trusted (dhcpd used the same one hour)
PER_NAME = 4                            # addresses kept per host name (a pool name answers with four)


def refresh(names, previous=None, per_name=PER_NAME, now=time.time, probe=query, resolver=socket.getaddrinfo):
    """Resolve and check every host name; returns ``(state, report)``.

    ``state`` maps each name to ``{"addresses": [...], "checked": time, "error": str, "stale": bool}``. An address that
    was good last time and still passes is kept in preference to a new one, so a pool name (a new set of volunteers
    every couple of minutes) does not make the DHCP server reload its configuration every hour. When nothing passes
    the previous addresses stay (``stale``): a DNS outage must not remove the NTP servers.
    """
    previous = previous or {}
    names = [n for n in dict.fromkeys(names) if n]
    remembered = [a for n in names for a in previous.get(n, {}).get("addresses", [])]
    report = check_servers(names + [a for a in dict.fromkeys(remembered) if a not in names],
                           probe=probe, resolver=resolver)
    results = {r["address"]: r for s in report["servers"] for r in s["addresses"] if not r.get("duplicate")}
    ok = {a for a, r in results.items() if r["ok"]}
    by_server = {s["server"]: s for s in report["servers"]}

    state, taken = {}, set()
    for name in names:
        old = [a for a in previous.get(name, {}).get("addresses", []) if a in ok and a not in taken]
        fresh = sorted((r for r in by_server[name]["addresses"] if r["ok"] and r["address"] not in old),
                       key=lambda r: (r["stratum"], r["rtt"]))
        chosen = (old + [r["address"] for r in fresh])[:per_name]
        taken.update(chosen)
        entry = {"addresses": chosen, "checked": now(), "error": "", "stale": False}
        if not chosen:
            why = by_server[name]["error"] or "; ".join(
                sorted({r["reason"] for r in by_server[name]["addresses"]})) or "no address passed the check"
            entry.update(addresses=list(previous.get(name, {}).get("addresses", [])), error=why,
                         stale=bool(previous.get(name, {}).get("addresses")))
        state[name] = entry
    return state, report
