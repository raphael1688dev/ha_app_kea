"""Convert the options of the old "DHCP server" app (ISC dhcpd, home-assistant/addons) into settings for this app.

The old app is configured with ``domain``, ``dns``, ``ntp``, ``default_lease``, ``max_lease``, ``networks`` and
``hosts``. :func:`convert` maps them onto our settings and returns a report that says what was carried over, what was
changed and what was left out, so nothing is lost silently. Nothing is applied here: the result goes into the Web UI
editor for review.
"""
import ipaddress
import re

import yaml

import settings as cfg

KNOWN_OPTIONS = {"domain", "dns", "ntp", "default_lease", "max_lease", "networks", "hosts"}
KNOWN_NETWORK_KEYS = {"subnet", "netmask", "range_start", "range_end", "broadcast", "gateway", "interface"}
MAX_REPORT_LINES = 300


class _Loader(yaml.SafeLoader):
    """SafeLoader that reads plain scalars the way Home Assistant writes them (YAML 1.2), not the YAML 1.1 way.

    PyYAML follows YAML 1.1, where ``10:20:30:40:50:11`` is a base-60 number, ``yes`` / ``on`` are booleans, ``0123`` is
    octal and ``2024-01-15`` a date. Home Assistant's editor writes a MAC address or a device name like that without
    quotes, so they must stay text. Only true/false, null and plain whole numbers are converted.
    """


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers
            if tag not in ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float", "tag:yaml.org,2002:bool",
                           "tag:yaml.org,2002:timestamp")]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
_Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"), list("-+0123456789"))


def parse_text(text):
    """YAML (or JSON, which is YAML) to a Python value. Aliases are refused: a small document can expand to gigabytes."""
    try:
        for event in yaml.parse(text, Loader=_Loader):
            if isinstance(event, yaml.AliasEvent):
                raise ValueError("YAML aliases (&anchor / *alias) are not supported")
        return yaml.load(text, Loader=_Loader)                # a SafeLoader subclass: no arbitrary objects
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise ValueError(f"not valid YAML{where}: {getattr(exc, 'problem', None) or exc}") from None


class Report:
    def __init__(self):
        self.items = []
        self.counts = {"error": 0, "warn": 0, "info": 0}

    def add(self, level, text):
        self.counts[level] += 1
        if len(self.items) < MAX_REPORT_LINES:
            self.items.append({"level": level, "text": text})

    def as_list(self):
        extra = sum(self.counts.values()) - len(self.items)
        out = list(self.items)
        if extra > 0:
            out.append({"level": "info", "text": f"... and {extra} more"})
        return out


def _text(value):
    return "" if value is None else str(value).strip()


def _network(entry, index, report):
    where = f"network #{index}"
    unknown = sorted(set(entry) - KNOWN_NETWORK_KEYS)
    if unknown:
        report.add("warn", f"{where}: option(s) not carried over: {', '.join(map(str, unknown))}")
    try:
        net = ipaddress.ip_network(f"{_text(entry.get('subnet'))}/{_text(entry.get('netmask'))}", strict=False)
    except ValueError:
        report.add("error", f"{where}: subnet {entry.get('subnet')!r} with netmask {entry.get('netmask')!r} is invalid")
        return None
    if _text(entry.get("subnet")) != str(net.network_address):
        report.add("warn", f"{where}: {entry.get('subnet')} is not the first address of {net}; using {net}")
    if net.version != 4:
        report.add("error", f"{where}: only IPv4 networks can be converted")
        return None
    start, end = _text(entry.get("range_start")), _text(entry.get("range_end"))
    try:
        low, high = ipaddress.ip_address(start), ipaddress.ip_address(end)
        if low not in net or high not in net or low > high:
            raise ValueError
    except ValueError:
        report.add("error", f"{where}: address range {start or '?'} - {end or '?'} does not fit inside {net}")
        return None
    broadcast = _text(entry.get("broadcast"))
    if broadcast and broadcast != str(net.broadcast_address):
        report.add("warn", f"{where}: broadcast {broadcast} differs from {net.broadcast_address}; Kea works the "
                           "broadcast address out itself, so it is not carried over")
    elif broadcast:
        report.add("info", f"{where}: broadcast address is worked out by Kea, nothing to carry over")
    return net


