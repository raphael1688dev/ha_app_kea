import json

import settings as cfg


def check(mutator, version=4):
    data = cfg.default_settings()
    key = "dhcp4" if version == 4 else "dhcp6"
    mutator(data[key]["subnets"][0])
    return cfg.validate(data)


def test_defaults_are_valid():
    cleaned, errors = cfg.validate(cfg.default_settings())
    assert errors == []
    assert cleaned["dhcp4"]["subnets"][0]["id"] == 1


def test_not_a_dict():
    assert cfg.validate([])[1] == ["設定格式錯誤"]


def test_bad_cidr_and_host_bits():
    assert check(lambda s: s.update(subnet="nope"))[1]
    # strict: host bits set
    assert any("網段" in e for e in check(lambda s: s.update(subnet="192.168.1.5/24"))[1])


def test_pool_outside_subnet_and_reversed():
    errs = check(lambda s: s.update(pool_start="10.0.0.1"))[1]
    assert any("不在網段內" in e for e in errs)
    errs = check(lambda s: s.update(pool_start="192.168.1.200", pool_end="192.168.1.100"))[1]
    assert any("不可大於" in e for e in errs)


def test_router_checks():
    assert check(lambda s: s.update(router="10.0.0.1"))[1]
    assert check(lambda s: s.update(router="x"))[1]
    assert check(lambda s: s.update(router=""))[1] == []


def test_dns_accepts_string_or_list_and_rejects_wrong_family():
    cleaned, errors = check(lambda s: s.update(dns="1.1.1.1, 8.8.8.8  9.9.9.9"))
    assert errors == [] and cleaned["dhcp4"]["subnets"][0]["dns"] == ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    assert check(lambda s: s.update(dns=["::1"]))[1]
    assert check(lambda s: s.update(dns=["2606:4700:4700::1111"]), version=6)[1] == []
    assert check(lambda s: s.update(dns=["1.1.1.1"]), version=6)[1]


def test_lease_time_bounds_and_bool_rejected():
    assert check(lambda s: s.update(lease_time=59))[1]
    assert check(lambda s: s.update(lease_time=31536001))[1]
    assert check(lambda s: s.update(lease_time=True))[1]
    cleaned, errors = check(lambda s: s.update(lease_time="3600"))
    assert errors == [] and cleaned["dhcp4"]["subnets"][0]["lease_time"] == 3600


def test_overlapping_subnets_and_id_assignment():
    data = cfg.default_settings()
    other = dict(data["dhcp4"]["subnets"][0], subnet="192.168.1.128/25",
                 pool_start="192.168.1.130", pool_end="192.168.1.140", id=None)
    data["dhcp4"]["subnets"].append(other)
    assert any("重疊" in e for e in cfg.validate(data)[1])

    data["dhcp4"]["subnets"][1].update(subnet="192.168.2.0/24", pool_start="192.168.2.10",
                                       pool_end="192.168.2.20", router="192.168.2.1")
    cleaned, errors = cfg.validate(data)
    assert errors == []
    assert [s["id"] for s in cleaned["dhcp4"]["subnets"]] == [1, 2]


def test_duplicate_ids_are_reassigned():
    data = cfg.default_settings()
    data["dhcp4"]["subnets"].append(dict(data["dhcp4"]["subnets"][0], subnet="192.168.2.0/24",
                                         pool_start="192.168.2.10", pool_end="192.168.2.20",
                                         router="192.168.2.1"))
    cleaned, errors = cfg.validate(data)
    assert errors == [] and sorted(s["id"] for s in cleaned["dhcp4"]["subnets"]) == [1, 2]


def test_mac_and_duid_normalisation():
    assert cfg.normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert cfg.normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert cfg.normalize_mac("aa:bb:cc") is None
    assert cfg.normalize_duid("00:01:00:01") == "00:01:00:01"
    assert cfg.normalize_duid("0001000") is None  # odd length


def test_reservations_validation():
    def add(res):
        return check(lambda s: s.update(reservations=res))

    ok = {"hw_address": "AA:BB:CC:DD:EE:01", "ip_address": "192.168.1.50", "hostname": "nas"}
    cleaned, errors = add([ok])
    assert errors == []
    assert cleaned["dhcp4"]["subnets"][0]["reservations"][0]["hw_address"] == "aa:bb:cc:dd:ee:01"

    assert add([dict(ok, hw_address="zz")])[1]
    assert add([dict(ok, ip_address="10.0.0.1")])[1]
    assert add([dict(ok, hostname="bad host!")])[1]
    assert any("重複" in e for e in add([ok, dict(ok, ip_address="192.168.1.51")])[1])  # same MAC
    assert any("重複" in e for e in add([ok, dict(ok, hw_address="aa:bb:cc:dd:ee:02")])[1])  # same IP
    assert add("nope")[1]


def test_reservation_hostname_optional():
    ok = {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50"}
    assert check(lambda s: s.update(reservations=[ok]))[1] == []


def test_dhcp6_reservation_uses_duid():
    res = {"duid": "00:01:00:01:aa:bb:cc:dd", "ip_address": "fd00::1500", "hostname": ""}
    cleaned, errors = check(lambda s: s.update(reservations=[res]), version=6)
    assert errors == []
    assert cleaned["dhcp6"]["subnets"][0]["reservations"][0]["duid"] == "00:01:00:01:aa:bb:cc:dd"


def test_render_dhcp4_shape():
    data = cfg.default_settings()
    data["dhcp4"]["subnets"][0]["reservations"] = [
        {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": ""}]
    out = cfg.render_dhcp4(data, "eth0", "/x/leases4.csv")["Dhcp4"]
    json.dumps(out)
    assert out["interfaces-config"]["interfaces"] == ["eth0"]
    assert out["lease-database"]["name"] == "/x/leases4.csv"
    subnet = out["subnet4"][0]
    assert subnet["pools"] == [{"pool": "192.168.1.100 - 192.168.1.200"}]
    assert subnet["valid-lifetime"] == 86400
    assert {"name": "routers", "data": "192.168.1.1"} in subnet["option-data"]
    assert {"name": "domain-name-servers", "data": "1.1.1.1, 8.8.8.8"} in subnet["option-data"]
    # empty hostname is dropped rather than sent as ""
    assert subnet["reservations"] == [{"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.1.50"}]
    assert out["loggers"][0]["output-options"] == [{"output": "stdout"}]


def test_render_dhcp6_shape():
    data = cfg.default_settings()
    data["dhcp6"]["subnets"][0]["reservations"] = [
        {"duid": "00:01:00:01", "ip_address": "fd00::1500", "hostname": "h"}]
    out = cfg.render_dhcp6(data, "eth0", "/x/leases6.csv")["Dhcp6"]
    subnet = out["subnet6"][0]
    assert subnet["preferred-lifetime"] == 43200
    assert {"name": "dns-servers", "data": "2606:4700:4700::1111, 2001:4860:4860::8888"} in subnet["option-data"]
    assert subnet["reservations"] == [
        {"duid": "00:01:00:01", "ip-addresses": ["fd00::1500"], "hostname": "h"}]


def test_render_omits_empty_router_and_dns():
    data = cfg.default_settings()
    s = data["dhcp4"]["subnets"][0]
    s["router"], s["dns"] = "", []
    assert cfg.render_dhcp4(data, "eth0", "/x")["Dhcp4"]["subnet4"][0]["option-data"] == []
