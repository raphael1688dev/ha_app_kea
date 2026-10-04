import json

import settings as cfg


def check(mutator, version=4):
    data = cfg.default_settings()
    key = "dhcp4" if version == 4 else "dhcp6"
    mutator(data[key]["subnets"][0])
    return cfg.validate(data)


def errs(mutator, version=4):
    return check(mutator, version)[1]


def test_defaults_are_valid():
    cleaned, errors = cfg.validate(cfg.default_settings())
    assert errors == []
    assert cleaned["dhcp4"]["subnets"][0]["id"] == 1
    assert cleaned["global"] == {"interface": "eth0", "enable_dhcp6": False}


def test_not_a_dict():
    assert cfg.validate([])[1] == ["invalid settings format"]


# ------------------------------------------------------------------ global
def test_global_validation_and_fallback():
    data = cfg.default_settings()
    del data["global"]
    cleaned, errors = cfg.validate(data, cfg.default_global("br0", True))
    assert errors == [] and cleaned["global"] == {"interface": "br0", "enable_dhcp6": True}

    data["global"] = {"interface": "bad name!", "enable_dhcp6": "yes"}
    errors = cfg.validate(data)[1]
    assert any("interface" in e for e in errors) and any("enable_dhcp6" in e for e in errors)

    data["global"] = {"interface": "x" * 16}
    assert cfg.validate(data)[1]
    data["global"] = {"interface": "eth0.10"}
    assert cfg.validate(data)[1] == []
    data["global"] = "nope"
    assert cfg.validate(data)[1]


# --------------------------------------------------------------- migration
def test_v1_settings_are_migrated():
    v1 = {
        "version": 1,
        "dhcp4": {"subnets": [{
            "id": 3, "subnet": "10.0.0.0/24", "pool_start": "10.0.0.10", "pool_end": "10.0.0.20",
            "router": "10.0.0.1", "dns": ["9.9.9.9"], "lease_time": 600, "reservations": []}]},
        "dhcp6": {"subnets": []},
    }
    cleaned, errors = cfg.validate(v1, cfg.default_global("eth5"))
    assert errors == []
    sub = cleaned["dhcp4"]["subnets"][0]
    assert sub["pools"] == [{"start": "10.0.0.10", "end": "10.0.0.20"}] and sub["id"] == 3
    assert cleaned["global"]["interface"] == "eth5" and cleaned["version"] == 2
    assert "pool_start" in v1["dhcp4"]["subnets"][0]  # input not mutated


# -------------------------------------------------------------------- core
def test_bad_cidr_and_host_bits():
    assert errs(lambda s: s.update(subnet="nope"))
    assert any("subnet" in e for e in errs(lambda s: s.update(subnet="192.168.1.5/24")))


def test_router_checks():
    assert errs(lambda s: s.update(router="10.0.0.1"))
    assert errs(lambda s: s.update(router="x"))
    assert errs(lambda s: s.update(router="")) == []


def test_dns_accepts_string_or_list_and_rejects_wrong_family():
    cleaned, errors = check(lambda s: s.update(dns="1.1.1.1, 8.8.8.8  9.9.9.9"))
    assert errors == [] and cleaned["dhcp4"]["subnets"][0]["dns"] == ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    assert errs(lambda s: s.update(dns=["::1"]))
    assert errs(lambda s: s.update(dns=["2606:4700:4700::1111"]), version=6) == []
    assert errs(lambda s: s.update(dns=["1.1.1.1"]), version=6)


def test_lease_time_bounds_and_bool_rejected():
    assert errs(lambda s: s.update(lease_time=59))
    assert errs(lambda s: s.update(lease_time=31536001))
    assert errs(lambda s: s.update(lease_time=True))
    cleaned, errors = check(lambda s: s.update(lease_time="3600"))
    assert errors == [] and cleaned["dhcp4"]["subnets"][0]["lease_time"] == 3600


def _second_subnet(data, **over):
    sub = dict(data["dhcp4"]["subnets"][0], subnet="192.168.2.0/24",
               pools=[{"start": "192.168.2.10", "end": "192.168.2.20"}], router="192.168.2.1", id=None)
    sub.update(over)
    data["dhcp4"]["subnets"].append(sub)


def test_overlapping_subnets_and_id_assignment():
    data = cfg.default_settings()
    _second_subnet(data, subnet="192.168.1.128/25",
                   pools=[{"start": "192.168.1.130", "end": "192.168.1.140"}])
    assert any("overlap" in e for e in cfg.validate(data)[1])

    data = cfg.default_settings()
    _second_subnet(data)
    cleaned, errors = cfg.validate(data)
    assert errors == [] and [s["id"] for s in cleaned["dhcp4"]["subnets"]] == [1, 2]


