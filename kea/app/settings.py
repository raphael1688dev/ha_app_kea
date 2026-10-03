"""Settings model, validation and Kea configuration rendering.

Settings are stored as JSON (``settings.json``) and edited from the Web UI.
Everything that reaches Kea goes through :func:`validate` first, then through
``render_dhcp4`` / ``render_dhcp6``.
"""
import copy
import ipaddress
import re

LEASE_MIN = 60
LEASE_MAX = 31536000  # 365 days

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)

DEFAULT_SETTINGS = {
    "version": 1,
    "dhcp4": {
        "subnets": [
            {
                "id": 1,
                "subnet": "192.168.1.0/24",
                "pool_start": "192.168.1.100",
                "pool_end": "192.168.1.200",
                "router": "192.168.1.1",
                "dns": ["1.1.1.1", "8.8.8.8"],
                "lease_time": 86400,
                "reservations": [],
            }
        ]
    },
    "dhcp6": {
        "subnets": [
            {
                "id": 1,
                "subnet": "fd00::/64",
                "pool_start": "fd00::1000",
                "pool_end": "fd00::2000",
                "dns": ["2606:4700:4700::1111", "2001:4860:4860::8888"],
                "lease_time": 86400,
                "reservations": [],
            }
        ]
    },
}


def default_settings():
    return copy.deepcopy(DEFAULT_SETTINGS)


# --------------------------------------------------------------------------
# Normalisers
# --------------------------------------------------------------------------
def normalize_mac(value):
    raw = re.sub(r"[:\-.\s]", "", str(value or "")).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", raw):
        return None
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def normalize_duid(value):
    raw = re.sub(r"[:\-\s]", "", str(value or "")).lower()
    if not re.fullmatch(r"[0-9a-f]{6,260}", raw) or len(raw) % 2:
        return None
    return ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))


def _parse_ip_list(value, version):
    """Accept a comma/space separated string or a list. Returns (list, bad)."""
    if value is None:
        return [], []
    if isinstance(value, (list, tuple)):
        parts = [str(v).strip() for v in value]
    else:
        parts = re.split(r"[,\s]+", str(value).strip())
    good, bad = [], []
    for part in parts:
        if not part:
            continue
        try:
            addr = ipaddress.ip_address(part)
            if addr.version != version:
                raise ValueError
            good.append(str(addr))
        except ValueError:
            bad.append(part)
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
def _validate_subnets(section, version, errors):
    label = "IPv4" if version == 4 else "IPv6"
    net_cls = ipaddress.IPv4Network if version == 4 else ipaddress.IPv6Network
    addr_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address

    raw_subnets = (section or {}).get("subnets", [])
    if not isinstance(raw_subnets, list):
        errors.append(f"{label}: subnets 必須是清單")
        return []

    cleaned = []
    used_ids = set()
    for idx, raw in enumerate(raw_subnets, start=1):
        where = f"{label} 網段 #{idx}"
        if not isinstance(raw, dict):
            errors.append(f"{where}: 格式錯誤")
            continue
        item = {}

        # subnet
        network = None
        try:
            network = net_cls(str(raw.get("subnet", "")).strip(), strict=True)
            item["subnet"] = str(network)
        except ValueError:
            errors.append(f"{where}: 網段格式錯誤(需為 CIDR,例如 "
                          f"{'192.168.1.0/24' if version == 4 else 'fd00::/64'})")

        # pool
        start = end = None
        try:
            start = addr_cls(str(raw.get("pool_start", "")).strip())
        except ValueError:
            errors.append(f"{where}: Pool 起始 IP 格式錯誤")
        try:
            end = addr_cls(str(raw.get("pool_end", "")).strip())
        except ValueError:
            errors.append(f"{where}: Pool 結束 IP 格式錯誤")
        if network and start and start not in network:
            errors.append(f"{where}: Pool 起始 IP 不在網段內")
        if network and end and end not in network:
            errors.append(f"{where}: Pool 結束 IP 不在網段內")
        if start and end and start > end:
            errors.append(f"{where}: Pool 起始 IP 不可大於結束 IP")
        item["pool_start"] = str(start) if start else str(raw.get("pool_start", ""))
        item["pool_end"] = str(end) if end else str(raw.get("pool_end", ""))

        # router (IPv4 only)
        if version == 4:
            router = str(raw.get("router", "") or "").strip()
            if router:
                try:
                    router_ip = ipaddress.IPv4Address(router)
                    if network and router_ip not in network:
                        errors.append(f"{where}: 預設閘道不在網段內")
                    router = str(router_ip)
                except ValueError:
                    errors.append(f"{where}: 預設閘道格式錯誤")
            item["router"] = router

        # dns
        dns, bad = _parse_ip_list(raw.get("dns"), version)
        if bad:
            errors.append(f"{where}: DNS 格式錯誤:{', '.join(bad)}")
        item["dns"] = dns

        # lease time
        lease = _parse_int(raw.get("lease_time"))
        if lease is None or not LEASE_MIN <= lease <= LEASE_MAX:
            errors.append(f"{where}: 租約秒數需介於 {LEASE_MIN} 到 {LEASE_MAX}")
            lease = 86400
        item["lease_time"] = lease

        # id (stable, so lease files keep matching their subnet)
        sid = _parse_int(raw.get("id"))
        if sid is not None and sid > 0 and sid not in used_ids:
            item["id"] = sid
            used_ids.add(sid)
        else:
            item["id"] = None

        # reservations
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
            nets.append((net_cls(item["subnet"]), item))
        except (ValueError, KeyError):
            pass
    for i, (a, _) in enumerate(nets):
        for b, _ in nets[i + 1:]:
            if a.overlaps(b):
                errors.append(f"{label}: 網段 {a} 與 {b} 範圍重疊")

    # reserved IPs must be unique across the whole section
    seen = {}
    for item in cleaned:
        for res in item["reservations"]:
            ip = res["ip_address"]
            if ip in seen:
                errors.append(f"{label}: 靜態綁定 IP {ip} 重複")
            seen[ip] = True
    return cleaned


