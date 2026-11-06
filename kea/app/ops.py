"""Small, named changes to the settings (for scripts and agents) and a readable description of what a change does.

:func:`apply_operations` takes the current (cleaned) settings and a list of operations and returns a new settings
document; nothing here writes anything. The result goes through the normal validation like any other settings, so an
operation cannot produce something the Web UI would refuse. Operations that can break the network (changing the
interface, turning DHCPv6 on or off, adding or removing a subnet) do not exist on purpose.
"""
import copy

import settings as cfg

MAX_OPERATIONS = 100
FAMILIES = ("dhcp4", "dhcp6")
SUBNET_FIELDS = {"dns", "router", "domain_name", "lease_time", "max_lease_time", "ntp_servers", "domain_search"}
GLOBAL_FIELDS = {"authoritative", "allow_shared_ips"}
_ID_KEY = {"dhcp4": "hw_address", "dhcp6": "duid"}


class OperationError(Exception):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise OperationError(f"'{name}' must be a non-empty text")
    return value.strip()


def _family(op):
    family = op.get("family")
    if family not in FAMILIES:
        raise OperationError("'family' must be \"dhcp4\" or \"dhcp6\"")
    return family


def _subnet(doc, op, family):
    sid = op.get("subnet_id")
    if isinstance(sid, bool) or not isinstance(sid, int):
        raise OperationError("'subnet_id' must be a whole number (see GET /v1/settings)")
    for subnet in doc[family]["subnets"]:
        if subnet.get("id") == sid:
            return subnet
    raise OperationError(f"{family} has no subnet with id {sid}")


def _normalize_identifier(family, value):
    value = _text(value, "identifier")
    normalized = (cfg.normalize_mac if family == "dhcp4" else cfg.normalize_duid)(value)
    return normalized or value             # an invalid one is passed on; the validation names the problem


def _find_reservation(doc, family, identifier):
    key = _ID_KEY[family]
    for subnet in doc[family]["subnets"]:
        for res in subnet.get("reservations", []):
            if res.get(key) == identifier:
                return subnet, res
    return None, None


def _only(op, allowed, required=()):
    extra = sorted(set(op) - allowed - {"op"})
    if extra:
        raise OperationError(f"unknown field(s): {', '.join(extra)}")
    missing = [name for name in required if name not in op]
    if missing:
        raise OperationError(f"missing field(s): {', '.join(missing)}")


def _add_reservation(doc, op):
    _only(op, {"family", "subnet_id", "identifier", "ip_address", "hostname"},
          ("family", "subnet_id", "identifier", "ip_address"))
    family = _family(op)
    subnet = _subnet(doc, op, family)
    identifier = _normalize_identifier(family, op["identifier"])
    if _find_reservation(doc, family, identifier)[1] is not None:
        raise OperationError(f"{identifier} already has a reservation (use update_reservation)")
    hostname = op.get("hostname") or ""
    if not isinstance(hostname, str):
        raise OperationError("'hostname' must be text")
    subnet.setdefault("reservations", []).append(
        {_ID_KEY[family]: identifier, "ip_address": _text(op["ip_address"], "ip_address"), "hostname": hostname.strip()})


def _update_reservation(doc, op):
    _only(op, {"family", "identifier", "ip_address", "hostname"}, ("family", "identifier"))
    family = _family(op)
    identifier = _normalize_identifier(family, op["identifier"])
    _, res = _find_reservation(doc, family, identifier)
    if res is None:
        raise OperationError(f"{identifier} has no reservation (use add_reservation)")
    if "ip_address" not in op and "hostname" not in op:
        raise OperationError("give 'ip_address' and/or 'hostname' to change")
    if "ip_address" in op:
        res["ip_address"] = _text(op["ip_address"], "ip_address")
    if "hostname" in op:
        if not isinstance(op["hostname"], str):
            raise OperationError("'hostname' must be text")
        res["hostname"] = op["hostname"].strip()


def _delete_reservation(doc, op):
    _only(op, {"family", "identifier"}, ("family", "identifier"))
    family = _family(op)
    identifier = _normalize_identifier(family, op["identifier"])
    subnet, res = _find_reservation(doc, family, identifier)
    if res is None:
        raise OperationError(f"{identifier} has no reservation")
    subnet["reservations"].remove(res)


def _update_subnet(doc, op):
    _only(op, {"family", "subnet_id", "fields"}, ("family", "subnet_id", "fields"))
    family = _family(op)
    subnet = _subnet(doc, op, family)
    fields = op["fields"]
    if not isinstance(fields, dict) or not fields:
        raise OperationError("'fields' must be an object with at least one field")
    bad = sorted(set(fields) - SUBNET_FIELDS)
    if bad:
        raise OperationError(f"cannot change: {', '.join(bad)} (allowed: {', '.join(sorted(SUBNET_FIELDS))})")
    if family == "dhcp6" and {"router", "domain_name", "max_lease_time"} & set(fields):
        raise OperationError("router, domain_name and max_lease_time exist for dhcp4 only")
    subnet.update(copy.deepcopy(fields))


def _pool_args(doc, op):
    _only(op, {"family", "subnet_id", "start", "end"}, ("family", "subnet_id", "start", "end"))
    family = _family(op)
    return _subnet(doc, op, family), _text(op["start"], "start"), _text(op["end"], "end")


def _add_pool(doc, op):
    subnet, start, end = _pool_args(doc, op)
    subnet.setdefault("pools", []).append({"start": start, "end": end})


