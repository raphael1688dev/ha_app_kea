import json
import os
import threading
import time

import pytest

import leases as lease_files
import settings as cfg
from manager import KeaManager


def wait_for(cond, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def log_text(mgr, **kw):
    return "\n".join(e["line"] for e in mgr.get_logs(**kw)["entries"])


@pytest.fixture
def mgr(tmp_path, fake_kea):
    m = KeaManager({"interface": "eth0"}, str(tmp_path / "etc"), str(tmp_path / "data"))
    m.settle_seconds = 0.4        # production waits 1.5 s after an apply
    m.reload_grace = 0            # production does not signal a daemon that started less than 2 s ago
    yield m
    m.shutdown()


def pid(m, name="dhcp4"):
    return m.status()["daemons"][name]["pid"]


def test_startup_creates_default_settings_and_runs_dhcp4_only(mgr):
    mgr.startup()
    st = mgr.status()
    assert st["interface"] == "eth0" and st["enable_dhcp6"] is False
    assert st["daemons"]["dhcp4"]["running"] is True
    assert st["daemons"]["dhcp6"]["running"] is False and not st["daemons"]["dhcp6"]["enabled"]
    conf = json.load(open(os.path.join(mgr.conf_dir, "kea-dhcp4.conf")))
    assert conf["Dhcp4"]["interfaces-config"]["interfaces"] == ["eth0"]
    assert conf["Dhcp4"]["lease-database"]["name"] == os.path.join(mgr.data_dir, "kea-leases4.csv")


def test_dhcp6_started_when_enabled_in_options(tmp_path, fake_kea):
    m = KeaManager({"interface": "eth0", "enable_dhcp6": True}, str(tmp_path / "e"), str(tmp_path / "d"))
    try:
        m.startup()
        assert m.status()["daemons"]["dhcp6"]["running"] is True
    finally:
        m.shutdown()


def test_settings_file_global_overrides_options(tmp_path, fake_kea):
    data = tmp_path / "d"
    data.mkdir()
    saved = cfg.default_settings("br1", True)
    (data / "settings.json").write_text(json.dumps(saved))
    m = KeaManager({"interface": "eth0", "enable_dhcp6": False}, str(tmp_path / "e"), str(data))
    try:
        m.startup()
        st = m.status()
        assert st["interface"] == "br1" and st["daemons"]["dhcp6"]["running"] is True
    finally:
        m.shutdown()


def test_v1_settings_file_is_migrated_on_load(tmp_path, fake_kea):
    data = tmp_path / "d"
    data.mkdir()
    v1 = {"version": 1, "dhcp4": {"subnets": [{
        "id": 1, "subnet": "10.0.0.0/24", "pool_start": "10.0.0.10", "pool_end": "10.0.0.20",
        "router": "10.0.0.1", "dns": [], "lease_time": 600, "reservations": []}]},
        "dhcp6": {"subnets": []}}
    (data / "settings.json").write_text(json.dumps(v1))
    m = KeaManager({"interface": "eth3"}, str(tmp_path / "e"), str(data))
    try:
        m.startup()
        conf = json.load(open(os.path.join(m.conf_dir, "kea-dhcp4.conf")))["Dhcp4"]
        assert conf["subnet4"][0]["pools"] == [{"pool": "10.0.0.10 - 10.0.0.20"}]
        assert conf["interfaces-config"]["interfaces"] == ["eth3"]
    finally:
        m.shutdown()


def test_apply_reloads_running_daemon_without_restart(mgr, good):
    mgr.startup()
    old_pid = pid(mgr)
    good["dhcp4"]["subnets"][0]["lease_time"] = 7200
    result = mgr.apply(good)
    assert result["ok"] is True and result["action"] == "reloaded"
    assert pid(mgr) == old_pid                       # same process: no downtime
    assert wait_for(lambda: "SIGHUP received" in log_text(mgr, source="dhcp4"))
    saved = json.load(open(mgr.settings_path))
    assert saved["dhcp4"]["subnets"][0]["lease_time"] == 7200


def test_apply_interface_change_restarts(mgr, good):
    mgr.startup()
    old_pid = pid(mgr)
    good["global"]["interface"] = "eth1"
    result = mgr.apply(good)
    assert result["ok"] and result["action"] == "restarted"
    assert pid(mgr) != old_pid and result["status"]["interface"] == "eth1"
    conf = json.load(open(os.path.join(mgr.conf_dir, "kea-dhcp4.conf")))
    assert conf["Dhcp4"]["interfaces-config"]["interfaces"] == ["eth1"]


def test_apply_can_enable_and_disable_dhcp6(mgr, good):
    mgr.startup()
    v4_pid = pid(mgr)
    good["global"]["enable_dhcp6"] = True
    assert mgr.apply(good)["ok"]
    st = mgr.status()
    assert st["daemons"]["dhcp6"]["running"] and st["enable_dhcp6"]
    assert pid(mgr) == v4_pid                        # IPv4 untouched

    good["global"]["enable_dhcp6"] = False
    assert mgr.apply(good)["ok"]
    assert mgr.status()["daemons"]["dhcp6"]["running"] is False
    assert pid(mgr) == v4_pid


def test_apply_restarts_a_crashed_daemon(mgr, good):
    mgr.startup()
    mgr.procs["dhcp4"].kill()
    mgr.procs["dhcp4"].wait()
    result = mgr.apply(good)
    assert result["ok"] and result["action"] == "restarted"
    assert mgr.status()["daemons"]["dhcp4"]["running"] is True


def test_apply_invalid_changes_nothing(mgr, good):
    mgr.startup()
    old_pid = pid(mgr)
    good["dhcp4"]["subnets"][0]["pools"][0]["start"] = "bogus"
    result = mgr.apply(good)
    assert result["ok"] is False and result["errors"]
    assert pid(mgr) == old_pid
    assert not os.path.exists(mgr.settings_path)


def test_kea_rejection_keeps_old_config_and_cleans_staging(mgr, good):
    mgr.startup()
    conf_path = os.path.join(mgr.conf_dir, "kea-dhcp4.conf")
    before = open(conf_path).read()
    old_pid = pid(mgr)
    # make the fake `kea -t` fail by injecting a marker into the rendered config
    orig = cfg.render_dhcp4
    cfg.render_dhcp4 = lambda *a, **k: dict(orig(*a, **k), marker="FAIL_ME")
    try:
        result = mgr.apply(good)
    finally:
        cfg.render_dhcp4 = orig
    assert result["ok"] is False and "config rejected" in result["errors"][0]
    assert open(conf_path).read() == before
    assert not os.path.exists(conf_path + ".new")
    assert pid(mgr) == old_pid
    assert not os.path.exists(mgr.settings_path)


def test_restart_replaces_processes(mgr):
    mgr.startup()
    old_pid = pid(mgr)
    assert mgr.restart()["ok"] is True
    assert pid(mgr) != old_pid


def test_corrupt_settings_file_falls_back_to_defaults(mgr):
    os.makedirs(mgr.data_dir, exist_ok=True)
    open(mgr.settings_path, "w").write("{not json")
    assert mgr.load_settings() == cfg.default_settings("eth0")


def test_watch_reports_crashed_daemon(mgr):
    mgr.startup()
    mgr.procs["dhcp4"].kill()
    mgr.procs["dhcp4"].wait()
    threading.Thread(target=mgr.watch, daemon=True).start()
    assert wait_for(lambda: mgr.last_error, timeout=6)
    mgr._stopping = True
    assert "exited unexpectedly" in mgr.last_error
    assert mgr.status()["daemons"]["dhcp4"]["running"] is False


def test_missing_binary_is_tolerated(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    m = KeaManager({}, str(tmp_path / "e"), str(tmp_path / "d"))
    m.startup()
    assert m.status()["daemons"]["dhcp4"]["installed"] is False


# --------------------------------------------------------------------- logs
def test_daemon_output_is_captured(mgr):
    mgr.startup()
    assert wait_for(lambda: "fake: started" in log_text(mgr, source="dhcp4"))
    entries = mgr.get_logs()["entries"]
    assert {e["source"] for e in entries} >= {"addon", "dhcp4"}
    assert all(e["source"] == "dhcp4" for e in mgr.get_logs(source="dhcp4")["entries"])


def test_log_since_and_limit(mgr):
    mgr._log("one")
    first = mgr.get_logs()
    mgr._log("two")
    mgr._log("three")
    newer = mgr.get_logs(since=first["last_seq"])
    assert [e["line"] for e in newer["entries"]] == ["two", "three"]
    assert [e["line"] for e in mgr.get_logs(limit=1)["entries"]] == ["three"]
    assert mgr.get_logs(since=newer["last_seq"])["entries"] == []


# ------------------------------------------------------------ lease deletion
HEADER4 = "address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context\n"


def write_leases(mgr, *rows):
    os.makedirs(mgr.data_dir, exist_ok=True)
    with open(mgr.lease_path("dhcp4"), "w") as fh:
        fh.write(HEADER4)
        for addr, host in rows:
            fh.write("%s,aa:bb:cc:dd:ee:01,,3600,%d,1,0,0,%s,0,\n" % (addr, time.time() + 3600, host))


def test_delete_lease(mgr):
    write_leases(mgr, ("192.168.1.101", "keep"), ("192.168.1.102", "drop"))
    mgr.startup()
    old_pid = pid(mgr)
    assert mgr.delete_lease("dhcp4", "192.168.1.102") == {"ok": True}
    assert [l["address"] for l in lease_files.read_leases(mgr.lease_path("dhcp4"))] == ["192.168.1.101"]
    assert mgr.status()["daemons"]["dhcp4"]["running"] and pid(mgr) != old_pid  # restarted around the edit
    assert "deleted lease 192.168.1.102" in log_text(mgr)
    # deleting again: nothing left
    assert mgr.delete_lease("dhcp4", "192.168.1.102")["ok"] is False


def test_delete_lease_rejects_bad_input(mgr):
    write_leases(mgr, ("192.168.1.101", "h"))
    mgr.startup()
    assert mgr.delete_lease("dhcp9", "192.168.1.101")["ok"] is False
    assert mgr.delete_lease("dhcp4", "not-an-ip")["ok"] is False
    assert mgr.delete_lease("dhcp4", "fd00::1")["ok"] is False
    assert mgr.delete_lease("dhcp4", "192.168.1.250")["ok"] is False
    assert len(lease_files.read_leases(mgr.lease_path("dhcp4"))) == 1


# ------------------------------------------------------------------- misc
def test_list_interfaces_shape(mgr):
    result = mgr.list_interfaces()
    assert isinstance(result["interfaces"], list) and "lo" not in result["interfaces"]
    assert "default" in result


def test_import_preview(mgr, good):
    ok = mgr.import_preview(good)
    assert ok["ok"] and ok["settings"]["global"]["interface"] == "eth0"
    good["dhcp4"]["subnets"][0]["subnet"] = "x"
    bad = mgr.import_preview(good)
    assert bad["ok"] is False and bad["errors"] and bad["settings"] is None
    assert mgr.import_preview([])["ok"] is False


# ------------------------------------------------------------ storage guard
def test_storage_guard(mgr, monkeypatch):
    import manager as manager_mod
    assert mgr.storage_persistent() is True and mgr.status()["storage_persistent"] is True  # tmp dir

    mgr.data_dir = "/config/kea"
    monkeypatch.setattr(manager_mod.os.path, "ismount", lambda p: False)
    assert mgr.storage_persistent() is False
    monkeypatch.setattr(manager_mod.os.path, "ismount", lambda p: p == "/config")
    assert mgr.storage_persistent() is True
    mgr.data_dir = "/configuration/kea"          # similar prefix must not count as /config
    assert mgr.storage_persistent() is True


def test_kea_gets_the_documented_path_variables(mgr):
    """Kea 3.x refuses lease/pid/lock files outside its default dirs unless these are set.
    (KEA_LEASE_DIR does not exist; the lease directory variable is KEA_DHCP_DATA_DIR.)"""
    assert mgr.env["KEA_DHCP_DATA_DIR"] == mgr.data_dir
    assert "KEA_LEASE_DIR" not in mgr.env
    for name in ("KEA_PIDFILE_DIR", "KEA_LOCKFILE_DIR", "KEA_LOG_FILE_DIR"):
        assert mgr.env[name]


# ------------------------------------------- apply is verified and rolled back
def patched_render(marker):
    """Make every rendered DHCPv4 config contain *marker* (the fake Kea reacts to DIE_ME / RELOAD_FAIL)."""
    orig = cfg.render_dhcp4
    cfg.render_dhcp4 = lambda *a, **k: dict(orig(*a, **k), marker=marker)
    return orig


def test_apply_is_rolled_back_when_the_daemon_dies(mgr, good):
    mgr.startup()
    assert mgr.apply(good)["ok"]                                    # a saved, working configuration
    saved_before = open(mgr.settings_path).read()
    conf_before = open(os.path.join(mgr.conf_dir, "kea-dhcp4.conf")).read()

    bad = json.loads(json.dumps(good))
    bad["dhcp4"]["subnets"][0]["lease_time"] = 1234
    bad["global"]["interface"] = "nonexistent0"          # a new interface means a restart, where the fake dies
    orig = patched_render("DIE_ME")
    try:
        result = mgr.apply(bad)
    finally:
        cfg.render_dhcp4 = orig

    assert result["ok"] is False
    assert "stopped right after" in result["errors"][0] and "INIT_FAIL" in result["errors"][0]
    assert "previous configuration was restored" in result["errors"][-1]
    assert open(mgr.settings_path).read() == saved_before                  # settings.json is back
    assert open(os.path.join(mgr.conf_dir, "kea-dhcp4.conf")).read() == conf_before
    assert mgr.settings["dhcp4"]["subnets"][0]["lease_time"] == good["dhcp4"]["subnets"][0]["lease_time"]
    assert wait_for(lambda: mgr.status()["daemons"]["dhcp4"]["running"])    # restarted from the old config
    assert mgr.status()["last_error"] is None


def test_first_apply_that_fails_leaves_no_settings_file(mgr, good):
    mgr.startup()
    good["global"]["interface"] = "nonexistent0"
    orig = patched_render("DIE_ME")
    try:
        assert mgr.apply(good)["ok"] is False
    finally:
        cfg.render_dhcp4 = orig
    assert not os.path.exists(mgr.settings_path)                            # nothing persisted
    assert wait_for(lambda: mgr.status()["daemons"]["dhcp4"]["running"])


def test_apply_is_rolled_back_when_a_reload_is_rejected(mgr, good):
    mgr.startup()
    old_pid = pid(mgr)
    orig = patched_render("RELOAD_FAIL")
    try:
        result = mgr.apply(good)
    finally:
        cfg.render_dhcp4 = orig
    assert result["ok"] is False and "reported a problem" in result["errors"][0]
    assert "RECONFIGURATION_FAIL" in result["errors"][0]
    assert not os.path.exists(mgr.settings_path)
    assert wait_for(lambda: mgr.status()["daemons"]["dhcp4"]["running"]) and pid(mgr) != old_pid


def test_a_daemon_that_just_started_is_restarted_instead_of_signalled(mgr, good):
    mgr.reload_grace = 60
    mgr.startup()
    old_pid = pid(mgr)
    result = mgr.apply(good)
    assert result["ok"] and result["action"] == "restarted" and result["changes"]["restarted"] == ["dhcp4"]
    assert pid(mgr) != old_pid
    assert "SIGHUP received" not in log_text(mgr, source="dhcp4")


def test_apply_reports_what_it_did_to_each_daemon(mgr, good):
    mgr.startup()
    reloaded = mgr.apply(good)
    assert reloaded["action"] == "reloaded" and reloaded["changes"]["reloaded"] == ["dhcp4"]

    good["global"]["enable_dhcp6"] = True
    started = mgr.apply(good)
    assert started["action"] == "reloaded"                                  # DHCPv4 was not restarted
    assert started["changes"]["started"] == ["dhcp6"] and started["changes"]["reloaded"] == ["dhcp4"]

    good["global"]["enable_dhcp6"] = False
    stopped = mgr.apply(good)
    assert stopped["action"] == "reloaded" and stopped["changes"]["stopped"] == ["dhcp6"]


# ------------------------------------------------------- smaller regressions
def test_delete_lease_does_not_resurrect_a_stopped_daemon(mgr):
    write_leases(mgr, ("192.168.1.101", "x"))
    mgr.startup()
    mgr._stop("dhcp4")                                                      # e.g. it failed or was stopped
    assert mgr.delete_lease("dhcp4", "192.168.1.101") == {"ok": True}
    assert mgr.status()["daemons"]["dhcp4"]["running"] is False


def test_import_without_a_global_section_keeps_the_current_interface(mgr, good):
    mgr.startup()
    current = json.loads(json.dumps(good))
    current["global"]["interface"] = "br7"
    assert mgr.apply(current)["ok"]
    old_format = json.loads(json.dumps(good))
    del old_format["global"]
    result = mgr.import_preview(old_format)
    assert result["ok"] and result["settings"]["global"]["interface"] == "br7"   # not the app option (eth0)


def test_a_malformed_settings_file_does_not_crash_start_up(mgr):
    """A hand-edited settings.json used to raise AttributeError and crash-loop the app."""
    os.makedirs(mgr.data_dir, exist_ok=True)
    for text in ('{"dhcp4": "x"}', '{"dhcp4": [1]}', '{"dhcp6": 5}', '[1, 2]', '{"global": 3}', 'null'):
        open(mgr.settings_path, "w").write(text)
        loaded = mgr.load_settings()                                         # must not raise
        assert isinstance(loaded, dict) and "dhcp4" in loaded and "global" in loaded, text
        # A broken section is treated as "no subnets", never as the sample network: Kea must not start
        # serving 192.168.1.0/24 on someone's LAN because their file is damaged.
        if text.startswith('{"dhcp4"'):
            assert loaded["dhcp4"]["subnets"] == [], text
    mgr.startup()                                                            # and the whole start-up survives
    assert mgr.status()["daemons"]["dhcp4"]["running"] is True


def test_malformed_settings_in_a_request_are_errors_not_crashes(mgr, good):
    mgr.startup()
    for bad in ({"dhcp4": "x"}, {"dhcp4": [1]}, {"dhcp6": 7}):
        result = mgr.apply(dict(good, **bad))
        assert result["ok"] is False and result["errors"], bad
        assert mgr.import_preview(dict(good, **bad))["ok"] is False


def test_a_hanging_config_test_is_a_problem_not_a_crash(mgr, good, monkeypatch):
    import subprocess as sp
    import manager as manager_mod

    def hang(*args, **kwargs):
        raise sp.TimeoutExpired(args[0], 30)

    mgr.startup()
    monkeypatch.setattr(manager_mod.subprocess, "run", hang)
    result = mgr.apply(good)
    assert result["ok"] is False and "could not test the configuration" in result["errors"][0]
    assert not os.path.exists(mgr._conf_path("dhcp4", ".new"))              # staging cleaned up


# ------------------------------------------------------------------ NTP host names (resolved by the app)
def ntp_probe(alive):
    def probe(address, **_):
        ok = address in alive
        return {"address": address, "ok": ok, "reason": "ok" if ok else "no answer",
                "stratum": 2 if ok else None, "offset": 0.0 if ok else None, "rtt": 0.01 if ok else None}
    return probe


def ntp_resolver(table):
    def resolver(name, port, family, kind):
        import socket
        if name not in table:
            raise socket.gaierror(-2, "Name or service not known")
        return [(family, kind, 17, "", (a, 0)) for a in table[name]]
    return resolver


def with_ntp_names(good, *names):
    good["dhcp4"]["subnets"][0]["ntp_servers"] = list(names)
    return good


def ntp_option(m):
    with open(m._conf_path("dhcp4"), encoding="utf-8") as fh:
        options = json.load(fh)["Dhcp4"]["subnet4"][0]["option-data"]
    return next((o["data"] for o in options if o["name"] == "ntp-servers"), None)


def test_ntp_names_are_resolved_checked_and_written_as_addresses(mgr, good):
    mgr.ntp_probe = ntp_probe({"198.51.100.1", "198.51.100.2"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1", "198.51.100.2", "198.51.100.3"]})
    mgr.startup()
    result = mgr.apply(with_ntp_names(good, "pool.test", "203.0.113.7"))
    assert result["ok"], result
    assert ntp_option(mgr) == "198.51.100.1, 198.51.100.2, 203.0.113.7"        # .3 did not answer
    assert mgr.settings["dhcp4"]["subnets"][0]["ntp_servers"] == ["pool.test", "203.0.113.7"]   # names are kept
    with open(mgr.ntp_path, encoding="utf-8") as fh:
        assert json.load(fh)["pool.test"]["addresses"] == ["198.51.100.1", "198.51.100.2"]


def test_a_name_that_does_not_resolve_is_saved_but_not_sent_and_warned_about(mgr, good):
    mgr.ntp_resolver = ntp_resolver({})
    mgr.startup()
    result = mgr.apply(with_ntp_names(good, "gone.test"))
    assert result["ok"] and ntp_option(mgr) is None
    assert any("gone.test" in w and "not sent to clients" in w for w in result["warnings"])


def test_refresh_reloads_kea_only_when_the_addresses_changed(mgr, good):
    mgr.ntp_probe = ntp_probe({"198.51.100.1", "198.51.100.2"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    mgr.startup()
    mgr.apply(with_ntp_names(good, "pool.test"))
    first = pid(mgr)
    assert mgr.refresh_ntp(force=True) == {"ok": True, "changed": False, "warnings": []}
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.2"]})            # the pool hands out another one
    assert mgr.refresh_ntp(force=True)["changed"]
    assert ntp_option(mgr) == "198.51.100.1, 198.51.100.2"                       # the old good one is kept first
    assert pid(mgr) == first                                                      # SIGHUP, not a restart
    assert "NTP servers changed" in log_text(mgr)


def test_refresh_keeps_the_last_good_addresses_during_a_dns_outage(mgr, good):
    mgr.ntp_probe = ntp_probe({"198.51.100.1"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    mgr.startup()
    mgr.apply(with_ntp_names(good, "pool.test"))
    mgr.ntp_resolver = ntp_resolver({})                       # DNS is down, the server itself still answers
    assert mgr.refresh_ntp(force=True)["warnings"] == [] and not mgr.ntp_status()["pool.test"]["stale"]
    mgr.ntp_probe = ntp_probe(set())                          # and now nothing answers either
    result = mgr.refresh_ntp(force=True)
    assert result["ok"] and not result["changed"] and "pool.test" in result["warnings"][0]
    assert ntp_option(mgr) == "198.51.100.1"
    assert mgr.ntp_status()["pool.test"]["stale"]


def test_names_not_yet_due_are_not_resolved_again(mgr, good):
    calls = []
    mgr.ntp_probe = ntp_probe({"198.51.100.1"})
    base = ntp_resolver({"pool.test": ["198.51.100.1"]})
    mgr.ntp_resolver = lambda *a: calls.append(a[0]) or base(*a)
    mgr.startup()
    mgr.apply(with_ntp_names(good, "pool.test"))
    calls.clear()
    assert mgr.refresh_ntp() == {"ok": True, "changed": False}
    assert calls == []


def test_a_failed_reload_restores_the_previous_ntp_addresses(mgr, good):
    mgr.ntp_probe = ntp_probe({"198.51.100.1", "198.51.100.2"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    mgr.startup()
    mgr.apply(with_ntp_names(good, "pool.test"))
    orig = cfg.render_dhcp4
    cfg.render_dhcp4 = lambda *a, **k: dict(orig(*a, **k), marker="FAIL_ME")
    try:
        mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.2"]})
        result = mgr.refresh_ntp(force=True)
    finally:
        cfg.render_dhcp4 = orig
    assert not result["ok"]
    assert mgr.ntp["pool.test"]["addresses"] == ["198.51.100.1"]


def test_removed_names_are_forgotten(mgr, good):
    mgr.ntp_probe = ntp_probe({"198.51.100.1"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    mgr.startup()
    mgr.apply(with_ntp_names(good, "pool.test"))
    mgr.apply(with_ntp_names(cfg.default_settings(), "203.0.113.7"))
    assert mgr.ntp == {}


def test_check_ntp_for_the_web_ui(mgr):
    mgr.ntp_probe = ntp_probe({"198.51.100.1"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1", "198.51.100.2"]})
    out = mgr.check_ntp(["pool.test"])
    assert out["ok"] and out["usable"] == ["198.51.100.1"] and out["warning"] == ""
    assert not mgr.check_ntp("pool.test")["ok"]
    assert not mgr.check_ntp(["x"] * 17)["ok"]


def test_ntp_names_survive_a_restart_without_new_network_access(tmp_path, fake_kea):
    first = KeaManager({"interface": "eth0"}, str(tmp_path / "etc"), str(tmp_path / "data"))
    first.settle_seconds, first.reload_grace = 0.4, 0
    first.ntp_probe = ntp_probe({"198.51.100.1"})
    first.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    first.startup()
    first.apply(with_ntp_names(cfg.default_settings(), "pool.test"))
    first.shutdown()
    second = KeaManager({"interface": "eth0"}, str(tmp_path / "etc"), str(tmp_path / "data"))
    second.ntp_resolver = ntp_resolver({})                  # offline: must use the saved addresses
    second.ntp_probe = ntp_probe(set())
    second.startup()
    try:
        assert ntp_option(second) == "198.51.100.1"
    finally:
        second.shutdown()


# ------------------------------------------------------------------ import from the old DHCP server app
OLD_APP = """
default_lease: 3600
dns: [192.0.2.53]
ntp: [pool.test]
domain: example.lan
networks:
  - {subnet: 192.168.1.0, netmask: 255.255.255.0, gateway: 192.168.1.1, interface: eth0,
     range_start: 192.168.1.100, range_end: 192.168.1.150}
hosts:
  - {name: printer, mac: "aa:bb:cc:dd:ee:01", ip: 192.168.1.10}
"""


def test_import_from_the_old_app_is_a_draft_and_keeps_ipv6(mgr, good):
    mgr.startup()
    v6 = good
    v6["global"]["enable_dhcp6"] = True
    assert mgr.apply(v6)["ok"]
    before = json.dumps(mgr.settings, sort_keys=True)
    result = mgr.import_dhcp_server(OLD_APP)
    assert result["ok"] and result["settings"]["dhcp4"]["subnets"][0]["reservations"][0]["hostname"] == "printer"
    assert result["settings"]["global"]["enable_dhcp6"] is True
    assert result["settings"]["dhcp6"] == mgr.settings["dhcp6"]
    assert json.dumps(mgr.settings, sort_keys=True) == before            # nothing was applied


def test_import_from_the_old_app_reports_unusable_input(mgr):
    mgr.startup()
    assert not mgr.import_dhcp_server("")["ok"]
    assert "not valid YAML" in mgr.import_dhcp_server("a: [1")["errors"][0]
    out = mgr.import_dhcp_server("hosts: []")
    assert not out["ok"] and "no networks" in out["errors"][0]


def test_host_addresses_have_the_expected_shape(mgr):
    mgr.startup()
    out = mgr.host_addresses()
    assert out["interface"] == "eth0" and set(out) == {"interface", "ipv4", "ipv6"}


def test_startup_never_waits_for_dns(tmp_path, fake_kea):
    first = KeaManager({"interface": "eth0"}, str(tmp_path / "etc"), str(tmp_path / "data"))
    first.settle_seconds, first.reload_grace = 0.4, 0
    first.ntp_probe = ntp_probe({"198.51.100.1"})
    first.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    first.startup()
    first.apply(with_ntp_names(cfg.default_settings(), "pool.test"))
    first.shutdown()
    calls = []

    def slow_dns(*args):
        calls.append(args[0])
        raise AssertionError("startup must not resolve names")
    second = KeaManager({"interface": "eth0"}, str(tmp_path / "etc"), str(tmp_path / "data"))
    second.ntp_resolver = slow_dns
    second.ntp_refresh_seconds = 0                          # the saved addresses are "due", and still used
    second.startup()
    try:
        assert calls == [] and ntp_option(second) == "198.51.100.1"
    finally:
        second.shutdown()


def test_a_name_never_resolved_is_filled_in_after_start_without_delaying_kea(mgr, good):
    mgr.startup()
    mgr.apply(with_ntp_names(good, "pool.test"))             # nothing resolves yet
    mgr.ntp.clear()                                          # as after a first start: no saved addresses at all
    assert ntp_option(mgr) is None
    mgr.ntp_probe = ntp_probe({"198.51.100.1"})
    mgr.ntp_resolver = ntp_resolver({"pool.test": ["198.51.100.1"]})
    first = pid(mgr)
    assert mgr.refresh_ntp()["changed"] and ntp_option(mgr) == "198.51.100.1" and pid(mgr) == first


# ------------------------------------------------------------------ default interface that does not exist
def routed_machine(monkeypatch, names=("enp1s0", "wlan0"), route="enp1s0"):
    import manager
    monkeypatch.setattr(manager, "network_interfaces", lambda: (list(names), route))


def new_manager(tmp_path, options=None):
    m = KeaManager(options if options is not None else {"interface": "eth0"},
                   str(tmp_path / "etc"), str(tmp_path / "data"))
    m.settle_seconds, m.reload_grace = 0.4, 0
    return m


def test_a_missing_default_interface_becomes_the_default_route_one_and_nothing_is_served_yet(tmp_path, fake_kea, monkeypatch):
    routed_machine(monkeypatch)
    m = new_manager(tmp_path)
    try:
        assert m.default_global["interface"] == "enp1s0" and m.auto_interface == "enp1s0"
        m.startup()
        st = m.status()
        assert st["interface"] == "enp1s0" and st["awaiting_setup"] and st["last_error"] is None
        assert not st["daemons"]["dhcp4"]["running"]
        assert "not started until you click Save & apply" in log_text(m)
        refused = m.restart()
        assert not refused["ok"] and "not been set up" in refused["errors"][0]
        assert not m.status()["daemons"]["dhcp4"]["running"]
        assert m.refresh_ntp(force=True) == {"ok": True, "changed": False}
    finally:
        m.shutdown()


def test_the_first_save_starts_kea_and_is_remembered(tmp_path, fake_kea, monkeypatch):
    routed_machine(monkeypatch)
    m = new_manager(tmp_path)
    m.startup()
    try:
        data = cfg.default_settings("enp1s0")
        assert m.apply(data)["ok"]
        assert m.status()["daemons"]["dhcp4"]["running"] and not m.status()["awaiting_setup"]
        assert os.path.exists(m.settings_path)
    finally:
        m.shutdown()
    again = new_manager(tmp_path)                       # restart of the app: settings exist now
    again.startup()
    try:
        assert not again.status()["awaiting_setup"] and again.status()["daemons"]["dhcp4"]["running"]
    finally:
        again.shutdown()


def test_a_first_save_that_fails_leaves_kea_off_and_still_waiting(tmp_path, fake_kea, monkeypatch):
    routed_machine(monkeypatch)
    m = new_manager(tmp_path)
    m.startup()
    orig = cfg.render_dhcp4
    cfg.render_dhcp4 = lambda *a, **k: dict(orig(*a, **k), marker="DIE_ME")      # the daemon exits at start
    try:
        result = m.apply(cfg.default_settings("enp1s0"))
        assert not result["ok"] and "restored" in result["errors"][-1]
    finally:
        cfg.render_dhcp4 = orig
    try:
        st = m.status()
        assert st["awaiting_setup"] and not st["daemons"]["dhcp4"]["running"]
        assert not os.path.exists(m.settings_path)
    finally:
        m.shutdown()


@pytest.mark.parametrize("options, names, route, why", [
    ({"interface": "enp9s9"}, ("enp1s0",), "enp1s0", "a name the user typed is never replaced"),
    ({"interface": "eth0"}, ("eth0", "wlan0"), "wlan0", "eth0 exists"),
    ({"interface": "eth0"}, ("enp1s0",), None, "no default route to choose from"),
    ({"interface": "eth0"}, (), None, "interfaces cannot be listed (not Linux)"),
])
def test_the_interface_is_left_alone_when_there_is_no_good_reason_to_change_it(tmp_path, fake_kea, monkeypatch,
                                                                                options, names, route, why):
    routed_machine(monkeypatch, names, route)
    m = new_manager(tmp_path, options)
    assert m.auto_interface is None and m.default_global["interface"] == options["interface"], why
    m.startup()
    try:
        assert not m.status()["awaiting_setup"] and m.status()["daemons"]["dhcp4"]["running"], why
    finally:
        m.shutdown()


def test_saved_settings_with_a_missing_interface_are_not_changed(tmp_path, fake_kea, monkeypatch):
    """Only a fresh install is helped. Someone who saved `eth0` earlier still gets Kea's own refusal."""
    routed_machine(monkeypatch)
    os.makedirs(tmp_path / "data")
    with open(tmp_path / "data" / "settings.json", "w", encoding="utf-8") as fh:
        json.dump(cfg.default_settings("eth0"), fh)
    m = new_manager(tmp_path)
    m.startup()
    try:
        assert not m.status()["awaiting_setup"] and m.status()["interface"] == "eth0"
    finally:
        m.shutdown()


# ------------------------------------------------------------------ Kea's control channel
from fake_control import FakeControl  # noqa: E402


@pytest.fixture
def controlled(tmp_path, fake_kea):
    """A manager that uses the control channel, with a fake daemon behind kea4-ctrl.sock (and the fake processes)."""
    fakes = []
    m = KeaManager({"interface": "eth0", "enable_dhcp6": True}, str(tmp_path / "etc"), str(tmp_path / "data"))
    m.settle_seconds, m.reload_grace = 0.4, 0
    m.control_api = True

    def attach(handler=None, name="kea4-ctrl.sock"):
        fake = FakeControl(handler, name=name)
        fakes.append(fake)
        m.run_dir = fake.dir
        return fake
    m.attach = attach
    yield m
    m.shutdown()
    for fake in fakes:
        fake.close()


def kea_lease4(address, host="h", hw="b4:bb:cc:dd:ee:01", state=0, valid=3600, age=0, subnet=1):
    return {"ip-address": address, "hw-address": hw, "hostname": host, "state": state, "subnet-id": subnet,
            "valid-lft": valid, "cltt": int(time.time()) - age}


def test_the_control_channel_is_used_only_when_the_lease_hook_is_installed(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    monkeypatch.setenv("KEA_HOOKS_PATH", str(hooks))
    monkeypatch.delenv("KEA_APP_CONTROL", raising=False)
    assert not KeaManager({}, str(tmp_path / "e1"), str(tmp_path / "d1")).control_api
    (hooks / "libdhcp_lease_cmds.so").write_bytes(b"")
    assert KeaManager({}, str(tmp_path / "e2"), str(tmp_path / "d2")).control_api
    monkeypatch.setenv("KEA_APP_CONTROL", "0")
    assert not KeaManager({}, str(tmp_path / "e3"), str(tmp_path / "d3")).control_api


def test_the_rendered_config_opens_the_socket_and_loads_the_hook(controlled):
    controlled.attach()
    controlled.startup()
    with open(controlled._conf_path("dhcp4"), encoding="utf-8") as fh:
        v4 = json.load(fh)["Dhcp4"]
    assert v4["control-sockets"] == [{"socket-type": "unix", "socket-name": "kea4-ctrl.sock"}]
    assert v4["hooks-libraries"] == [{"library": "libdhcp_lease_cmds.so"}]
    with open(controlled._conf_path("dhcp6"), encoding="utf-8") as fh:
        assert json.load(fh)["Dhcp6"]["control-sockets"][0]["socket-name"] == "kea6-ctrl.sock"
    assert controlled.status()["control_api"] is True


def test_leases_come_from_kea_when_it_answers(controlled):
    items = [kea_lease4("192.168.1.20", "laptop"), kea_lease4("192.168.1.3", "phone", hw="aa:bb:cc:dd:ee:02"),
             kea_lease4("192.168.1.30", "declined", state=1), kea_lease4("192.168.1.31", "old", valid=60, age=600)]
    fake = controlled.attach(lambda c, a: {"result": 0, "arguments": {"leases": items}} if c == "lease4-get-all"
                             else {"result": 3, "text": "0 IPv6 lease(s) found."})
    controlled.startup()
    leases = controlled.get_leases()
    assert [l["address"] for l in leases["dhcp4"]] == ["192.168.1.3", "192.168.1.20"]       # sorted, active only
    phone = leases["dhcp4"][0]
    assert (phone["identifier"], phone["hostname"], phone["random_mac"], phone["subnet_id"]) == (
        "aa:bb:cc:dd:ee:02", "phone", True, 1)
    assert 3500 < phone["remaining"] <= 3600 and phone["kind"] == "address"
    assert leases["dhcp6"] == []
    assert ("lease4-get-all", None) in fake.calls


def test_an_empty_answer_means_no_leases_not_a_failure(controlled):
    controlled.attach(lambda c, a: {"result": 3, "text": "0 IPv4 lease(s) found."})
    controlled.startup()
    write_leases(controlled, ("192.168.1.101", "stale-csv"))          # the CSV must not be consulted
    assert controlled.get_leases()["dhcp4"] == []


@pytest.mark.parametrize("mode", ["garbage", "silent"])
def test_leases_fall_back_to_the_files_when_the_channel_misbehaves(controlled, mode):
    fake = controlled.attach()
    controlled.control_timeout = 0.3
    controlled.startup()
    write_leases(controlled, ("192.168.1.101", "from-csv"))
    fake.mode = mode
    assert [l["hostname"] for l in controlled.get_leases()["dhcp4"]] == ["from-csv"]


def test_deleting_a_lease_goes_through_kea_without_stopping_it(controlled):
    fake = controlled.attach()
    controlled.startup()
    old = pid(controlled)
    assert controlled.delete_lease("dhcp4", "192.168.1.102") == {"ok": True}
    assert fake.calls[-1] == ("lease4-del", {"ip-address": "192.168.1.102"})
    assert pid(controlled) == old and controlled.status()["daemons"]["dhcp4"]["running"]
    assert "deleted lease 192.168.1.102" in log_text(controlled)


def test_deleting_an_unknown_lease_says_so_and_a_refusal_shows_kea_s_words(controlled):
    answers = iter([{"result": 3, "text": "IPv4 lease not found."}, {"result": 1, "text": "lease database error"}])
    controlled.attach(lambda c, a: next(answers))
    controlled.startup()
    assert controlled.delete_lease("dhcp4", "192.168.1.102") == {"ok": False, "errors": ["no lease found for 192.168.1.102"]}
    assert controlled.delete_lease("dhcp4", "192.168.1.102") == {"ok": False, "errors": ["lease database error"]}


def test_an_ipv6_address_is_tried_as_an_address_and_then_as_a_delegated_prefix(controlled):
    fake = controlled.attach(lambda c, a: {"result": 3, "text": "not found"} if a["type"] == "IA_NA" else {"result": 0},
                             name="kea6-ctrl.sock")
    controlled.startup()
    assert controlled.delete_lease("dhcp6", "2001:db8:1::") == {"ok": True}
    assert [a["type"] for c, a in fake.calls if c == "lease6-del"] == ["IA_NA", "IA_PD"]


def test_a_silent_channel_falls_back_to_editing_the_lease_file(controlled):
    fake = controlled.attach()
    fake.mode = "garbage"
    write_leases(controlled, ("192.168.1.101", "keep"), ("192.168.1.102", "drop"))
    controlled.startup()
    old = pid(controlled)
    assert controlled.delete_lease("dhcp4", "192.168.1.102") == {"ok": True}
    assert [l["address"] for l in lease_files.read_leases(controlled.lease_path("dhcp4"))] == ["192.168.1.101"]
    assert pid(controlled) != old                                       # the old method restarts the daemon
    assert "editing the lease file" in log_text(controlled)


def test_a_reload_goes_through_the_channel_not_a_signal(controlled, good):
    fake = controlled.attach()
    controlled.startup()
    old = pid(controlled)
    good["dhcp4"]["subnets"][0]["lease_time"] = 7200
    assert controlled.apply(good)["ok"]
    assert ("config-reload", None) in fake.calls
    assert pid(controlled) == old
    text = log_text(controlled)
    assert "reloaded kea-dhcp4 configuration (control channel)" in text and "SIGHUP" not in text


def test_a_reload_that_kea_refuses_is_rolled_back_with_kea_s_own_message(controlled, good):
    controlled.attach(lambda c, a: {"result": 1, "text": "unsupported parameter 'x'"} if c == "config-reload"
                      else {"result": 0})
    controlled.startup()
    before = json.dumps(controlled.settings, sort_keys=True)
    good["dhcp4"]["subnets"][0]["lease_time"] = 7200
    result = controlled.apply(good)
    assert not result["ok"]
    assert any("refused the new configuration: unsupported parameter 'x'" in e for e in result["errors"])
    assert "previous configuration was restored" in result["errors"][-1]
    assert json.dumps(controlled.settings, sort_keys=True) == before


def test_a_channel_that_does_not_answer_falls_back_to_the_signal(controlled, good):
    fake = controlled.attach()
    fake.mode = "garbage"
    controlled.startup()
    good["dhcp4"]["subnets"][0]["lease_time"] = 7200
    assert controlled.apply(good)["ok"]
    text = log_text(controlled)
    assert "did not answer on its control socket" in text and "reloading kea-dhcp4 configuration (SIGHUP)" in text
