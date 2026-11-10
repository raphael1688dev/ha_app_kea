import copy

import pytest

import ops
import settings as cfg


@pytest.fixture
def doc():
    s = cfg.default_settings("eth0", True)
    s["dhcp4"]["subnets"][0]["reservations"] = [
        {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": "nas"}]
    s["dhcp6"]["subnets"][0]["reservations"] = [
        {"duid": "00:01:00:01:aa:bb:cc:dd:ee:ff:00:11", "ip_address": "fd00::1500", "hostname": "tv"}]
    cleaned, errors = cfg.validate(s)
    assert errors == []
    return cleaned


def run(doc, *operations):
    new, errors = ops.apply_operations(doc, list(operations))
    if new is not None:
        cleaned, verrors = cfg.validate(new)
        assert verrors == [], verrors
        return cleaned, errors
    return None, errors


def test_the_input_is_not_modified(doc):
    before = copy.deepcopy(doc)
    ops.apply_operations(doc, [{"op": "delete_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:01"}])
    assert doc == before


def test_add_update_and_delete_a_reservation(doc):
    new, errors = run(doc, {"op": "add_reservation", "family": "dhcp4", "subnet_id": 1, "identifier": "AA-BB-CC-DD-EE-02",
                            "ip_address": "192.168.1.51", "hostname": "printer"})
    assert errors == [] and {"hw_address": "aa:bb:cc:dd:ee:02", "ip_address": "192.168.1.51", "hostname": "printer"} in \
        new["dhcp4"]["subnets"][0]["reservations"]
    new, _ = run(new, {"op": "update_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:02",
                       "ip_address": "192.168.1.52"})
    assert [r["ip_address"] for r in new["dhcp4"]["subnets"][0]["reservations"]] == ["192.168.1.50", "192.168.1.52"]
    new, _ = run(new, {"op": "delete_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:02"})
    assert [r["hw_address"] for r in new["dhcp4"]["subnets"][0]["reservations"]] == ["aa:bb:cc:dd:ee:01"]


def test_ipv6_reservations_use_the_duid(doc):
    new, errors = run(doc, {"op": "add_reservation", "family": "dhcp6", "subnet_id": 1, "identifier": "00:01:00:01:11:22:33:44:55:66:77:88",
                            "ip_address": "fd00::1501"})
    assert errors == [] and new["dhcp6"]["subnets"][0]["reservations"][-1]["duid"] == "00:01:00:01:11:22:33:44:55:66:77:88"


def test_operations_run_in_order_and_one_failure_applies_nothing(doc):
    good = {"op": "add_pool", "family": "dhcp4", "subnet_id": 1, "start": "192.168.1.210", "end": "192.168.1.220"}
    bad = {"op": "delete_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:77"}
    new, errors = ops.apply_operations(doc, [good, bad])
    assert new is None and len(errors) == 1 and "operation #2" in errors[0] and "no reservation" in errors[0]


@pytest.mark.parametrize("operation, fragment", [
    ({"op": "nope"}, "unknown op"),
    ("text", "must be an object"),
    ({"op": "add_reservation", "family": "dhcp9", "subnet_id": 1, "identifier": "a", "ip_address": "b"}, "'family'"),
    ({"op": "add_reservation", "family": "dhcp4", "subnet_id": 7, "identifier": "aa:bb:cc:dd:ee:02", "ip_address": "x"}, "no subnet with id 7"),
    ({"op": "add_reservation", "family": "dhcp4", "subnet_id": "1", "identifier": "aa:bb:cc:dd:ee:02", "ip_address": "x"}, "whole number"),
    ({"op": "add_reservation", "family": "dhcp4", "subnet_id": 1, "identifier": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.60"}, "already has a reservation"),
    ({"op": "add_reservation", "family": "dhcp4", "subnet_id": 1, "identifier": "aa:bb:cc:dd:ee:02"}, "missing field"),
    ({"op": "add_reservation", "family": "dhcp4", "subnet_id": 1, "identifier": "aa:bb:cc:dd:ee:02", "ip_address": "192.168.1.60", "extra": 1}, "unknown field"),
    ({"op": "update_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:01"}, "give 'ip_address'"),
    ({"op": "update_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:09", "hostname": "x"}, "no reservation"),
    ({"op": "update_subnet", "family": "dhcp4", "subnet_id": 1, "fields": {"subnet": "10.0.0.0/8"}}, "cannot change: subnet"),
    ({"op": "update_subnet", "family": "dhcp4", "subnet_id": 1, "fields": {}}, "at least one field"),
    ({"op": "update_subnet", "family": "dhcp6", "subnet_id": 1, "fields": {"router": "x"}}, "dhcp4 only"),
    ({"op": "delete_pool", "family": "dhcp4", "subnet_id": 1, "start": "1.1.1.1", "end": "1.1.1.2"}, "no pool"),
    ({"op": "set_global", "fields": {"interface": "eth1"}}, "cannot change: interface"),
    ({"op": "set_global", "fields": {"authoritative": "yes"}}, "true or false"),
])
def test_bad_operations_are_refused_with_a_reason(doc, operation, fragment):
    new, errors = ops.apply_operations(doc, [operation])
    assert new is None and any(fragment in e for e in errors), errors


def test_limits_and_shape_of_the_list(doc):
    assert ops.apply_operations(doc, [])[0] is None
    assert ops.apply_operations(doc, "x")[0] is None
    many = [{"op": "set_global", "fields": {"authoritative": True}}] * (ops.MAX_OPERATIONS + 1)
    assert "at most" in ops.apply_operations(doc, many)[1][0]


def test_subnet_fields_pools_and_globals(doc):
    new, errors = run(
        doc,
        {"op": "update_subnet", "family": "dhcp4", "subnet_id": 1,
         "fields": {"dns": ["9.9.9.9"], "lease_time": 7200, "max_lease_time": 14400, "ntp_servers": "pool.test"}},
        {"op": "add_pool", "family": "dhcp4", "subnet_id": 1, "start": "192.168.1.210", "end": "192.168.1.220"},
        {"op": "delete_pool", "family": "dhcp4", "subnet_id": 1, "start": "192.168.1.100", "end": "192.168.1.200"},
        {"op": "set_global", "fields": {"authoritative": True, "allow_shared_ips": True}})
    assert errors == []
    s = new["dhcp4"]["subnets"][0]
    assert (s["dns"], s["lease_time"], s["max_lease_time"], s["ntp_servers"]) == (["9.9.9.9"], 7200, 14400, ["pool.test"])
    assert s["pools"] == [{"start": "192.168.1.210", "end": "192.168.1.220"}]
    assert new["global"]["authoritative"] and new["global"]["allow_shared_ips"]


def test_an_operation_cannot_make_invalid_settings_pass_validation(doc):
    new, errors = ops.apply_operations(doc, [{"op": "add_reservation", "family": "dhcp4", "subnet_id": 1,
                                              "identifier": "aa:bb:cc:dd:ee:02", "ip_address": "10.9.9.9"}])
    assert errors == []                              # the operation itself is fine ...
    _, verrors = cfg.validate(new)
    assert any("outside the subnet" in e for e in verrors)      # ... the normal validation catches the value


def test_describe_changes_in_plain_sentences(doc):
    new, _ = run(
        doc,
        {"op": "add_reservation", "family": "dhcp4", "subnet_id": 1, "identifier": "aa:bb:cc:dd:ee:02",
         "ip_address": "192.168.1.51", "hostname": "printer"},
        {"op": "update_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.55"},
        {"op": "delete_reservation", "family": "dhcp6", "identifier": "00:01:00:01:aa:bb:cc:dd:ee:ff:00:11"},
        {"op": "update_subnet", "family": "dhcp4", "subnet_id": 1, "fields": {"lease_time": 7200}},
        {"op": "add_pool", "family": "dhcp4", "subnet_id": 1, "start": "192.168.1.210", "end": "192.168.1.220"},
        {"op": "set_global", "fields": {"authoritative": True}})
    text = "\n".join(ops.describe_changes(doc, new))
    for fragment in ("reservation added: aa:bb:cc:dd:ee:02 -> 192.168.1.51 (printer)",
                     "reservation aa:bb:cc:dd:ee:01 changed: 192.168.1.50 (nas) -> 192.168.1.55 (nas)",
                     "dhcp6 fd00::/64: reservation removed", "lease_time 86400 -> 7200",
                     "pool 192.168.1.210 - 192.168.1.220 added", "setting authoritative: False -> True"):
        assert fragment in text, fragment
    assert ops.describe_changes(doc, doc) == []


def test_describe_says_what_is_in_custom_options_and_prefix_pools(doc):
    """A person approves a proposal on this text alone, so it must show the values, not just "changed"."""
    new = copy.deepcopy(doc)
    new["dhcp6"]["subnets"] = []
    doc["dhcp4"]["subnets"][0]["custom_options"] = [{"name": "tftp-server-name", "data": "192.168.1.5"},
                                                    {"name": "boot-file-name", "data": "old.0"}]
    new["dhcp4"]["subnets"][0]["custom_options"] = [{"name": "boot-file-name", "data": "evil.0"},
                                                    {"name": "wpad-url", "data": "http://198.51.100.9/wpad.dat"}]
    lines = ops.describe_changes(doc, new)
    assert any("subnet fd00::/64 removed" in l for l in lines)
    assert any("custom option boot-file-name: old.0 -> evil.0" in l for l in lines)
    assert any("custom option wpad-url added: http://198.51.100.9/wpad.dat" in l for l in lines)
    assert any("custom option tftp-server-name removed (was 192.168.1.5)" in l for l in lines)
    v6 = copy.deepcopy(doc)
    v6["dhcp6"]["subnets"][0]["pd_pools"] = [{"prefix": "2001:db8:1::", "prefix_len": 48, "delegated_len": 64}]
    assert any("prefix delegation pool 2001:db8:1::/48 (delegating /64) added" in l for l in ops.describe_changes(doc, v6))


def test_an_added_subnet_is_described_with_everything_in_it(doc):
    new = copy.deepcopy(doc)
    new["dhcp4"]["subnets"].append({
        "id": 2, "subnet": "10.20.0.0/24", "router": "10.20.0.1", "dns": ["198.51.100.53"], "domain_name": "", "domain_search": [],
        "ntp_servers": [], "custom_options": [{"name": "boot-file-name", "data": "x.0"}], "lease_time": 600, "max_lease_time": None,
        "pools": [{"start": "10.20.0.10", "end": "10.20.0.99"}],
        "reservations": [{"hw_address": "aa:bb:cc:dd:ee:77", "ip_address": "10.20.0.5", "hostname": "cam"}]})
    text = "\n".join(ops.describe_changes(doc, new))
    for fragment in ("dhcp4: subnet 10.20.0.0/24 added", "dns (none) -> 198.51.100.53", "router (none) -> 10.20.0.1",
                     "lease_time (none) -> 600", "pool 10.20.0.10 - 10.20.0.99 added", "custom option boot-file-name added: x.0",
                     "reservation added: aa:bb:cc:dd:ee:77 -> 10.20.0.5 (cam)"):
        assert fragment in text, fragment
    assert "domain_name" not in text and "max_lease_time" not in text          # empty fields are not listed
