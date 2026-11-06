"""The Web UI side of the agent API: switching it on, tokens, approving or rejecting proposals, rolling back."""
import pytest
from fastapi.testclient import TestClient

from manager import KeaManager
from server import create_app

ADD_PRINTER = [{"op": "add_reservation", "family": "dhcp4", "subnet_id": 1, "identifier": "aa:bb:cc:dd:ee:20",
                "ip_address": "192.168.1.60", "hostname": "printer"}]


class FakeAgent:
    def __init__(self, manager):
        self.manager, self.applied, self.stopped = manager, 0, False

    def apply_config(self):
        self.applied += 1
        return self.status()

    def status(self):
        c = self.manager.apiconfig
        return {"running": c.enabled, "port": c.port if c.enabled else None, "error": ""}

    def stop(self):
        self.stopped = True


@pytest.fixture
def parts(tmp_path, fake_kea):
    m = KeaManager({"interface": "eth0"}, str(tmp_path / "etc"), str(tmp_path / "data"))
    m.settle_seconds, m.reload_grace = 0.4, 0
    agent = FakeAgent(m)
    with TestClient(create_app(m, {"testclient"}, agent)) as client:
        yield client, m, agent
    assert agent.stopped


def post(client, path, body=None):
    return client.post(path, json=body or {})


def test_the_api_is_off_until_the_owner_switches_it_on(parts):
    client, m, agent = parts
    state = client.get("/api/agent").json()
    assert state["config"]["enabled"] is False and state["server"]["running"] is False and state["config"]["tokens"] == []
    assert agent.applied == 1                                          # asked to follow the (off) config at start-up
    r = client.put("/api/agent/config", json={"enabled": True, "port": 8222, "allowed_cidrs": "192.168.88.0/24"})
    assert r.status_code == 200 and r.json()["server"] == {"running": True, "port": 8222, "error": ""} and agent.applied == 2
    assert m.apiconfig.public()["allowed_cidrs"] == ["192.168.88.0/24"]
    bad = client.put("/api/agent/config", json={"enabled": True, "port": 80, "allowed_cidrs": "192.168.88.0/24"})
    assert bad.status_code == 400 and "between" in bad.json()["errors"][0] and m.apiconfig.port == 8222


def test_tokens_are_created_once_listed_without_secrets_and_deleted(parts):
    client, m, _ = parts
    made = post(client, "/api/agent/tokens", {"name": "claude", "scopes": ["propose"]}).json()
    assert made["ok"] and made["secret"].startswith("kea_") and made["token"]["scopes"] == ["read", "propose"]
    listed = client.get("/api/agent").json()
    assert [t["name"] for t in listed["config"]["tokens"]] == ["claude"] and made["secret"] not in str(listed)
    assert m.apiconfig.authenticate(made["secret"]) is not None
    assert post(client, "/api/agent/tokens", {"name": "claude", "scopes": []}).status_code == 400          # duplicate name
    assert client.request("DELETE", "/api/agent/tokens/" + made["token"]["id"], json={}).json() == {"ok": True}
    assert m.apiconfig.authenticate(made["secret"]) is None
    assert client.request("DELETE", "/api/agent/tokens/nope", json={}).status_code == 404


def test_a_person_approves_or_rejects_what_an_agent_proposed(parts):
    client, m, _ = parts
    first = m.propose_change("agent", "add the printer", operations=ADD_PRINTER)["proposal"]
    listed = client.get("/api/agent").json()["proposals"]
    assert [p["id"] for p in listed] == [first["id"]] and listed[0]["changes"] and "settings" not in listed[0]
    assert client.get(f"/api/agent/proposals/{first['id']}").json()["status"] == "pending"
    assert post(client, f"/api/agent/proposals/{first['id']}/approve").status_code == 200
    assert m.settings["dhcp4"]["subnets"][0]["reservations"][0]["hostname"] == "printer"
    assert client.get(f"/api/agent/proposals/{first['id']}").json()["status"] == "applied"
    assert post(client, f"/api/agent/proposals/{first['id']}/approve").status_code == 400            # not twice
    second = m.propose_change("agent", "remove it", operations=[
        {"op": "delete_reservation", "family": "dhcp4", "identifier": "aa:bb:cc:dd:ee:20"}])["proposal"]
    assert post(client, f"/api/agent/proposals/{second['id']}/reject").status_code == 200
    assert len(m.settings["dhcp4"]["subnets"][0]["reservations"]) == 1
    assert client.get("/api/agent/proposals/nope").status_code == 404 and post(client, "/api/agent/proposals/nope/reject").status_code == 400


def test_a_stale_proposal_cannot_be_approved(parts):
    client, m, _ = parts
    proposal = m.propose_change("agent", "x", operations=ADD_PRINTER)["proposal"]
    other = m.settings
    other["dhcp4"]["subnets"][0]["lease_time"] = 7200
    assert m.apply(other)["ok"]
    r = post(client, f"/api/agent/proposals/{proposal['id']}/approve")
    assert r.status_code == 400 and r.json()["code"] == "stale"


def test_history_is_listed_and_can_be_rolled_back_from_the_web_ui(parts):
    client, m, _ = parts
    first = m.revision()
    new = m.settings
    new["dhcp4"]["subnets"][0]["lease_time"] = 7200
    assert m.apply(new)["ok"]
    state = client.get("/api/agent").json()
    assert state["revision"] == m.revision() and [h["revision"] for h in state["history"]][-1] == first
    assert post(client, f"/api/history/{first}/rollback").status_code == 200 and m.revision() == first
    assert post(client, "/api/history/nope/rollback").status_code == 400


def test_the_state_changing_calls_need_the_json_content_type(parts):
    client, _, _ = parts
    for method, path in (("PUT", "/api/agent/config"), ("POST", "/api/agent/tokens"), ("POST", "/api/history/x/rollback"),
                         ("POST", "/api/agent/proposals/x/approve")):
        r = client.request(method, path, content="x", headers={"content-type": "text/plain"})
        assert r.status_code == 415, path