def test_duplicate_ids_are_reassigned():
    data = cfg.default_settings()
    _second_subnet(data, id=1)
    cleaned, errors = cfg.validate(data)
    assert errors == [] and sorted(s["id"] for s in cleaned["dhcp4"]["subnets"]) == [1, 2]


# ------------------------------------------------------------------- pools
def test_pool_rules():
    one = lambda start, end: (lambda s: s.update(pools=[{"start": start, "end": end}]))
    assert errs(one("10.0.0.1", "192.168.1.9"))
    assert any("outside the subnet" in e for e in errs(one("10.0.0.1", "10.0.0.2")))
    assert any("greater than" in e for e in errs(one("192.168.1.200", "192.168.1.100")))
    assert errs(one("bogus", "192.168.1.9"))
    assert errs(lambda s: s.update(pools="x"))


def test_multiple_pools_and_overlap():
    two = [{"start": "192.168.1.10", "end": "192.168.1.20"},
           {"start": "192.168.1.100", "end": "192.168.1.110"}]
    cleaned, errors = check(lambda s: s.update(pools=two))
    assert errors == [] and len(cleaned["dhcp4"]["subnets"][0]["pools"]) == 2

    overlapping = [{"start": "192.168.1.10", "end": "192.168.1.50"},
                   {"start": "192.168.1.50", "end": "192.168.1.60"}]
    assert any("overlap" in e for e in errs(lambda s: s.update(pools=overlapping)))


def test_zero_pools_allowed_for_reservation_only_subnets():
    assert errs(lambda s: s.update(pools=[])) == []


# ----------------------------------------------------------------- options
def test_domain_options():
    cleaned, errors = check(lambda s: s.update(
        domain_name="home.lan.", domain_search="a.lan, b.lan", ntp_servers="192.168.1.2 192.168.1.3"))
    assert errors == []
    sub = cleaned["dhcp4"]["subnets"][0]
    assert sub["domain_name"] == "home.lan" and sub["domain_search"] == ["a.lan", "b.lan"]
    assert sub["ntp_servers"] == ["192.168.1.2", "192.168.1.3"]

    assert errs(lambda s: s.update(domain_name="bad domain"))
    assert errs(lambda s: s.update(domain_search=["ok.lan", "-bad-"]))
    assert errs(lambda s: s.update(ntp_servers=["::1"]))
    assert errs(lambda s: s.update(domain_search="x.lan"), version=6) == []


def test_custom_options():
    ok = [{"name": "TFTP-Server-Name", "data": "192.168.1.5"}, {"name": "boot-file-name", "data": "pxe.0"}]
    cleaned, errors = check(lambda s: s.update(custom_options=ok))
    assert errors == []
    assert cleaned["dhcp4"]["subnets"][0]["custom_options"][0]["name"] == "tftp-server-name"

    assert errs(lambda s: s.update(custom_options=[{"name": "", "data": "x"}]))
    assert errs(lambda s: s.update(custom_options=[{"name": "tftp-server-name", "data": ""}]))
    assert errs(lambda s: s.update(custom_options=[{"name": "bad name", "data": "x"}]))
    assert any("own field" in e for e in errs(lambda s: s.update(custom_options=[{"name": "routers", "data": "x"}])))
    assert any("duplicated" in e for e in errs(lambda s: s.update(custom_options=ok + ok[:1])))
    assert errs(lambda s: s.update(custom_options="nope"))
    assert errs(lambda s: s.update(custom_options=[{"name": "x1", "data": "a\nb"}]))


def test_pd_pools():
    good = [{"prefix": "2001:db8:1::", "prefix_len": "48", "delegated_len": "64"}]
    cleaned, errors = check(lambda s: s.update(pd_pools=good), version=6)
    assert errors == []
    assert cleaned["dhcp6"]["subnets"][0]["pd_pools"] == [
        {"prefix": "2001:db8:1::", "prefix_len": 48, "delegated_len": 64}]

    bad = lambda **kw: errs(lambda s: s.update(pd_pools=[dict(good[0], **kw)]), version=6)
    assert bad(prefix="nope")
    assert bad(prefix="2001:db8:1::1")                       # host bits set
    assert bad(prefix_len="0") and bad(prefix_len="129") and bad(delegated_len="x")
    assert any("smaller" in e for e in bad(delegated_len="40"))
    two = good + [{"prefix": "2001:db8:1:8000::", "prefix_len": 49, "delegated_len": 64}]
    assert any("overlap" in e for e in errs(lambda s: s.update(pd_pools=two), version=6))
    assert errs(lambda s: s.update(pd_pools="x"), version=6)


