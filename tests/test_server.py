import pytest
from fastapi.testclient import TestClient

import settings as cfg
from server import create_app


class FakeManager:
    enabled = ["dhcp4"]
    settings = cfg.default_settings()

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.started = self.stopped = False
        self.applied = None

    def startup(self): self.started = True
    def watch(self): pass
    def shutdown(self): self.stopped = True
    def lease_path(self, name): return str(self.tmp_path / f"{name}.csv")
    def status(self): return {"daemons": {}, "last_error": None}

    def apply(self, raw):
        self.applied = raw
        cleaned, errors = cfg.validate(raw)
        return {"ok": False, "errors": errors} if errors else {"ok": True, "settings": cleaned}

    def restart(self): return {"ok": True, "status": self.status()}


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
                             ("post", "/api/restart")):
            r = getattr(c, method)(path)
            assert r.status_code == 403, path


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


def test_put_requires_json_content_type(client, manager):
    r = client.put("/api/settings", content=b'{"a":1}', headers={"content-type": "text/plain"})
    assert r.status_code == 415 and manager.applied is None
    r = client.post("/api/restart", headers={"content-type": "text/plain"})
    assert r.status_code == 415


def test_put_valid_and_invalid(client, manager):
    assert client.put("/api/settings", json=cfg.default_settings()).status_code == 200
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
