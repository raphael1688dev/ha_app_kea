"""Settings model, validation and Kea configuration rendering.

Settings are stored as JSON (``settings.json``) and edited from the Web UI.
Everything that reaches Kea goes through :func:`validate` first, then through
``render_dhcp4`` / ``render_dhcp6``.

Schema history
  v1: one pool per subnet (``pool_start`` / ``pool_end``), no global section.
  v2: ``pools`` list, extra DHCP options, DHCPv6 prefix delegation and a
      ``global`` section (interface, enable_dhcp6). v1 data is migrated on read.
"""
import copy
import ipaddress
import re

SETTINGS_VERSION = 2
LEASE_MIN = 60
LEASE_MAX = 31536000  # 365 days

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
# Names of devices that are reserved an address. Real devices announce names such as "zhimi-fan-za5_mibtf763", so
# underscores are accepted here (Kea does not sanitise reserved names); domains and search domains stay strict.
_RESERVATION_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
    r"(\.[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*$"
)
NTP6_SPACE = "v6-ntp-server-suboptions"
MAX_NTP_SERVERS = 16
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")  # IFNAMSIZ is 16 incl. NUL
_OPTION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# Options that have a dedicated field; a custom option must not shadow them.
MANAGED_OPTIONS = {
    "routers", "domain-name-servers", "domain-name", "domain-search",
    "ntp-servers", "dns-servers", "ntp-server",
}


def default_global(interface="eth0", enable_dhcp6=False, authoritative=False, allow_shared_ips=False):
    """Settings that apply to the whole server.

    authoritative: answer clients that ask for an address Kea does not know with a NAK instead of staying silent
      (the old ISC dhcpd based DHCP server app always did this).
    allow_shared_ips: several reservations may use the same IPv4 address (a device with a wired and a wireless
      interface); needs Kea's ``ip-reservations-unique: false``.
    """
    return {"interface": interface, "enable_dhcp6": bool(enable_dhcp6),
            "authoritative": bool(authoritative), "allow_shared_ips": bool(allow_shared_ips)}


def default_settings(interface="eth0", enable_dhcp6=False):
    return {
        "version": SETTINGS_VERSION,
        "global": default_global(interface, enable_dhcp6),
        "dhcp4": {
            "subnets": [
                {
                    "id": 1,
                    "subnet": "192.168.1.0/24",
                    "pools": [{"start": "192.168.1.100", "end": "192.168.1.200"}],
                    "router": "192.168.1.1",
                    "dns": ["1.1.1.1", "8.8.8.8"],
                    "domain_name": "",
                    "domain_search": [],
                    "ntp_servers": [],
                    "custom_options": [],
                    "lease_time": 86400,
                    "max_lease_time": None,
                    "reservations": [],
                }
            ]
        },
        "dhcp6": {
            "subnets": [
                {
                    "id": 1,
                    "subnet": "fd00::/64",
                    "pools": [{"start": "fd00::1000", "end": "fd00::2000"}],
                    "pd_pools": [],
                    "dns": ["2606:4700:4700::1111", "2001:4860:4860::8888"],
                    "domain_search": [],
                    "ntp_servers": [],
                    "custom_options": [],
                    "lease_time": 86400,
                    "reservations": [],
                }
            ]
        },
    }


def migrate(raw):
    """Return a copy of *raw* upgraded to the current layout (idempotent)."""
    if not isinstance(raw, dict):
        return raw
    data = copy.deepcopy(raw)
    for family in ("dhcp4", "dhcp6"):
        section = data.get(family)
        subnets = section.get("subnets") if isinstance(section, dict) else None
        if not isinstance(subnets, list):
            continue
        for subnet in subnets:
            if (isinstance(subnet, dict) and "pools" not in subnet
                    and ("pool_start" in subnet or "pool_end" in subnet)):
                subnet["pools"] = [{"start": subnet.pop("pool_start", ""),
                                    "end": subnet.pop("pool_end", "")}]
    return data


# --------------------------------------------------------------------------
# Normalisers
# --------------------------------------------------------------------------
def normalize_mac(value):
    """aa:bb:cc:dd:ee:ff from the usual spellings.

    Separated octets may have one or two digits ("b0:a7:32:6:a5:18" is b0:a7:32:06:a5:18): Kea and ISC dhcpd both
    accept that, so a device list written for dhcpd must too. Without separators (or Cisco style aabb.ccdd.eeff)
    exactly 12 digits are needed.
    """
    text = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{1,2}([:-][0-9a-f]{1,2}){5}", text):
        return ":".join(part.zfill(2) for part in re.split(r"[:-]", text))
    raw = re.sub(r"[:\-.\s]", "", text)
    if not re.fullmatch(r"[0-9a-f]{12}", raw):
        return None
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def normalize_duid(value):
    raw = re.sub(r"[:\-\s]", "", str(value or "")).lower()
    if not re.fullmatch(r"[0-9a-f]{6,260}", raw) or len(raw) % 2:
        return None
    return ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))


