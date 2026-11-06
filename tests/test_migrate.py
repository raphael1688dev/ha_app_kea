"""Converting the options of the old DHCP server app. All data here is made up."""
import pytest

import migrate
import settings as cfg

OLD = """
default_lease: 3600
max_lease: 7200
domain: example.lan
dns: [192.0.2.53, 192.0.2.54]
ntp: [0.pool.example, 192.0.2.123]
networks:
  - subnet: 192.0.2.0
    netmask: 255.255.255.0
    broadcast: 192.0.2.255
    gateway: 192.0.2.1
    interface: eth9
    range_start: 192.0.2.100
    range_end: 192.0.2.150
hosts:
  - {name: printer, mac: "AA:BB:CC:DD:EE:01", ip: 192.0.2.10}
  - {name: fan_01, mac: "aa:bb:cc:dd:ee:02", ip: 192.0.2.11}
  - {name: laptop, mac: "b0:a7:32:6:a5:18", ip: 192.0.2.12}
  - {name: laptop-wifi, mac: "b0:a7:32:6:a5:19", ip: 192.0.2.12}
"""


def run(text=OLD, **kw):
    return migrate.convert(migrate.parse_text(text), **kw)


def levels(result, level):
    return [i["text"] for i in result["report"] if i["level"] == level]


