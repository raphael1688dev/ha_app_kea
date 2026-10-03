import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kea", "app"))

import settings as cfg  # noqa: E402

FAKE_KEA = """#!/bin/sh
# fake kea daemon: `-t FILE` validates, `-c FILE` runs until killed.
mode="$1"; file="$2"
if [ "$mode" = "-t" ]; then
  if grep -q FAIL_ME "$file"; then echo "fake: config rejected" >&2; exit 1; fi
  exit 0
fi
exec sleep 300
"""


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


@pytest.fixture
def good():
    """A valid settings dict (deep copy of the defaults)."""
    return cfg.default_settings()
