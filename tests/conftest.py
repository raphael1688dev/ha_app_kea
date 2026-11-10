import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kea", "app"))

import settings as cfg  # noqa: E402

FAKE_KEA = """#!/bin/sh
# fake kea daemon: `-t FILE` validates, `-c FILE` logs and runs until killed.
mode="$1"; file="$2"
if [ "$mode" = "-t" ]; then
  if grep -q FAIL_ME "$file"; then echo "fake: config rejected" >&2; exit 1; fi
  exit 0
fi
# markers a test puts into the rendered config: DIE_ME = exit at start-up, RELOAD_FAIL = reject a SIGHUP reload
if grep -q DIE_ME "$file" 2>/dev/null; then echo "ERROR [kea-fake] DHCP4_INIT_FAIL interface does not exist"; exit 3; fi
trap 'if grep -q RELOAD_FAIL "$file"; then echo "ERROR [kea-fake] DHCP4_DYNAMIC_RECONFIGURATION_FAIL fake"; else echo "fake: SIGHUP received"; fi' HUP
echo "fake: started $0 $*"
while true; do sleep 0.1; done
"""


@pytest.fixture(autouse=True)
def eth0_exists(monkeypatch):
    """The machine running the tests (a CI runner, a laptop) must not decide whether `eth0` "exists"."""
    import manager
    monkeypatch.setattr(manager, "network_interfaces", lambda: (["eth0"], "eth0"))


@pytest.fixture
def fake_kea(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("kea-dhcp4", "kea-dhcp6"):
        path = bindir / name
        path.write_text(FAKE_KEA)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return bindir


def seed_settings(data_dir, interface="eth0", enable_dhcp6=False):
    """Write a settings.json, as an installation that has been set up has one. Without it the app waits for the first
    Save & apply and starts no daemon (a fresh install must not serve the sample subnet)."""
    import json
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg.default_settings(interface, enable_dhcp6), fh)


@pytest.fixture
def good():
    """A valid settings dict (deep copy of the defaults)."""
    return cfg.default_settings()
