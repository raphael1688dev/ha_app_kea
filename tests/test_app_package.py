"""Static checks of the app packaging against the Supervisor's validation rules.

The rule sets below mirror supervisor/docker/const.py, supervisor/apps/const.py
and supervisor/apps/validate.py (checked 2026-10). scripts/check_supervisor_rules.py
compares them with the live Supervisor source, so drift is caught in CI.
"""
import os
import re

import pytest
import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
APP_DIR = os.path.join(ROOT, "kea")

VALID_CAPABILITIES = {
    "AUDIT_WRITE", "BPF", "CHECKPOINT_RESTORE", "DAC_READ_SEARCH", "IPC_LOCK", "MKNOD",
    "NET_ADMIN", "NET_RAW", "PERFMON", "SETFCAP", "SYS_ADMIN", "SYS_MODULE", "SYS_NICE",
    "SYS_PTRACE", "SYS_RAWIO", "SYS_RESOURCE", "SYS_TIME",
}
# Current (non-legacy) map types. `config`, `addons`, `addon_config`, `all_addon_configs`
# are deprecated and must not be used by new apps.
VALID_MAP_TYPES = {
    "data", "ssl", "local_apps", "backup", "share", "media",
    "homeassistant_config", "all_app_configs", "app_config",
}
VALID_ARCH = {"aarch64", "amd64"}
VALID_STARTUP = {"initialize", "system", "services", "application", "once"}
VALID_BOOT = {"auto", "manual", "manual_only"}
VALID_STAGE = {"stable", "experimental", "deprecated"}
SCHEMA_TYPE = re.compile(r"^(str|bool|int|float|email|url|password|port|match\(.+\)|list\(.+\)|device(\(.+\))?)\??$")