def _validate_reservations(raw_list, version, network, where, errors):
    if not isinstance(raw_list, list):
        errors.append(f"{where}: 靜態綁定必須是清單")
        return []
    ident_key = "hw_address" if version == 4 else "duid"
    ident_label = "MAC 位址" if version == 4 else "DUID"
    normalize = normalize_mac if version == 4 else normalize_duid
    addr_cls = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address

    out, seen_ids = [], set()
    for ridx, raw in enumerate(raw_list, start=1):
        rwhere = f"{where} 靜態綁定 #{ridx}"
        if not isinstance(raw, dict):
            errors.append(f"{rwhere}: 格式錯誤")
            continue
        ident = normalize(raw.get(ident_key))
        if ident is None:
            errors.append(f"{rwhere}: {ident_label} 格式錯誤")
            ident = str(raw.get(ident_key, ""))
        elif ident in seen_ids:
            errors.append(f"{rwhere}: {ident_label} {ident} 在同一網段內重複")
        seen_ids.add(ident)

        ip_text = str(raw.get("ip_address", "")).strip()
        try:
            ip = addr_cls(ip_text)
            if network and ip not in network:
                errors.append(f"{rwhere}: IP 不在網段內")
            ip_text = str(ip)
        except ValueError:
            errors.append(f"{rwhere}: IP 格式錯誤")

        hostname = str(raw.get("hostname", "") or "").strip()
        if hostname and not _HOSTNAME_RE.match(hostname):
            errors.append(f"{rwhere}: 主機名稱格式錯誤")

        out.append({ident_key: ident, "ip_address": ip_text, "hostname": hostname})
    return out


def validate(raw):
    """Validate user supplied settings. Returns ``(cleaned, errors)``."""
    errors = []
    if not isinstance(raw, dict):
        return None, ["設定格式錯誤"]
    cleaned = {
        "version": 1,
        "dhcp4": {"subnets": _validate_subnets(raw.get("dhcp4"), 4, errors)},
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


def render_dhcp4(settings, interface, lease_file):
    subnets = []
    for s in settings["dhcp4"]["subnets"]:
        options = []
        if s.get("router"):
            options.append({"name": "routers", "data": s["router"]})
        if s["dns"]:
            options.append({"name": "domain-name-servers", "data": ", ".join(s["dns"])})
        entry = {
            "id": s["id"],
            "subnet": s["subnet"],
            "pools": [{"pool": f"{s['pool_start']} - {s['pool_end']}"}],
            "valid-lifetime": s["lease_time"],
            "option-data": options,
        }
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
    return {
        "Dhcp4": {
            "interfaces-config": {"interfaces": [interface], "dhcp-socket-type": "raw"},
            "lease-database": {
                "type": "memfile", "persist": True,
                "name": lease_file, "lfc-interval": 3600,
            },
            "valid-lifetime": 86400,
            "subnet4": subnets,
            "loggers": _logger("kea-dhcp4"),
        }
    }


def render_dhcp6(settings, interface, lease_file):
    subnets = []
    for s in settings["dhcp6"]["subnets"]:
        options = []
        if s["dns"]:
            options.append({"name": "dns-servers", "data": ", ".join(s["dns"])})
        entry = {
            "id": s["id"],
            "subnet": s["subnet"],
            "pools": [{"pool": f"{s['pool_start']} - {s['pool_end']}"}],
            "valid-lifetime": s["lease_time"],
            "preferred-lifetime": s["lease_time"] // 2,
            "option-data": options,
        }
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
    return {
        "Dhcp6": {
            "interfaces-config": {"interfaces": [interface]},
            "lease-database": {
                "type": "memfile", "persist": True,
                "name": lease_file, "lfc-interval": 3600,
            },
            "valid-lifetime": 86400,
            "preferred-lifetime": 43200,
            "subnet6": subnets,
            "loggers": _logger("kea-dhcp6"),
        }
    }


def _drop_empty(d):
    return {k: v for k, v in d.items() if v not in ("", None)}
