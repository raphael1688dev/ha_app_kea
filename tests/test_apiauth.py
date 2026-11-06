import json
import os
import stat

import pytest

from apiauth import ApiConfig, ApiConfigError, DEFAULT_CIDRS


@pytest.fixture
def cfg(tmp_path):
    clock = {"t": 1000}
    c = ApiConfig(str(tmp_path / "api.json"), clock=lambda: clock["t"])
    c.tick = lambda seconds: clock.update(t=clock["t"] + seconds)
    return c


def test_defaults_are_off_with_private_networks(cfg):
    pub = cfg.public()
    assert pub["enabled"] is False and pub["port"] == 8100 and pub["tokens"] == []
    assert pub["allowed_cidrs"] == DEFAULT_CIDRS and "read" in pub["scopes"]


def test_a_token_is_shown_once_and_stored_only_as_a_hash(cfg):
    record, secret = cfg.create_token("home-agent", ["propose"])
    assert secret.startswith("kea_") and len(secret) > 40
    assert record["scopes"] == ["read", "propose"] and "hash" not in record
    raw = open(cfg.path, encoding="utf-8").read()
    assert secret not in raw and json.loads(raw)["tokens"][0]["hash"] != secret
    assert stat.S_IMODE(os.stat(cfg.path).st_mode) == 0o600
    assert "hash" not in json.dumps(cfg.public())


def test_a_token_authenticates_until_it_is_deleted(cfg):
    record, secret = cfg.create_token("agent", [])
    assert cfg.authenticate(secret)["id"] == record["id"] and record["scopes"] == ["read"]
    for wrong in ("", "kea_wrong", secret + "x", None, 5, "x" * 500, "Bearer " + secret):
        assert cfg.authenticate(wrong) is None
    assert cfg.delete_token(record["id"]) and not cfg.delete_token(record["id"])
    assert cfg.authenticate(secret) is None


def test_tokens_survive_a_restart_of_the_app(cfg, tmp_path):
    _, secret = cfg.create_token("agent", ["apply", "restart"])
    again = ApiConfig(cfg.path)
    assert again.authenticate(secret)["scopes"] == ["read", "apply", "restart"]


def test_last_use_is_recorded_but_not_on_every_call(cfg):
    _, secret = cfg.create_token("agent", [])
    assert cfg.authenticate(secret)["last_used"] == 1000
    cfg.tick(5)
    cfg.authenticate(secret)
    assert json.load(open(cfg.path))["tokens"][0]["last_used"] == 1000          # within a minute: not rewritten
    cfg.tick(120)
    assert cfg.authenticate(secret)["last_used"] == 1125


@pytest.mark.parametrize("name, scopes, fragment", [
    ("", [], "name must be"), ("x" * 41, [], "name must be"), ("bad/name", [], "name must be"), (5, [], "name must be"),
    ("ok", ["root"], "scopes must be"), ("ok", "read", "scopes must be"),
])
def test_bad_token_requests_are_refused(cfg, name, scopes, fragment):
    with pytest.raises(ApiConfigError, match=fragment):
        cfg.create_token(name, scopes)


def test_names_are_unique_and_the_number_of_tokens_is_limited(cfg):
    cfg.create_token("Agent", [])
    with pytest.raises(ApiConfigError, match="already"):
        cfg.create_token("agent", [])
    for i in range(19):
        cfg.create_token(f"t{i}", [])
    with pytest.raises(ApiConfigError, match="at most"):
        cfg.create_token("one-too-many", [])


def test_configuration_is_validated(cfg):
    cfg.set_config(True, 8200, "192.168.88.0/24, 10.1.2.3")
    pub = cfg.public()
    assert pub["enabled"] and pub["port"] == 8200 and pub["allowed_cidrs"] == ["192.168.88.0/24", "10.1.2.3/32"]
    for args, fragment in [((1, 8100, ["10.0.0.0/8"]), "true or false"), ((True, 80, ["10.0.0.0/8"]), "between"),
                           ((True, 8099, ["10.0.0.0/8"]), "Web UI"), ((True, "8100", ["10.0.0.0/8"]), "between"),
                           ((True, 8100, []), "give 1 to"), ((True, 8100, ["nonsense"]), "not a network"),
                           ((True, 8100, ["0.0.0.0/0"]), "every address"), ((True, 8100, ["::/0"]), "every address")]:
        with pytest.raises(ApiConfigError, match=fragment):
            cfg.set_config(*args)
    assert cfg.public()["port"] == 8200                                              # a refusal changes nothing


def test_allowed_networks(cfg):
    cfg.set_config(True, 8100, ["192.168.88.0/24", "fd00::/8"])
    assert cfg.ip_allowed("192.168.88.7") and cfg.ip_allowed("fd12::1") and cfg.ip_allowed("::ffff:192.168.88.7")
    for blocked in ("192.168.89.1", "8.8.8.8", "2001:db8::1", "not-an-ip", ""):
        assert not cfg.ip_allowed(blocked)


def test_a_damaged_file_gives_the_safe_defaults(tmp_path):
    path = tmp_path / "api.json"
    path.write_text("{ nope")
    c = ApiConfig(str(path))
    assert not c.enabled and c.port == 8100
    path.write_text(json.dumps({"enabled": "yes", "port": 80, "allowed_cidrs": ["bad"], "tokens": [{"x": 1}, "y"]}))
    c = ApiConfig(str(path))
    assert not c.enabled and c.port == 8100 and c.public()["allowed_cidrs"] == DEFAULT_CIDRS and c.public()["tokens"] == []