def _delete_pool(doc, op):
    subnet, start, end = _pool_args(doc, op)
    for pool in subnet.get("pools", []):
        if pool.get("start") == start and pool.get("end") == end:
            subnet["pools"].remove(pool)
            return
    raise OperationError(f"no pool {start} - {end} in that subnet")


def _set_global(doc, op):
    _only(op, {"fields"}, ("fields",))
    fields = op["fields"]
    if not isinstance(fields, dict) or not fields:
        raise OperationError("'fields' must be an object with at least one field")
    bad = sorted(set(fields) - GLOBAL_FIELDS)
    if bad:
        raise OperationError(f"cannot change: {', '.join(bad)} (allowed: {', '.join(sorted(GLOBAL_FIELDS))})")
    for name, value in fields.items():
        if not isinstance(value, bool):
            raise OperationError(f"'{name}' must be true or false")
        doc["global"][name] = value


OPERATIONS = {
    "add_reservation": _add_reservation, "update_reservation": _update_reservation,
    "delete_reservation": _delete_reservation, "update_subnet": _update_subnet,
    "add_pool": _add_pool, "delete_pool": _delete_pool, "set_global": _set_global,
}


def apply_operations(settings, operations):
    """``(new_settings, errors)``. *settings* is not modified. Either every operation applies or ``new_settings`` is None."""
    if not isinstance(operations, list) or not operations:
        return None, ["'operations' must be a non-empty list"]
    if len(operations) > MAX_OPERATIONS:
        return None, [f"at most {MAX_OPERATIONS} operations at once"]
    doc = copy.deepcopy(settings)
    errors = []
    for index, op in enumerate(operations, start=1):
        label = f"operation #{index}"
        if not isinstance(op, dict):
            errors.append(f"{label}: must be an object")
            continue
        handler = OPERATIONS.get(op.get("op"))
        if handler is None:
            errors.append(f"{label}: unknown op {op.get('op')!r} (known: {', '.join(sorted(OPERATIONS))})")
            continue
        try:
            handler(doc, op)
        except OperationError as exc:
            errors.append(f"{label} ({op['op']}): {exc}")
    return (None if errors else doc), errors


# ------------------------------------------------------------------ describing a change
def _subnet_label(subnet):
    return subnet.get("subnet") or f"id {subnet.get('id')}"


def _brief(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(none)"
    return "(none)" if value in (None, "") else str(value)


def describe_changes(old, new):
    """A list of plain sentences saying what *new* changes compared with *old* (both cleaned settings)."""
    out = []
    old_global, new_global = old.get("global", {}), new.get("global", {})
    for key in sorted(set(old_global) | set(new_global)):
        if old_global.get(key) != new_global.get(key):
            out.append(f"setting {key}: {_brief(old_global.get(key))} -> {_brief(new_global.get(key))}")
    for family in FAMILIES:
        old_subnets = {s.get("id"): s for s in old.get(family, {}).get("subnets", [])}
        new_subnets = {s.get("id"): s for s in new.get(family, {}).get("subnets", [])}
        for sid in old_subnets.keys() - new_subnets.keys():
            out.append(f"{family}: subnet {_subnet_label(old_subnets[sid])} removed")
        for sid in new_subnets.keys() - old_subnets.keys():
            out.append(f"{family}: subnet {_subnet_label(new_subnets[sid])} added")
        for sid in sorted(old_subnets.keys() & new_subnets.keys(), key=str):
            out += _describe_subnet(family, old_subnets[sid], new_subnets[sid])
    return out


def _describe_subnet(family, old, new):
    where = f"{family} {_subnet_label(new)}"
    out = []
    for key in sorted((set(old) | set(new)) - {"id", "pools", "reservations", "custom_options", "pd_pools"}):
        if old.get(key) != new.get(key):
            out.append(f"{where}: {key} {_brief(old.get(key))} -> {_brief(new.get(key))}")
    old_pools = {(p["start"], p["end"]) for p in old.get("pools", [])}
    new_pools = {(p["start"], p["end"]) for p in new.get("pools", [])}
    out += [f"{where}: pool {a} - {b} removed" for a, b in sorted(old_pools - new_pools)]
    out += [f"{where}: pool {a} - {b} added" for a, b in sorted(new_pools - old_pools)]
    if old.get("custom_options", []) != new.get("custom_options", []):
        out.append(f"{where}: custom options changed")
    if old.get("pd_pools", []) != new.get("pd_pools", []):
        out.append(f"{where}: prefix delegation pools changed")
    key = _ID_KEY[family]
    old_res = {r.get(key): r for r in old.get("reservations", [])}
    new_res = {r.get(key): r for r in new.get("reservations", [])}
    for ident in sorted(old_res.keys() - new_res.keys(), key=str):
        out.append(f"{where}: reservation removed: {ident} -> {old_res[ident].get('ip_address')}")
    for ident in sorted(new_res.keys() - old_res.keys(), key=str):
        r = new_res[ident]
        out.append(f"{where}: reservation added: {ident} -> {r.get('ip_address')}" + (f" ({r['hostname']})" if r.get("hostname") else ""))
    for ident in sorted(old_res.keys() & new_res.keys(), key=str):
        a, b = old_res[ident], new_res[ident]
        if a != b:
            out.append(f"{where}: reservation {ident} changed: {a.get('ip_address')} ({a.get('hostname') or '-'}) -> "
                       f"{b.get('ip_address')} ({b.get('hostname') or '-'})")
    return out
