"""Run the generated configuration through the *real* Kea (`kea-dhcpX -t`).

Skipped when Kea is not installed (local development); CI runs this inside the
built app image, so a Kea upgrade that rejects our output fails the build.
"""
import copy
import json
import os
import shutil
import subprocess

import pytest

import settings as cfg
from manager import KeaManager

HAVE_KEA = bool(shutil.which("kea-dhcp4") and shutil.which("kea-dhcp6"))
# CI sets REQUIRE_REAL_KEA=1 inside the image: a missing Kea must fail, never skip silently.
REQUIRED = bool(os.environ.get("REQUIRE_REAL_KEA"))
pytestmark = pytest.mark.skipif(not HAVE_KEA and not REQUIRED,
                                reason="real Kea binaries are not installed")


@pytest.mark.skipif(not REQUIRED, reason="only enforced when REQUIRE_REAL_KEA is set")
def test_real_kea_is_installed():
    assert HAVE_KEA, "kea-dhcp4 / kea-dhcp6 are missing from the image"


def kea_env(tmp_path):
    env = dict(os.environ)
    env.update({"KEA_DHCP_DATA_DIR": str(tmp_path), "KEA_PIDFILE_DIR": str(tmp_path),
                "KEA_LOCKFILE_DIR": str(tmp_path), "KEA_LOG_FILE_DIR": str(tmp_path),
                "KEA_CONTROL_SOCKET_DIR": str(tmp_path)})
    os.chmod(tmp_path, 0o750)                      # Kea 3.0 wants the control socket directory to be 0750
    return env


def run_test(binary, rendered, tmp_path):
    path = tmp_path / f"{binary}.conf"
    path.write_text(json.dumps(rendered, indent=2))
    return subprocess.run([binary, "-t", str(path)], env=kea_env(tmp_path),
                          capture_output=True, text=True, timeout=60)


def rendered_pair(settings, tmp_path):
    return (cfg.render_dhcp4(settings, str(tmp_path / "leases4.csv")),
            cfg.render_dhcp6(settings, str(tmp_path / "leases6.csv")))


def assert_accepted(settings, tmp_path):
    clean, errors = cfg.validate(settings)
    assert errors == [], errors
    r4, r6 = rendered_pair(clean, tmp_path)
    for binary, rendered in (("kea-dhcp4", r4), ("kea-dhcp6", r6)):
        result = run_test(binary, rendered, tmp_path)
        assert result.returncode == 0, f"{binary} rejected the config:\n{result.stdout}{result.stderr}"


