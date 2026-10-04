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
                "KEA_LOCKFILE_DIR": str(tmp_path), "KEA_LOG_FILE_DIR": str(tmp_path)})
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