# ------------------------------------------------------------ reservations
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
    assert any("duplicated" in e for e in add([ok, dict(ok, ip_address="192.168.1.51")])[1])  # same MAC
    assert any("duplicated" in e for e in add([ok, dict(ok, hw_address="aa:bb:cc:dd:ee:02")])[1])  # same IP
    assert add("nope")[1]


def test_reservation_hostname_optional():
    ok = {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50"}
    assert errs(lambda s: s.update(reservations=[ok])) == []


def test_dhcp6_reservation_uses_duid():
    res = {"duid": "00:01:00:01:aa:bb:cc:dd", "ip_address": "fd00::1500", "hostname": ""}
    cleaned, errors = check(lambda s: s.update(reservations=[res]), version=6)
    assert errors == []
    assert cleaned["dhcp6"]["subnets"][0]["reservations"][0]["duid"] == "00:01:00:01:aa:bb:cc:dd"


# --------------------------------------------------------------- rendering
def test_render_dhcp4_shape():
    data = cfg.default_settings("eth7")
    sub = data["dhcp4"]["subnets"][0]
    sub["reservations"] = [{"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": ""}]
    sub["pools"].append({"start": "192.168.1.20", "end": "192.168.1.30"})
    sub.update(domain_name="home.lan", domain_search=["a.lan", "b.lan"], ntp_servers=["192.168.1.2"],
               custom_options=[{"name": "tftp-server-name", "data": "192.168.1.5"}])
    out = cfg.render_dhcp4(data, "/x/leases4.csv")["Dhcp4"]
    json.dumps(out)
    assert out["interfaces-config"]["interfaces"] == ["eth7"]
    assert out["lease-database"]["name"] == "/x/leases4.csv"
    subnet = out["subnet4"][0]
    assert subnet["pools"] == [{"pool": "192.168.1.100 - 192.168.1.200"},
                               {"pool": "192.168.1.20 - 192.168.1.30"}]
    assert subnet["valid-lifetime"] == 86400
    opts = {o["name"]: o["data"] for o in subnet["option-data"]}
    assert opts == {
        "routers": "192.168.1.1", "domain-name-servers": "1.1.1.1, 8.8.8.8",
        "domain-name": "home.lan", "domain-search": "a.lan, b.lan",
        "ntp-servers": "192.168.1.2", "tftp-server-name": "192.168.1.5"}
    # empty hostname is dropped rather than sent as ""
    assert subnet["reservations"] == [{"hw-address": "aa:bb:cc:dd:ee:01", "ip-address": "192.168.1.50"}]
    assert out["loggers"][0]["output-options"] == [{"output": "stdout"}]


def test_render_dhcp6_shape():
    data = cfg.default_settings()
    sub = data["dhcp6"]["subnets"][0]
    sub["reservations"] = [{"duid": "00:01:00:01", "ip_address": "fd00::1500", "hostname": "h"}]
    sub["pd_pools"] = [{"prefix": "2001:db8:1::", "prefix_len": 48, "delegated_len": 64}]
    sub["domain_search"] = ["a.lan"]
    out = cfg.render_dhcp6(data, "/x/leases6.csv")["Dhcp6"]
    subnet = out["subnet6"][0]
    assert subnet["preferred-lifetime"] == 43200
    opts = {o["name"]: o["data"] for o in subnet["option-data"]}
    assert opts == {"dns-servers": "2606:4700:4700::1111, 2001:4860:4860::8888", "domain-search": "a.lan"}
    assert subnet["pd-pools"] == [{"prefix": "2001:db8:1::", "prefix-len": 48, "delegated-len": 64}]
    assert subnet["reservations"] == [
        {"duid": "00:01:00:01", "ip-addresses": ["fd00::1500"], "hostname": "h"}]


def test_render_omits_empty_options_and_pd_pools():
    data = cfg.default_settings()
    s = data["dhcp4"]["subnets"][0]
    s["router"], s["dns"] = "", []
    assert cfg.render_dhcp4(data, "/x")["Dhcp4"]["subnet4"][0]["option-data"] == []
    sub6 = data["dhcp6"]["subnets"][0]
    sub6["dns"] = []
    rendered = cfg.render_dhcp6(data, "/x")["Dhcp6"]["subnet6"][0]
    assert rendered["option-data"] == [] and "pd-pools" not in rendered


def test_malformed_sections_are_errors_not_crashes():
    for bad in ({"dhcp4": "x"}, {"dhcp4": [1]}, {"dhcp6": 5}, {"dhcp4": None, "dhcp6": []}):
        data = cfg.default_settings()
        data.update(bad)
        cleaned, errors = cfg.validate(data)                 # used to raise AttributeError
        assert cleaned is not None
        if bad.get("dhcp4") not in (None,) or bad.get("dhcp6") not in (None, []):
            assert any("invalid format" in e for e in errors), bad