@pytest.fixture(scope="module")
def config():
    with open(os.path.join(APP_DIR, "config.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_required_keys(config):
    for key in ("name", "version", "slug", "description", "arch"):
        assert config.get(key), key
    assert re.fullmatch(r"[A-Za-z0-9_-]+", config["slug"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(config["version"]))


def test_arch_has_no_deprecated_values(config):
    assert set(config["arch"]) <= VALID_ARCH


def test_privileged_capabilities_are_valid(config):
    bad = set(config.get("privileged", [])) - VALID_CAPABILITIES
    assert not bad, f"invalid capabilities (Supervisor would reject the app): {sorted(bad)}"


def test_map_types_are_current(config):
    for entry in config.get("map", []):
        match = re.fullmatch(r"([a-z_]+)(?::(rw|ro))?", entry)
        assert match, entry
        assert match.group(1) in VALID_MAP_TYPES, f"{entry}: unknown or deprecated map type"


def test_enumerated_values(config):
    assert config.get("startup", "application") in VALID_STARTUP
    assert config.get("boot", "auto") in VALID_BOOT
    assert config.get("stage", "stable") in VALID_STAGE


def test_ingress_settings(config):
    if config.get("ingress"):
        assert 1 <= int(config.get("ingress_port", 8099)) <= 65535
        assert config.get("panel_title")


def test_options_match_schema(config):
    assert set(config.get("options", {})) == set(config.get("schema", {}))
    for key, kind in config.get("schema", {}).items():
        assert SCHEMA_TYPE.match(str(kind)), f"{key}: unsupported schema type {kind!r}"


def test_translations_cover_every_option(config):
    folder = os.path.join(APP_DIR, "translations")
    files = [f for f in os.listdir(folder) if f.endswith(".yaml")]
    assert "en.yaml" in files
    for name in files:
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert set(data["configuration"]) == set(config["schema"]), name
        for key, entry in data["configuration"].items():
            assert entry.get("name") and entry.get("description"), f"{name}:{key}"


def test_dockerfile_follows_current_rules():
    with open(os.path.join(APP_DIR, "Dockerfile"), encoding="utf-8") as fh:
        text = fh.read()
    assert re.search(r"^FROM ghcr\.io/home-assistant/base:\S+$", text, re.M), \
        "Dockerfile must use an explicit FROM (BUILD_FROM is no longer provided)"
    assert "BUILD_FROM" not in text
    for label in ("io.hass.version", "io.hass.type", "io.hass.arch"):
        assert label in text, label
    assert 'io.hass.type="app"' in text


def test_docs_and_changelog_present(config):
    for name in ("DOCS.md", "CHANGELOG.md"):
        assert os.path.isfile(os.path.join(APP_DIR, name)), name
    with open(os.path.join(APP_DIR, "CHANGELOG.md"), encoding="utf-8") as fh:
        assert f"## {config['version']}" in fh.read(), "CHANGELOG.md has no entry for the current version"


def test_runtime_requirements_are_fully_pinned():
    """Reproducible builds, and something for Renovate to track: every line is name==version."""
    with open(os.path.join(APP_DIR, "requirements.txt"), encoding="utf-8") as fh:
        lines = [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    assert lines, "kea/requirements.txt is empty"
    for line in lines:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==\d[\w.!+-]*", line), f"not an exact pin: {line!r}"
    names = {l.split("==")[0].lower().replace("_", "-") for l in lines}
    assert {"fastapi", "uvicorn"} <= names


def test_dockerfile_installs_the_pinned_requirements():
    with open(os.path.join(APP_DIR, "Dockerfile"), encoding="utf-8") as fh:
        text = fh.read()
    assert "COPY requirements.txt" in text and "pip install" in text and "-r /tmp/requirements.txt" in text
    assert not re.search(r"pip install[^\n]*==", text), "pin packages in requirements.txt, not in the Dockerfile"


def test_dev_requirements_are_pinned_and_reuse_the_runtime_set():
    with open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8") as fh:
        lines = [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    assert "-r kea/requirements.txt" in lines
    for line in lines:
        if not line.startswith("-r"):
            assert "==" in line, f"dev dependency is not pinned: {line}"


def test_minimum_core_version_covers_the_app_config_mount(config):
    """`app_config` needs Supervisor 2026.07; older Supervisors silently skip the mount, so settings and leases
    would be lost on restart. The Supervisor has no minimum-Supervisor key, so a minimum *Core* version stands in."""
    app_mounts = {"app_config", "all_app_configs", "local_apps"}
    if not any(entry.split(":")[0] in app_mounts for entry in config.get("map", [])):
        pytest.skip("no app_* map type in use")
    minimum = config.get("homeassistant")
    assert isinstance(minimum, str), '`homeassistant: "2026.7.0"` is missing (quote it so YAML keeps it a string)'
    match = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d+)", minimum)
    assert match, f"{minimum!r} is not a Home Assistant version like 2026.7.0"
    assert (int(match.group(1)), int(match.group(2))) >= (2026, 7)


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG file"
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def test_the_app_has_an_icon_and_a_logo():
    """Without them the Supervisor shows the generic puzzle piece. icon.png has to be square."""
    width, height = png_size(os.path.join(APP_DIR, "icon.png"))
    assert width == height and 64 <= width <= 512
    width, height = png_size(os.path.join(APP_DIR, "logo.png"))
    assert width >= 100 and height >= 100
    for name in ("icon.png", "logo.png"):
        assert os.path.getsize(os.path.join(APP_DIR, name)) < 100_000


def test_the_logo_is_credited_to_its_owner():
    with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as fh:
        notices = fh.read()
    assert "isc.org/isclogos" in notices and "belong to ISC" in notices
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        assert "Kea logo, which belongs to ISC" in fh.read()


def test_the_image_installs_the_lease_hook_the_app_loads():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "kea", "app"))
    import manager
    with open(os.path.join(APP_DIR, "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    assert "kea-hook-lease-cmds" in dockerfile, "the package that ships " + manager.LEASE_HOOK
    assert manager.LEASE_HOOK == "libdhcp_lease_cmds.so" and manager.HOOKS_DIR == "/usr/lib/kea/hooks"