def feature_rich():
    s = cfg.default_settings("eth0", True)
    v4 = s["dhcp4"]["subnets"][0]
    v4["pools"].append({"start": "192.168.1.10", "end": "192.168.1.20"})
    v4.update(domain_name="home.lan", domain_search=["home.lan", "lan"],
              ntp_servers=["192.168.1.2", "192.168.1.3"], lease_time=7200,
              custom_options=[{"name": "tftp-server-name", "data": "192.168.1.5"},
                              {"name": "boot-file-name", "data": "pxelinux.0"}])
    v4["reservations"] = [
        {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": "nas"},
        {"hw_address": "aa:bb:cc:dd:ee:02", "ip_address": "192.168.1.150", "hostname": ""}]
    v4b = copy.deepcopy(v4)
    v4b.update(id=2, subnet="10.20.0.0/24", router="10.20.0.1",
               pools=[{"start": "10.20.0.10", "end": "10.20.0.99"}], reservations=[],
               custom_options=[], ntp_servers=[], domain_search=[], domain_name="")
    s["dhcp4"]["subnets"].append(v4b)

    v6 = s["dhcp6"]["subnets"][0]
    v6.update(domain_search=["home.lan"], lease_time=7200,
              pd_pools=[{"prefix": "2001:db8:1::", "prefix_len": 48, "delegated_len": 64}],
              reservations=[{"duid": "00:01:00:01:aa:bb:cc:dd:ee:ff:00:11", "ip_address": "fd00::1500",
                             "hostname": "tv"}])
    return s


def test_default_settings_are_accepted(tmp_path):
    assert_accepted(cfg.default_settings(), tmp_path)


def test_feature_rich_settings_are_accepted(tmp_path):
    assert_accepted(feature_rich(), tmp_path)


def test_reservation_only_subnet_without_pools(tmp_path):
    s = cfg.default_settings()
    s["dhcp4"]["subnets"][0]["pools"] = []
    s["dhcp4"]["subnets"][0]["reservations"] = [
        {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": ""}]
    s["dhcp6"]["subnets"][0]["pools"] = []
    assert_accepted(s, tmp_path)


def test_no_options_at_all(tmp_path):
    s = cfg.default_settings()
    s["dhcp4"]["subnets"][0].update(router="", dns=[])
    s["dhcp6"]["subnets"][0].update(dns=[])
    assert_accepted(s, tmp_path)


def test_real_kea_rejects_an_unknown_option_name(tmp_path):
    """Our validator only checks the option name's shape; Kea must catch the rest."""
    s = cfg.default_settings()
    s["dhcp4"]["subnets"][0]["custom_options"] = [{"name": "not-a-real-option", "data": "x"}]
    clean, errors = cfg.validate(s)
    assert errors == []
    result = run_test("kea-dhcp4", cfg.render_dhcp4(clean, str(tmp_path / "l.csv")), tmp_path)
    assert result.returncode != 0


def test_manager_stage_and_test_with_real_kea(tmp_path):
    mgr = KeaManager({"interface": "eth0", "enable_dhcp6": True},
                     str(tmp_path / "etc"), str(tmp_path / "data"))
    settings, errors = cfg.validate(feature_rich())
    assert errors == []
    assert mgr._stage_and_test(settings) == []

    bad = copy.deepcopy(settings)
    bad["dhcp4"]["subnets"][0]["custom_options"] = [{"name": "not-a-real-option", "data": "x"}]
    problems = mgr._stage_and_test(bad)
    assert problems and "rejected the configuration" in problems[0]
    assert not os.path.exists(mgr._conf_path("dhcp4", ".new"))     # staging cleaned up


def _usable_interface():
    try:
        names = sorted(n for n in os.listdir("/sys/class/net") if n != "lo")
    except OSError:
        return None
    return names[0] if names else None


@pytest.mark.skipif(not _usable_interface(), reason="no network interface other than lo")
def test_real_kea_really_starts_and_opens_the_lease_file(tmp_path):
    """`-t` does not open the lease database, so it cannot catch a path Kea refuses at start-up
    (this once broke the app: lease files outside /var/lib/kea need KEA_DHCP_DATA_DIR)."""
    import time
    settings = cfg.default_settings(_usable_interface())
    rendered = cfg.render_dhcp4(settings, str(tmp_path / "kea-leases4.csv"))
    conf = tmp_path / "kea-dhcp4.conf"
    conf.write_text(json.dumps(rendered))
    proc = subprocess.Popen(["kea-dhcp4", "-c", str(conf)], env=kea_env(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(4)
        exited = proc.poll()
    finally:
        if proc.poll() is None:
            proc.terminate()
        output = proc.communicate(timeout=15)[0]
    assert exited is None, f"kea-dhcp4 exited with {exited}:\n{output}"
    assert "MEMFILE_FAILED_TO_OPEN" not in output and "CONFIG_LOAD_FAIL" not in output, output


# ---- features added for people coming from the ISC dhcpd based DHCP server app -------------------------------
def migration_settings():
    """What migrate.convert() produces: shared IPs, authoritative, underscore names, max lease, NTP in v4 and v6."""
    s = cfg.default_settings("eth0", True)
    s["global"].update(authoritative=True, allow_shared_ips=True)
    v4 = s["dhcp4"]["subnets"][0]
    v4.update(lease_time=3600, max_lease_time=7200, ntp_servers=["0.pool.example", "192.168.1.2"])
    v4["reservations"] = [
        {"hw_address": "b0:a7:32:06:a5:18", "ip_address": "192.168.1.50", "hostname": "zhimi-fan_za5"},
        {"hw_address": "b0:a7:32:06:a5:19", "ip_address": "192.168.1.50", "hostname": "zhimi-fan_za5-wifi"}]
    s["dhcp6"]["subnets"][0]["ntp_servers"] = ["2001:db8::123", "ff05::101", "pool.example"]
    return s


def test_migration_features_are_accepted_by_kea(tmp_path):
    clean, errors = cfg.validate(migration_settings())
    assert errors == []
    r4 = cfg.render_dhcp4(clean, str(tmp_path / "leases4.csv"), {"0.pool.example": ["198.51.100.1", "198.51.100.2"]})
    r6 = cfg.render_dhcp6(clean, str(tmp_path / "leases6.csv"))
    assert r4["Dhcp4"]["ip-reservations-unique"] is False and r4["Dhcp4"]["authoritative"] is True
    for binary, rendered in (("kea-dhcp4", r4), ("kea-dhcp6", r6)):
        result = run_test(binary, rendered, tmp_path)
        assert result.returncode == 0, f"{binary} rejected the config:\n{result.stdout}{result.stderr}"


def test_kea_refuses_shared_ips_unless_told_otherwise(tmp_path):
    """Pins the reason for the setting: with Kea's default, two reservations for one IP are an error."""
    clean, errors = cfg.validate(migration_settings())
    assert errors == []
    rendered = cfg.render_dhcp4(clean, str(tmp_path / "leases4.csv"))
    del rendered["Dhcp4"]["ip-reservations-unique"]
    assert run_test("kea-dhcp4", rendered, tmp_path).returncode != 0


def test_a_reserved_name_with_an_underscore_is_accepted_by_kea(tmp_path):
    s = cfg.default_settings()
    s["dhcp4"]["subnets"][0]["reservations"] = [
        {"hw_address": "aa:bb:cc:dd:ee:01", "ip_address": "192.168.1.50", "hostname": "zhimi-fan-za5_mibtf763"}]
    assert_accepted(s, tmp_path)


# ---- Kea's control channel and the lease hook ---------------------------------------------------------------
CONTROL = {"socket": "kea4-ctrl.sock", "hooks": ["libdhcp_lease_cmds.so"]}


def test_the_lease_hook_is_installed():
    hook = os.path.join(os.environ.get("KEA_HOOKS_PATH") or "/usr/lib/kea/hooks", "libdhcp_lease_cmds.so")
    if not os.path.exists(hook) and not REQUIRED:
        pytest.skip("the lease hook library is not installed")
    assert os.path.exists(hook), "kea-hook-lease-cmds is missing from the image"


def test_the_control_socket_and_the_lease_hook_are_accepted_by_kea(tmp_path):
    if not os.path.exists("/usr/lib/kea/hooks/libdhcp_lease_cmds.so") and not REQUIRED:
        pytest.skip("the lease hook library is not installed")
    clean, errors = cfg.validate(cfg.default_settings("eth0", True))
    assert errors == []
    v4 = cfg.render_dhcp4(clean, str(tmp_path / "leases4.csv"), control=CONTROL)
    v6 = cfg.render_dhcp6(clean, str(tmp_path / "leases6.csv"), control={"socket": "kea6-ctrl.sock", "hooks": CONTROL["hooks"]})
    for binary, rendered in (("kea-dhcp4", v4), ("kea-dhcp6", v6)):
        result = run_test(binary, rendered, tmp_path)
        assert result.returncode == 0, f"{binary} rejected the config:\n{result.stdout}{result.stderr}"


@pytest.mark.skipif(not _usable_interface(), reason="no network interface other than lo")
def test_a_running_kea_answers_the_commands_the_app_uses(tmp_path):
    """The whole control path against the real daemon: status, add / list / delete a lease, reload the file."""
    import time
    import kea_control as kc
    if not os.path.exists("/usr/lib/kea/hooks/libdhcp_lease_cmds.so") and not REQUIRED:
        pytest.skip("the lease hook library is not installed")
    iface = _usable_interface()
    settings = cfg.default_settings(iface)
    conf = tmp_path / "kea-dhcp4.conf"
    conf.write_text(json.dumps(cfg.render_dhcp4(settings, str(tmp_path / "kea-leases4.csv"), control=CONTROL)))
    env = kea_env(tmp_path)
    proc = subprocess.Popen(["kea-dhcp4", "-c", str(conf)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    sock = str(tmp_path / CONTROL["socket"])
    try:
        deadline = time.time() + 20
        while not os.path.exists(sock) and proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is None, f"kea-dhcp4 exited: {proc.communicate(timeout=10)[0]}"
        assert os.path.exists(sock), "no control socket appeared"

        assert kc.command(sock, "status-get").ok

        add = {"ip-address": "192.168.1.150", "hw-address": "aa:bb:cc:dd:ee:99", "subnet-id": 1,
               "valid-lft": 3600, "hostname": "control-test"}
        reply = kc.command(sock, "lease4-add", add)
        assert reply.ok, reply

        listed = kc.command(sock, "lease4-get-all")
        assert listed.result in (kc.RESULT_OK,), listed
        import leases as lease_files
        mine = [l for l in lease_files.from_control(listed.arguments["leases"], "dhcp4") if l["address"] == "192.168.1.150"]
        assert len(mine) == 1 and mine[0]["identifier"] == "aa:bb:cc:dd:ee:99" and mine[0]["hostname"] == "control-test"
        assert 3000 < mine[0]["remaining"] <= 3600

        assert kc.command(sock, "lease4-del", {"ip-address": "192.168.1.150"}).ok
        again = kc.command(sock, "lease4-del", {"ip-address": "192.168.1.150"})
        assert again.result == kc.RESULT_EMPTY, again               # the code the app turns into "no lease found"
        left = kc.command(sock, "lease4-get-all")
        assert left.result in (kc.RESULT_OK, kc.RESULT_EMPTY)
        assert not [l for l in left.arguments.get("leases", []) if l.get("ip-address") == "192.168.1.150"]

        # a changed file is picked up by config-reload, and a broken one is refused with a message
        settings["dhcp4"]["subnets"][0]["lease_time"] = 7200
        conf.write_text(json.dumps(cfg.render_dhcp4(settings, str(tmp_path / "kea-leases4.csv"), control=CONTROL)))
        assert kc.command(sock, "config-reload", timeout=30).ok
        conf.write_text("{ this is not json")
        broken = kc.command(sock, "config-reload", timeout=30)
        assert not broken.ok and broken.text
        assert kc.command(sock, "status-get").ok                    # Kea keeps running on the old configuration
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=20)
