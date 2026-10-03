import json
import os

import pytest

import settings as cfg
from manager import KeaManager


@pytest.fixture
def mgr(tmp_path, fake_kea, monkeypatch):
    conf, data = tmp_path / "etc", tmp_path / "data"
    m = KeaManager({"interface": "eth9"}, str(conf), str(data))
    yield m
    m.shutdown()


def test_startup_creates_default_settings_and_runs_dhcp4_only(mgr):
    mgr.startup()
    st = mgr.status()
    assert st["interface"] == "eth9" and st["enable_dhcp6"] is False
    assert st["daemons"]["dhcp4"]["running"] is True
    assert st["daemons"]["dhcp6"]["running"] is False and not st["daemons"]["dhcp6"]["enabled"]
    conf = json.load(open(os.path.join(mgr.conf_dir, "kea-dhcp4.conf")))
    assert conf["Dhcp4"]["interfaces-config"]["interfaces"] == ["eth9"]
    assert conf["Dhcp4"]["lease-database"]["name"] == os.path.join(mgr.data_dir, "kea-leases4.csv")


def test_dhcp6_started_when_enabled(tmp_path, fake_kea):
    m = KeaManager({"interface": "eth0", "enable_dhcp6": True}, str(tmp_path / "e"), str(tmp_path / "d"))
    try:
        m.startup()
        assert m.status()["daemons"]["dhcp6"]["running"] is True
    finally:
        m.shutdown()


def test_apply_valid_persists_and_restarts(mgr, good):
    mgr.startup()
    old_pid = mgr.status()["daemons"]["dhcp4"]["pid"]
    good["dhcp4"]["subnets"][0]["lease_time"] = 7200
    result = mgr.apply(good)
    assert result["ok"] is True
    assert result["status"]["daemons"]["dhcp4"]["pid"] != old_pid
    saved = json.load(open(mgr.settings_path))
    assert saved["dhcp4"]["subnets"][0]["lease_time"] == 7200


def test_apply_invalid_changes_nothing(mgr, good):
    mgr.startup()
    pid = mgr.status()["daemons"]["dhcp4"]["pid"]
    good["dhcp4"]["subnets"][0]["pool_start"] = "bogus"
    result = mgr.apply(good)
    assert result["ok"] is False and result["errors"]
    assert mgr.status()["daemons"]["dhcp4"]["pid"] == pid
    assert not os.path.exists(mgr.settings_path)


def test_kea_rejection_keeps_old_config_and_cleans_staging(mgr, good):
    mgr.startup()
    conf_path = os.path.join(mgr.conf_dir, "kea-dhcp4.conf")
    before = open(conf_path).read()
    pid = mgr.status()["daemons"]["dhcp4"]["pid"]
    # make the fake `kea -t` fail by injecting a marker into the rendered config
    good["dhcp4"]["subnets"][0]["reservations"] = [
        {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": "FAIL-ME"}]
    orig = cfg.render_dhcp4
    cfg.render_dhcp4 = lambda *a, **k: dict(orig(*a, **k), marker="FAIL_ME")
    try:
        result = mgr.apply(good)
    finally:
        cfg.render_dhcp4 = orig
    assert result["ok"] is False and "config rejected" in result["errors"][0]
    assert open(conf_path).read() == before
    assert not os.path.exists(conf_path + ".new")
    assert mgr.status()["daemons"]["dhcp4"]["pid"] == pid


def test_restart_uses_current_settings(mgr):
    mgr.startup()
    pid = mgr.status()["daemons"]["dhcp4"]["pid"]
    assert mgr.restart()["ok"] is True
    assert mgr.status()["daemons"]["dhcp4"]["pid"] != pid


def test_corrupt_settings_file_falls_back_to_defaults(mgr):
    os.makedirs(mgr.data_dir, exist_ok=True)
    open(mgr.settings_path, "w").write("{not json")
    assert mgr.load_settings() == cfg.default_settings()


def test_watch_reports_crashed_daemon(mgr):
    mgr.startup()
    mgr.procs["dhcp4"].kill()
    mgr.procs["dhcp4"].wait()
    mgr._stopping = False
    import threading, time
    t = threading.Thread(target=mgr.watch, daemon=True)
    t.start()
    deadline = time.time() + 6
    while time.time() < deadline and not mgr.last_error:
        time.sleep(0.2)
    mgr._stopping = True
    assert mgr.last_error and "異常結束" in mgr.last_error
    assert mgr.status()["daemons"]["dhcp4"]["running"] is False


def test_missing_binary_is_tolerated(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    m = KeaManager({}, str(tmp_path / "e"), str(tmp_path / "d"))
    m.startup()
    assert m.status()["daemons"]["dhcp4"]["installed"] is False