def test_the_common_case_converts_completely():
    result = run()
    assert result["ok"] and result["complete"] and result["counts"]["error"] == 0
    s = result["settings"]
    assert s["global"] == {"interface": "eth9", "enable_dhcp6": False, "authoritative": True, "allow_shared_ips": True}
    net = s["dhcp4"]["subnets"][0]
    assert net["subnet"] == "192.0.2.0/24" and net["router"] == "192.0.2.1"
    assert net["pools"] == [{"start": "192.0.2.100", "end": "192.0.2.150"}]
    assert net["dns"] == ["192.0.2.53", "192.0.2.54"] and net["domain_name"] == "example.lan"
    assert net["ntp_servers"] == ["0.pool.example", "192.0.2.123"]
    assert (net["lease_time"], net["max_lease_time"]) == (3600, 7200)
    assert [r["hw_address"] for r in net["reservations"]][:3] == [
        "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "b0:a7:32:06:a5:18"]
    assert net["reservations"][1]["hostname"] == "fan_01"            # underscores survive
    assert any("share one IP" in t for t in levels(result, "info"))
    assert any("authoritative" in t for t in levels(result, "info"))


def test_the_result_is_a_valid_settings_file_that_renders():
    s = run()["settings"]
    assert cfg.validate(s)[1] == []
    rendered = cfg.render_dhcp4(s, "/x", {"0.pool.example": ["198.51.100.1"]})["Dhcp4"]
    assert rendered["authoritative"] is True and rendered["ip-reservations-unique"] is False


def test_sharing_is_only_switched_on_when_needed():
    text = OLD.replace('ip: 192.0.2.12}\n', 'ip: 192.0.2.12}\n', 1).replace(
        '"b0:a7:32:6:a5:19", ip: 192.0.2.12', '"b0:a7:32:6:a5:19", ip: 192.0.2.13')
    assert run(text)["settings"]["global"]["allow_shared_ips"] is False


@pytest.mark.parametrize("host, fragment", [
    ('{name: a, mac: "zz", ip: 192.0.2.20}', "MAC address"),
    ('{name: a, mac: "aa:bb:cc:dd:ee:09", ip: 999.1.1.1}', "IP address"),
    ('{name: a, mac: "aa:bb:cc:dd:ee:09", ip: 203.0.113.9}', "not inside any converted network"),
    ('{name: a, mac: "AA:BB:CC:DD:EE:01", ip: 192.0.2.20}', "already reserved"),
])
def test_bad_hosts_are_skipped_and_reported_but_the_rest_is_kept(host, fragment):
    result = run(OLD + f"  - {host}\n")
    assert result["ok"] and not result["complete"]
    assert any(fragment in t for t in levels(result, "error"))
    assert len(result["settings"]["dhcp4"]["subnets"][0]["reservations"]) == 4
    assert any("4 of 5 devices" in t for t in levels(result, "info"))


def test_an_exact_duplicate_is_kept_once_and_is_not_an_error():
    result = run(OLD + '  - {name: printer-again, mac: "aa-bb-cc-dd-ee-01", ip: 192.0.2.10}\n')
    reservations = result["settings"]["dhcp4"]["subnets"][0]["reservations"]
    assert result["complete"] and result["counts"]["error"] == 0 and len(reservations) == 4
    assert [r["hostname"] for r in reservations if r["hw_address"] == "aa:bb:cc:dd:ee:01"] == ["printer"]   # first one wins
    info = levels(result, "info")
    assert any("duplicate is ignored" in t for t in info)
    assert any("4 of 4 devices carried over (1 exact duplicate ignored)" in t for t in info)


def test_the_same_mac_with_another_address_is_still_an_error():
    result = run(OLD + '  - {name: printer-again, mac: "aa:bb:cc:dd:ee:01", ip: 192.0.2.99}\n')
    assert not result["complete"] and any("already reserved" in t for t in levels(result, "error"))
    assert not any("duplicate" in t for t in levels(result, "info"))


def test_an_invalid_device_name_is_dropped_but_the_reservation_stays():
    result = run(OLD + '  - {name: "bad name!", mac: "aa:bb:cc:dd:ee:09", ip: 192.0.2.20}\n')
    res = result["settings"]["dhcp4"]["subnets"][0]["reservations"][-1]
    assert res["hostname"] == "" and res["ip_address"] == "192.0.2.20"
    assert any("bad name!" in t for t in levels(result, "warn"))


def test_options_that_are_not_carried_over_are_named():
    result = run(OLD + "leases_dir: /x\n")
    assert any("leases_dir" in t for t in levels(result, "warn"))


def test_a_broadcast_address_that_is_wrong_is_mentioned():
    result = run(OLD.replace("broadcast: 192.0.2.255", "broadcast: 192.0.2.254"))
    assert any("differs" in t for t in levels(result, "warn"))


def test_two_interfaces_use_the_first_and_say_so():
    clean = OLD.replace("hosts:", """  - subnet: 198.51.100.0
    netmask: 255.255.255.0
    gateway: 198.51.100.1
    interface: eth8
    range_start: 198.51.100.10
    range_end: 198.51.100.20
hosts:""", 1)
    result = run(clean)
    assert result["settings"]["global"]["interface"] == "eth9"
    assert any("several interfaces" in t for t in levels(result, "warn"))
    assert len(result["settings"]["dhcp4"]["subnets"]) == 2


def test_max_lease_smaller_than_default_is_dropped_with_a_warning():
    result = run(OLD.replace("max_lease: 7200", "max_lease: 600"))
    assert result["settings"]["dhcp4"]["subnets"][0]["max_lease_time"] is None
    assert any("max_lease" in t for t in levels(result, "warn"))


@pytest.mark.parametrize("value", [None, [], "text", {"hosts": []}, {"networks": [{"subnet": "x"}]},
                                   {"networks": [{"subnet": "192.0.2.0", "netmask": "255.255.255.0",
                                                  "range_start": "192.0.2.1", "range_end": "203.0.113.1"}]}])
def test_unusable_input_is_refused_with_a_reason(value):
    result = migrate.convert(value)
    assert not result["ok"] and result["settings"] is None and result["counts"]["error"] >= 1


def test_json_is_accepted_and_aliases_are_not():
    assert migrate.parse_text('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError, match="aliases"):
        migrate.parse_text("a: &x [1]\nb: *x\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        migrate.parse_text("a: [1\n")


def test_a_large_list_is_reported_compactly():
    hosts = "".join(f"  - {{name: h{i}, mac: zz, ip: 192.0.2.20}}\n" for i in range(400))
    result = run(OLD + hosts)
    assert len(result["report"]) <= migrate.MAX_REPORT_LINES + 1 and result["counts"]["error"] == 400
    assert "more" in result["report"][-1]["text"]
