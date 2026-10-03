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