def _split_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p for p in re.split(r"[,\s]+", str(value).strip()) if p]


def _parse_ip_list(value, version):
    """Accept a comma/space separated string or a list. Returns (list, bad)."""
    good, bad = [], []
    for part in _split_list(value):
        try:
            addr = ipaddress.ip_address(part)
            if addr.version != version:
                raise ValueError
            good.append(str(addr))
        except ValueError:
            bad.append(part)
    return good, bad


def ntp6_suboption(entry):
    """Which DHCPv6 NTP suboption (RFC 5908) carries *entry*: an address, a multicast address or a host name."""
    try:
        address = ipaddress.ip_address(entry)
    except ValueError:
        return "ntp-server-fqdn"
    return "ntp-server-multicast" if address.is_multicast else "ntp-server-address"


def _is_host_name(name):
    """A DNS name, as opposed to a mistyped address: "999.1.1.1" or "192.168.1" would pass as labels, but no top-level
    domain is a number, so a name whose last label is all digits is refused."""
    return bool(_HOSTNAME_RE.match(name)) and not name.rsplit(".", 1)[-1].isdigit()


def _parse_ntp4_list(value):
    """DHCPv4 NTP servers: IPv4 addresses or host names (the app resolves names). Returns (entries, bad)."""
    good, bad = [], []
    for part in _split_list(value):
        try:
            address = ipaddress.ip_address(part)
        except ValueError:
            name = part.rstrip(".").lower()
            (good if _is_host_name(name) else bad).append(name if _is_host_name(name) else part)
            continue
        (good if address.version == 4 else bad).append(str(address) if address.version == 4 else part)
    return good, bad


def ntp_hostnames(settings):
    """Host names (not addresses) used as DHCPv4 NTP servers anywhere in *settings*."""
    names = []
    for s in settings["dhcp4"]["subnets"]:
        for entry in s.get("ntp_servers", []):
            try:
                ipaddress.ip_address(entry)
            except ValueError:
                if entry not in names:
                    names.append(entry)
    return names


MAX_NTP_ADDRESSES = 16      # sent to a client; a DHCPv4 option holds 255 bytes (63 addresses) and clients use a handful


def _ntp4_addresses(entries, resolved):
    """Replace host names by the addresses the app resolved and checked; unresolved names are left out.

    At most MAX_NTP_ADDRESSES are returned. When there are more, every entry contributes in turn, so one name with many
    addresses cannot push the others out.
    """
    groups = [[a for a in (resolved or {}).get(entry, [entry] if _is_address(entry) else [])] for entry in entries]
    flat = list(dict.fromkeys(a for group in groups for a in group))
    if len(flat) <= MAX_NTP_ADDRESSES:
        return flat
    out = []
    for rank in range(max(len(g) for g in groups)):
        for group in groups:
            if rank < len(group) and group[rank] not in out and len(out) < MAX_NTP_ADDRESSES:
                out.append(group[rank])
    return out