def convert(options, default_interface="eth0"):
    """Returns ``{"ok", "settings", "report": [{level, text}], "counts"}``; ``settings`` is None when not usable."""
    report = Report()
    if not isinstance(options, dict):
        report.add("error", "this is not the options of the DHCP server app (expected a mapping with 'networks' and 'hosts')")
        return {"ok": False, "settings": None, "report": report.as_list(), "counts": report.counts}
    unknown = sorted(set(options) - KNOWN_OPTIONS)
    if unknown:
        report.add("warn", f"option(s) not carried over: {', '.join(map(str, unknown))}")

    networks = [n for n in (options.get("networks") or []) if isinstance(n, dict)]
    if not networks:
        report.add("error", "no networks found: nothing to convert")
        return {"ok": False, "settings": None, "report": report.as_list(), "counts": report.counts}

    interfaces = []
    subnets = []
    default_lease = options.get("default_lease")
    max_lease = options.get("max_lease")
    for index, entry in enumerate(networks, start=1):
        net = _network(entry, index, report)
        if net is None:
            continue
        interface = _text(entry.get("interface"))
        if interface and interface not in interfaces:
            interfaces.append(interface)
        subnets.append({
            "net": net,
            "subnet": str(net),
            "pools": [{"start": _text(entry.get("range_start")), "end": _text(entry.get("range_end"))}],
            "router": _text(entry.get("gateway")),
            "dns": [str(d).strip() for d in (options.get("dns") or []) if str(d).strip()],
            "domain_name": _text(options.get("domain")),
            "domain_search": [],
            "ntp_servers": [str(n).strip() for n in (options.get("ntp") or []) if str(n).strip()],
            "custom_options": [],
            "lease_time": default_lease if default_lease is not None else 86400,
            "max_lease_time": max_lease,
            "reservations": [],
        })
    if not subnets:
        return {"ok": False, "settings": None, "report": report.as_list(), "counts": report.counts}

    if len(interfaces) > 1:
        report.add("warn", f"networks use several interfaces ({', '.join(interfaces)}); this app serves one interface, "
                           f"so {interfaces[0]} is used. Run a second app instance or change the networks for the others")
    interface = interfaces[0] if interfaces else default_interface
    if not interfaces:
        report.add("warn", f"no interface named; using {interface}")

    lease = cfg._parse_int(default_lease)
    if lease is not None and lease < cfg.LEASE_MIN:
        report.add("warn", f"lease time {lease} s is below the minimum of {cfg.LEASE_MIN} s; raised")
        for s in subnets:
            s["lease_time"] = cfg.LEASE_MIN
    maximum = cfg._parse_int(max_lease)
    if maximum is not None and lease is not None and maximum < lease:
        report.add("warn", f"max_lease ({maximum}) is smaller than default_lease ({lease}); the maximum is not carried over")
        for s in subnets:
            s["max_lease_time"] = None

    ntp = subnets[0]["ntp_servers"]
    if ntp:
        names = [n for n in ntp if not cfg._is_address(n)]
        if names:
            report.add("info", "NTP host names are kept as names: this app resolves them every hour and sends only "
                               "addresses that answer as NTP servers (check them with the Check button)")

    hosts_raw = options.get("hosts") or []
    if not isinstance(hosts_raw, list):
        report.add("error", "'hosts' must be a list")
        hosts_raw = []
    seen_macs, seen_ips, shared = {}, {}, set()
    carried = duplicates = 0
    for index, host in enumerate(hosts_raw, start=1):
        if not isinstance(host, dict):
            report.add("error", f"host #{index}: invalid format, skipped")
            continue
        label = f"host #{index} ({_text(host.get('name')) or _text(host.get('mac')) or '?'})"
        mac = cfg.normalize_mac(host.get("mac"))
        if mac is None:
            report.add("error", f"{label}: MAC address {host.get('mac')!r} is invalid, skipped")
            continue
        ip_text = _text(host.get("ip"))
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            report.add("error", f"{label}: IP address {ip_text!r} is invalid, skipped")
            continue
        target = next((s for s in subnets if ip in s["net"]), None)
        if target is None:
            report.add("error", f"{label}: {ip} is not inside any converted network, skipped")
            continue
        if mac in seen_macs:
            if seen_macs[mac] == str(ip):
                duplicates += 1
                report.add("info", f"{label}: the same device and address are already listed above; "
                                   "the duplicate is ignored")
                continue
            report.add("error", f"{label}: MAC {mac} is already reserved for {seen_macs[mac]}; Kea allows one "
                                "reservation per MAC address, skipped")
            continue
        seen_macs[mac] = str(ip)
        hostname = _text(host.get("name"))
        if hostname and not cfg._RESERVATION_HOSTNAME_RE.match(hostname):
            report.add("warn", f"{label}: name {hostname!r} is not a valid host name; the reservation is kept without it")
            hostname = ""
        if str(ip) in seen_ips:
            shared.add(str(ip))
        seen_ips.setdefault(str(ip), []).append(mac)
        if cfg.normalize_mac(host.get("mac")) != _text(host.get("mac")).lower():
            report.add("info", f"{label}: MAC written as {host.get('mac')} is stored as {mac}")
        target["reservations"].append({"hw_address": mac, "ip_address": str(ip), "hostname": hostname})
        carried += 1
    skipped = len(hosts_raw) - carried - duplicates
    report.add("info", f"{carried} of {len(hosts_raw) - duplicates} devices carried over"
                       + (f", {skipped} skipped" if skipped else "")
                       + (f" ({duplicates} exact duplicate{'s' if duplicates != 1 else ''} ignored)" if duplicates else ""))
    if shared:
        report.add("info", f"{len(shared)} IP address(es) are reserved for more than one MAC address "
                           f"({', '.join(sorted(shared)[:5])}{'...' if len(shared) > 5 else ''}); \"Allow several devices to "
                           "share one IP\" was turned on")

    report.add("info", "\"Answer clients that ask for an unknown address\" (authoritative) was turned on, like the old app")
    report.add("info", "Existing leases are not carried over: clients get their address again when they renew "
                       "(at most one lease time)")

    for s in subnets:
        del s["net"]
    candidate = {
        "version": cfg.SETTINGS_VERSION,
        "global": cfg.default_global(interface, False, authoritative=True, allow_shared_ips=bool(shared)),
        "dhcp4": {"subnets": subnets},
        "dhcp6": {"subnets": []},
    }
    cleaned, errors = cfg.validate(candidate)
    for text in errors:
        report.add("error", text)
    ok = not errors and report.counts["error"] == 0
    # Skipped devices are errors in the report, but the rest is still a usable draft: let the user review it.
    usable = not errors
    return {"ok": usable, "settings": cleaned if usable else None, "report": report.as_list(),
            "counts": report.counts, "complete": ok}
