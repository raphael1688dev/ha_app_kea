import json

import pytest
from fastapi.testclient import TestClient

import settings as cfg
from server import create_app


class FakeManager:
    enabled = ["dhcp4"]

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.settings = cfg.default_settings()
        self.started = self.stopped = False
        self.applied = None
        self.deleted = []
        self.log_args = None

    def startup(self): self.started = True
    def watch(self): pass
    def shutdown(self): self.stopped = True
    def lease_path(self, name): return str(self.tmp_path / f"{name}.csv")
    def status(self): return {"daemons": {}, "last_error": None}

    def apply(self, raw):
        self.applied = raw
        cleaned, errors = cfg.validate(raw)
        return ({"ok": False, "errors": errors} if errors
                else {"ok": True, "settings": cleaned, "action": "reloaded", "status": self.status()})

    def restart(self): return {"ok": True, "status": self.status()}

    def delete_lease(self, family, address):
        self.deleted.append((family, address))
        return {"ok": True} if address == "192.168.1.5" else {"ok": False, "errors": ["no lease found"]}

    def get_logs(self, since=0, source=None, limit=500):
        self.log_args = (since, source, limit)
        return {"entries": [{"seq": 1, "ts": 0, "source": "dhcp4", "line": "hello"}], "last_seq": 1}

    def list_interfaces(self): return {"interfaces": ["eth0"], "default": "eth0"}

    def import_preview(self, raw):
        cleaned, errors = cfg.validate(raw)
        return {"ok": not errors, "errors": errors, "settings": None if errors else cleaned}


@pytest.fixture
def manager(tmp_path):
    return FakeManager(tmp_path)


@pytest.fixture
def client(manager):
    app = create_app(manager, {"testclient"})
    with TestClient(app) as c:   # context manager runs lifespan
        yield c


def test_lifespan_starts_and_stops_manager(manager):
    with TestClient(create_app(manager, {"testclient"})):
        assert manager.started
    assert manager.stopped


def test_forbidden_for_other_hosts(manager):
    with TestClient(create_app(manager, {"172.30.32.2"})) as c:
        for method, path in (("get", "/"), ("get", "/api/state"), ("put", "/api/settings"),
                             ("post", "/api/restart"), ("get", "/api/logs"), ("get", "/api/backup"),
                             ("delete", "/api/leases/dhcp4/192.168.1.5"), ("post", "/api/import"),
                             ("get", "/api/interfaces")):
            r = getattr(c, method)(path)
            assert r.status_code == 403, path
    assert manager.deleted == []


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"


def test_docs_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_state_status_leases(client):
    assert client.get("/api/state").json()["settings"]["dhcp4"]
    assert "daemons" in client.get("/api/status").json()
    assert client.get("/api/leases").json() == {"dhcp4": [], "dhcp6": []}
    assert client.get("/api/state").headers["cache-control"] == "no-store"


def test_unknown_route_404(client):
    assert client.get("/api/nope").status_code == 404


def test_mutations_require_json_content_type(client, manager):
    plain = {"content-type": "text/plain"}
    assert client.put("/api/settings", content=b'{"a":1}', headers=plain).status_code == 415
    assert client.post("/api/restart", headers=plain).status_code == 415
    assert client.post("/api/import", content=b"{}", headers=plain).status_code == 415
    assert client.delete("/api/leases/dhcp4/192.168.1.5", headers=plain).status_code == 415
    assert manager.applied is None and manager.deleted == []


def test_put_valid_and_invalid(client):
    r = client.put("/api/settings", json=cfg.default_settings())
    assert r.status_code == 200 and r.json()["action"] == "reloaded"
    bad = cfg.default_settings()
    bad["dhcp4"]["subnets"][0]["subnet"] = "x"
    r = client.put("/api/settings", json=bad)
    assert r.status_code == 400 and r.json()["errors"]


def test_put_malformed_body_is_422(client):
    assert client.put("/api/settings", content=b"{oops", headers={"content-type": "application/json"}
                      ).status_code == 422
    assert client.put("/api/settings", json=[1, 2]).status_code == 422


def test_oversize_body_rejected(client):
    r = client.put("/api/settings", content=b"x" * (1024 * 1024 + 1),
                   headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_restart(client):
    r = client.post("/api/restart", json={})
    assert r.status_code == 200 and r.json()["ok"]


def test_delete_lease(client, manager):
    r = client.delete("/api/leases/dhcp4/192.168.1.5", headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    r = client.delete("/api/leases/dhcp6/fd00::1", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert manager.deleted == [("dhcp4", "192.168.1.5"), ("dhcp6", "fd00::1")]


def test_logs_endpoint(client, manager):
    r = client.get("/api/logs?since=5&source=dhcp4&limit=20")
    assert r.status_code == 200 and r.json()["entries"][0]["line"] == "hello"
    assert manager.log_args == (5, "dhcp4", 20)
    client.get("/api/logs")
    assert manager.log_args == (0, None, 500)
    assert client.get("/api/logs?source=bogus").status_code == 400
    assert client.get("/api/logs?since=abc").status_code == 422


def test_interfaces_endpoint(client):
    assert client.get("/api/interfaces").json() == {"interfaces": ["eth0"], "default": "eth0"}


def test_backup_download(client, manager):
    r = client.get("/api/backup")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and "kea-settings.json" in r.headers["content-disposition"]
    assert json.loads(r.text) == manager.settings


def test_import_preview(client, manager):
    r = client.post("/api/import", json=cfg.default_settings())
    assert r.status_code == 200 and r.json()["settings"]["version"] == 2
    bad = cfg.default_settings()
    bad["dhcp4"]["subnets"][0]["router"] = "nope"
    r = client.post("/api/import", json=bad)
    assert r.status_code == 400 and r.json()["errors"]
    assert manager.applied is None   # preview never applies