def _is_address(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _parse_ntp6_list(value):
    """DHCPv6 NTP servers: IPv6 addresses, IPv6 multicast addresses or host names. Returns (entries, bad)."""
    good, bad = [], []
    for part in _split_list(value):
        try:
            address = ipaddress.ip_address(part)
        except ValueError:
            name = part.rstrip(".").lower()
            (good if _is_host_name(name) else bad).append(name if _is_host_name(name) else part)
            continue
        (good if address.version == 6 else bad).append(str(address) if address.version == 6 else part)
    return good, bad


def _parse_name_list(value):
    good, bad = [], []
    for part in _split_list(value):
        name = part.rstrip(".")
        (good if _HOSTNAME_RE.match(name) else bad).append(name or part)
    return good, bad


def _parse_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _validate_global(raw, fallback, errors):
    base = fallback or default_global()
    if raw is not None and not isinstance(raw, dict):
        errors.append("global settings have an invalid format")
        raw = {}
    raw = raw or {}

    interface = str(raw.get("interface", base["interface"]) or "").strip()
    if not _IFACE_RE.match(interface):
        errors.append("network interface name is invalid "
                      "(1-15 characters: letters, digits, . _ : -)")
    out = {"interface": interface}
    for key in ("enable_dhcp6", "authoritative", "allow_shared_ips"):
        value = raw.get(key, base.get(key, False))
        if not isinstance(value, bool):
            errors.append(f"{key} must be true or false")
            value = bool(base.get(key, False))
        out[key] = value
    return out


def _validate_pools(raw_pools, version, network, where, errors):
    addr_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
    if raw_pools is None:
        return []
    if not isinstance(raw_pools, list):
        errors.append(f"{where}: pools must be a list")
        return []

    out, ranges = [], []
    for pidx, raw in enumerate(raw_pools, start=1):
        pwhere = f"{where} pool #{pidx}"
        if not isinstance(raw, dict):
            errors.append(f"{pwhere}: invalid format")
            continue
        start = end = None
        try:
            start = addr_cls(str(raw.get("start", "")).strip())
        except ValueError:
            errors.append(f"{pwhere}: start IP is invalid")
        try:
            end = addr_cls(str(raw.get("end", "")).strip())
        except ValueError:
            errors.append(f"{pwhere}: end IP is invalid")
        if network and start and start not in network:
            errors.append(f"{pwhere}: start IP is outside the subnet")
        if network and end and end not in network:
            errors.append(f"{pwhere}: end IP is outside the subnet")
        if start and end and start > end:
            errors.append(f"{pwhere}: start IP must not be greater than end IP")
        elif start and end:
            ranges.append((int(start), int(end), pidx))
        out.append({"start": str(start) if start else str(raw.get("start", "")),
                    "end": str(end) if end else str(raw.get("end", ""))})

    ranges.sort()
    for (s1, e1, i1), (s2, _e2, i2) in zip(ranges, ranges[1:]):
        if s2 <= e1:
            errors.append(f"{where}: pool #{i1} and pool #{i2} overlap")
    return out


def _validate_pd_pools(raw_pools, where, errors):
    if raw_pools is None:
        return []
    if not isinstance(raw_pools, list):
        errors.append(f"{where}: prefix delegation pools must be a list")
        return []

    out, nets = [], []
    for idx, raw in enumerate(raw_pools, start=1):
        pwhere = f"{where} prefix pool #{idx}"
        if not isinstance(raw, dict):
            errors.append(f"{pwhere}: invalid format")
            continue
        prefix = str(raw.get("prefix", "")).strip()
        plen = _parse_int(raw.get("prefix_len"))
        dlen = _parse_int(raw.get("delegated_len"))
        ok = True
        if plen is None or not 1 <= plen <= 128:
            errors.append(f"{pwhere}: prefix length must be between 1 and 128")
            ok = False
        if dlen is None or not 1 <= dlen <= 128:
            errors.append(f"{pwhere}: delegated length must be between 1 and 128")
            ok = False
        if ok and dlen < plen:
            errors.append(f"{pwhere}: delegated length must not be smaller than prefix length")
            ok = False
        network = None
        try:
            ipaddress.IPv6Address(prefix)
            if plen is not None and 1 <= plen <= 128:
                network = ipaddress.IPv6Network(f"{prefix}/{plen}", strict=True)
        except ValueError:
            errors.append(f"{pwhere}: prefix is invalid (IPv6 prefix without host bits)")
        if network is not None and ok:
            nets.append((network, idx))
        out.append({"prefix": prefix,
                    "prefix_len": plen if plen is not None else raw.get("prefix_len"),
                    "delegated_len": dlen if dlen is not None else raw.get("delegated_len")})

    for i, (a, ai) in enumerate(nets):
        for b, bi in nets[i + 1:]:
            if a.overlaps(b):
                errors.append(f"{where}: prefix pool #{ai} and prefix pool #{bi} overlap")
    return out


def _validate_custom_options(raw_list, where, errors):
    if raw_list is None or raw_list == "":
        return []
    if not isinstance(raw_list, list):
        errors.append(f"{where}: custom options must be a list")
        return []
    out, seen = [], set()
    for idx, raw in enumerate(raw_list, start=1):
        owhere = f"{where} custom option #{idx}"
        if not isinstance(raw, dict):
            errors.append(f"{owhere}: invalid format")
            continue
        name = str(raw.get("name", "")).strip().lower()
        data = str(raw.get("data", "")).strip()
        if not _OPTION_NAME_RE.match(name):
            errors.append(f"{owhere}: option name is invalid (Kea option name such as "
                          "tftp-server-name)")
        elif name in MANAGED_OPTIONS:
            errors.append(f"{owhere}: '{name}' has its own field above")
        elif name in seen:
            errors.append(f"{owhere}: option '{name}' is duplicated")
        seen.add(name)
        if not data or len(data) > 255 or any(ord(c) < 32 for c in data):
            errors.append(f"{owhere}: option data is empty or invalid")
        out.append({"name": name, "data": data})
    return out


def _validate_subnets(section, version, errors, allow_shared=False):
    label = "IPv4" if version == 4 else "IPv6"
    net_cls = ipaddress.IPv4Network if version == 4 else ipaddress.IPv6Network

    if section is not None and not isinstance(section, dict):
        errors.append(f"{label}: invalid format")
        return []
    raw_subnets = (section or {}).get("subnets", [])
    if not isinstance(raw_subnets, list):
        errors.append(f"{label}: subnets must be a list")
        return []

    cleaned = []
    used_ids = set()
    for idx, raw in enumerate(raw_subnets, start=1):
        where = f"{label} subnet #{idx}"
        if not isinstance(raw, dict):
            errors.append(f"{where}: invalid format")
            continue
        item = {}

        network = None
        try:
            network = net_cls(str(raw.get("subnet", "")).strip(), strict=True)
            item["subnet"] = str(network)
        except ValueError:
            errors.append(f"{where}: invalid subnet (CIDR without host bits, e.g. "
                          f"{'192.168.1.0/24' if version == 4 else 'fd00::/64'})")
            item["subnet"] = str(raw.get("subnet", ""))

        item["pools"] = _validate_pools(raw.get("pools"), version, network, where, errors)

        if version == 4:
            router = str(raw.get("router", "") or "").strip()
            if router:
                try:
                    router_ip = ipaddress.IPv4Address(router)
                    if network and router_ip not in network:
                        errors.append(f"{where}: default gateway is outside the subnet")
                    router = str(router_ip)
                except ValueError:
                    errors.append(f"{where}: default gateway is invalid")
            item["router"] = router

        dns, bad = _parse_ip_list(raw.get("dns"), version)
        if bad:
            errors.append(f"{where}: DNS is invalid: {', '.join(bad)}")
        item["dns"] = dns

        if version == 4:
            domain = str(raw.get("domain_name", "") or "").strip().rstrip(".")
            if domain and not _HOSTNAME_RE.match(domain):
                errors.append(f"{where}: domain name is invalid")
            item["domain_name"] = domain

        search, bad = _parse_name_list(raw.get("domain_search"))
        if bad:
            errors.append(f"{where}: search domain is invalid: {', '.join(bad)}")
        item["domain_search"] = search

        if version == 4:
            ntp, bad = _parse_ntp4_list(raw.get("ntp_servers"))
            if bad:
                errors.append(f"{where}: NTP server is invalid (an IPv4 address or a host name): {', '.join(bad)}")
        else:
            ntp, bad = _parse_ntp6_list(raw.get("ntp_servers"))
            if bad:
                errors.append(f"{where}: NTP server is invalid (an IPv6 address or a host name): {', '.join(bad)}")
        if len(ntp) > MAX_NTP_SERVERS:
            errors.append(f"{where}: at most {MAX_NTP_SERVERS} NTP servers")
        item["ntp_servers"] = ntp

        item["custom_options"] = _validate_custom_options(raw.get("custom_options"), where, errors)

        if version == 6:
            item["pd_pools"] = _validate_pd_pools(raw.get("pd_pools"), where, errors)

        lease = _parse_int(raw.get("lease_time"))
        if lease is None or not LEASE_MIN <= lease <= LEASE_MAX:
            errors.append(f"{where}: lease time must be between {LEASE_MIN} and {LEASE_MAX} seconds")
            lease = 86400
        item["lease_time"] = lease

        if version == 4:
            raw_max = raw.get("max_lease_time")
            if raw_max in (None, ""):
                item["max_lease_time"] = None
            else:
                maximum = _parse_int(raw_max)
                if maximum is None or not LEASE_MIN <= maximum <= LEASE_MAX:
                    errors.append(f"{where}: maximum lease time must be between {LEASE_MIN} and {LEASE_MAX} seconds")
                elif maximum < lease:
                    errors.append(f"{where}: maximum lease time must not be smaller than the lease time")
                item["max_lease_time"] = maximum

        # id (stable, so lease files keep matching their subnet)
        sid = _parse_int(raw.get("id"))
        if sid is not None and sid > 0 and sid not in used_ids:
            item["id"] = sid
            used_ids.add(sid)
        else:
            item["id"] = None

        item["reservations"] = _validate_reservations(
            raw.get("reservations", []), version, network, where, errors)
        cleaned.append(item)

    next_id = max(used_ids, default=0) + 1
    for item in cleaned:
        if item["id"] is None:
            item["id"] = next_id
            next_id += 1

    # overlapping subnets
    nets = []
    for item in cleaned:
        try:
            nets.append(net_cls(item["subnet"]))
        except (ValueError, KeyError):
            pass
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            if a.overlaps(b):
                errors.append(f"{label}: subnets {a} and {b} overlap")

    # Reserved IPs must be unique across the section, unless sharing is switched on (IPv4 only): a device with a
    # wired and a wireless interface has two MAC addresses and one IP. A MAC may still be listed only once per subnet.
    if not (allow_shared and version == 4):
        seen = set()
        for item in cleaned:
            for res in item["reservations"]:
                ip = res["ip_address"]
                if ip in seen:
                    errors.append(f"{label}: reservation IP {ip} is duplicated" +
                                  (" (turn on \"Allow several devices to share one IP\" in Settings if one device has "
                                   "two network interfaces)" if version == 4 else ""))
                seen.add(ip)
    return cleaned


def _validate_reservations(raw_list, version, network, where, errors):
    if not isinstance(raw_list, list):
        errors.append(f"{where}: reservations must be a list")
        return []
    ident_key = "hw_address" if version == 4 else "duid"
    ident_label = "MAC address" if version == 4 else "DUID"
    normalize = normalize_mac if version == 4 else normalize_duid
    addr_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address

    out, seen_ids = [], set()
    for ridx, raw in enumerate(raw_list, start=1):
        rwhere = f"{where} reservation #{ridx}"
        if not isinstance(raw, dict):
            errors.append(f"{rwhere}: invalid format")
            continue
        ident = normalize(raw.get(ident_key))
        if ident is None:
            errors.append(f"{rwhere}: {ident_label} is invalid")
            ident = str(raw.get(ident_key, ""))
        elif ident in seen_ids:
            errors.append(f"{rwhere}: {ident_label} {ident} is duplicated within the subnet")
        seen_ids.add(ident)

        ip_text = str(raw.get("ip_address", "")).strip()
        try:
            ip = addr_cls(ip_text)
            if network and ip not in network:
                errors.append(f"{rwhere}: IP is outside the subnet")
            ip_text = str(ip)
        except ValueError:
            errors.append(f"{rwhere}: IP is invalid")

        hostname = str(raw.get("hostname", "") or "").strip()
        if hostname and not _RESERVATION_HOSTNAME_RE.match(hostname):
            errors.append(f"{rwhere}: hostname is invalid (letters, digits, hyphens and underscores)")

        out.append({ident_key: ident, "ip_address": ip_text, "hostname": hostname})
    return out


def validate(raw, fallback_global=None):
    """Validate user supplied settings. Returns ``(cleaned, errors)``.

    ``fallback_global`` supplies the interface / DHCPv6 switch when *raw* has no
    ``global`` section (v1 files, or the app options on first start).
    """
    errors = []
    if not isinstance(raw, dict):
        return None, ["invalid settings format"]
    raw = migrate(raw)
    settings_global = _validate_global(raw.get("global"), fallback_global, errors)
    cleaned = {
        "version": SETTINGS_VERSION,
        "global": settings_global,
        "dhcp4": {"subnets": _validate_subnets(raw.get("dhcp4"), 4, errors,
                                               allow_shared=settings_global["allow_shared_ips"])},
        "dhcp6": {"subnets": _validate_subnets(raw.get("dhcp6"), 6, errors)},
    }
    return cleaned, errors


# --------------------------------------------------------------------------
# Kea config rendering
# --------------------------------------------------------------------------
def _logger(name):
    return [{
        "name": name,
        "output-options": [{"output": "stdout"}],
        "severity": "INFO",
    }]


def _option(name, data):
    return {"name": name, "data": data}


def _add_control(server, control):
    """Open the daemon's control socket and load the hook libraries the app's commands need.

    ``control`` is ``{"socket": "kea4-ctrl.sock", "hooks": ["libdhcp_lease_cmds.so"]}`` or None. Kea 3.0 accepts a
    control socket only in its own socket directory and hook libraries only from its own hooks directory, so both
    are given as bare file names.
    """
    if not control:
        return
    server["control-sockets"] = [{"socket-type": "unix", "socket-name": control["socket"]}]
    if control.get("hooks"):
        server["hooks-libraries"] = [{"library": name} for name in control["hooks"]]


def render_dhcp4(settings, lease_file, ntp_addresses=None, control=None):
    subnets = []
    for s in settings["dhcp4"]["subnets"]:
        options = []
        if s.get("router"):
            options.append(_option("routers", s["router"]))
        if s["dns"]:
            options.append(_option("domain-name-servers", ", ".join(s["dns"])))
        if s.get("domain_name"):
            options.append(_option("domain-name", s["domain_name"]))
        if s.get("domain_search"):
            options.append(_option("domain-search", ", ".join(s["domain_search"])))
        ntp = _ntp4_addresses(s.get("ntp_servers", []), ntp_addresses)
        if ntp:
            options.append(_option("ntp-servers", ", ".join(ntp)))
        options += [_option(o["name"], o["data"]) for o in s.get("custom_options", [])]
        entry = {
            "id": s["id"],
            "subnet": s["subnet"],
            "pools": [{"pool": f"{p['start']} - {p['end']}"} for p in s["pools"]],
            "valid-lifetime": s["lease_time"],
            "option-data": options,
        }
        if s.get("max_lease_time"):
            entry["max-valid-lifetime"] = s["max_lease_time"]
        if s["reservations"]:
            entry["reservations"] = [
                _drop_empty({
                    "hw-address": r["hw_address"],
                    "ip-address": r["ip_address"],
                    "hostname": r["hostname"],
                })
                for r in s["reservations"]
            ]
        subnets.append(entry)
    server = {
        "interfaces-config": {"interfaces": [settings["global"]["interface"]],
                              "dhcp-socket-type": "raw"},
        "lease-database": {
            "type": "memfile", "persist": True,
            "name": lease_file, "lfc-interval": 3600,
        },
        "valid-lifetime": 86400,
        "subnet4": subnets,
        "loggers": _logger("kea-dhcp4"),
    }
    if settings["global"].get("authoritative"):
        server["authoritative"] = True
    if settings["global"].get("allow_shared_ips"):
        server["ip-reservations-unique"] = False
    _add_control(server, control)
    return {"Dhcp4": server}


def render_dhcp6(settings, lease_file, control=None):
    subnets = []
    for s in settings["dhcp6"]["subnets"]:
        options = []
        if s["dns"]:
            options.append(_option("dns-servers", ", ".join(s["dns"])))
        if s.get("domain_search"):
            options.append(_option("domain-search", ", ".join(s["domain_search"])))
        if s.get("ntp_servers"):
            # RFC 5908: option 56 is a container; every server is a suboption (Kea allows several of one kind).
            options.append({"name": "ntp-server"})
            options += [{"name": ntp6_suboption(server), "space": NTP6_SPACE, "data": server}
                        for server in s["ntp_servers"]]
        options += [_option(o["name"], o["data"]) for o in s.get("custom_options", [])]
        entry = {
            "id": s["id"],
            "subnet": s["subnet"],
            "pools": [{"pool": f"{p['start']} - {p['end']}"} for p in s["pools"]],
            "valid-lifetime": s["lease_time"],
            "preferred-lifetime": s["lease_time"] // 2,
            "option-data": options,
        }
        if s.get("pd_pools"):
            entry["pd-pools"] = [
                {"prefix": p["prefix"], "prefix-len": p["prefix_len"],
                 "delegated-len": p["delegated_len"]}
                for p in s["pd_pools"]
            ]
        if s["reservations"]:
            entry["reservations"] = [
                _drop_empty({
                    "duid": r["duid"],
                    "ip-addresses": [r["ip_address"]],
                    "hostname": r["hostname"],
                })
                for r in s["reservations"]
            ]
        subnets.append(entry)
    server = {
        "interfaces-config": {"interfaces": [settings["global"]["interface"]]},
        "lease-database": {
            "type": "memfile", "persist": True,
            "name": lease_file, "lfc-interval": 3600,
        },
        "valid-lifetime": 86400,
        "preferred-lifetime": 43200,
        "subnet6": subnets,
        "loggers": _logger("kea-dhcp6"),
    }
    _add_control(server, control)
    return {"Dhcp6": server}


def _drop_empty(d):
    return {k: v for k, v in d.items() if v not in ("", None)}
