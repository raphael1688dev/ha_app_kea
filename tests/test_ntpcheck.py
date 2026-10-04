"""NTP checks against a fake NTP server on 127.0.0.1 (a real UDP socket, so the packet handling is exercised)."""
import socket
import struct
import threading
import time

import pytest

import ntpcheck

NTP_EPOCH = ntpcheck.NTP_EPOCH


def ntp_time(value):
    seconds = int(value)
    return struct.pack("!II", seconds + NTP_EPOCH, int((value - seconds) * 2 ** 32))


class FakeNtp:
    """Answers NTP requests on a free local port. ``mode`` picks the kind of (mis)behaviour."""

    def __init__(self, mode="ok", offset=0.0, stratum=2, leap=0):
        self.mode, self.offset, self.stratum, self.leap = mode, offset, stratum, leap
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.stop = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop:
            try:
                data, peer = self.sock.recvfrom(1024)
            except socket.timeout:
                continue
            if self.mode == "silent":
                continue
            now = time.time() + self.offset
            flags = (self.leap << 6) | (4 << 3) | 4
            stratum = 0 if self.mode == "kod" else self.stratum
            reply = bytearray(48)
            reply[0], reply[1] = flags, stratum
            if self.mode == "kod":
                reply[12:16] = b"RATE"
            reply[24:32] = b"\0" * 8 if self.mode == "wrong-nonce" else data[40:48]
            reply[32:40] = ntp_time(now)
            reply[40:48] = b"\0" * 8 if self.mode == "no-time" else ntp_time(now)
            if self.mode == "short":
                reply = reply[:20]
            self.sock.sendto(bytes(reply), peer)

    def close(self):
        self.stop = True
        self.thread.join(1)
        self.sock.close()


@pytest.fixture
def servers():
    made = []

    def make(**kw):
        server = FakeNtp(**kw)
        made.append(server)
        return server
    yield make
    for server in made:
        server.close()


def ask(server, **kw):
    return ntpcheck.query("127.0.0.1", port=server.port, timeout=0.5, retries=0, **kw)


def test_good_server_passes_with_offset_and_rtt(servers):
    result = ask(servers(offset=0.0))
    assert result["ok"] and result["stratum"] == 2 and result["reason"] == "ok"
    assert abs(result["offset"]) < 0.5 and 0 <= result["rtt"] < 0.5


def test_offset_is_measured(servers):
    assert ask(servers(offset=5.0))["offset"] == pytest.approx(5.0, abs=0.5)


@pytest.mark.parametrize("mode, text", [
    ("silent", "no answer"),
    ("kod", "kiss-o'-death RATE"),
    ("wrong-nonce", "does not match"),
    ("no-time", "no time"),
    ("short", "too short"),
])
def test_bad_servers_are_refused(servers, mode, text):
    result = ask(servers(mode=mode))
    assert not result["ok"] and text in result["reason"]


def test_unsynchronised_servers_are_refused(servers):
    assert "not synchronised" in ask(servers(stratum=16))["reason"]
    assert "not synchronised" in ask(servers(leap=3))["reason"]


def test_unusable_addresses():
    for bad in ("0.0.0.0", "224.0.0.1", "127.0.0.1", "169.254.1.1", "255.255.255.255", "ff02::101", "::", "x"):
        assert ntpcheck.unusable_address(bad)
    assert ntpcheck.unusable_address("203.0.113.5") is None
    assert ntpcheck.unusable_address("2001:db8::1") is None


def fake_probe(table):
    def probe(address, **_):
        spec = table.get(address, {"ok": False, "reason": "no answer"})
        base = {"address": address, "ok": False, "reason": "", "stratum": None, "offset": None, "rtt": None}
        return dict(base, **spec)
    return probe


def good(offset=0.0, stratum=2, rtt=0.05):
    return {"ok": True, "reason": "ok", "stratum": stratum, "offset": offset, "rtt": rtt}


def resolver_for(table):
    def resolver(name, port, family, kind):
        if name not in table:
            raise socket.gaierror(-2, "Name or service not known")
        return [(family, kind, 17, "", (a, 0)) for a in table[name]]
    return resolver


def test_check_resolves_names_and_marks_each_address():
    report = ntpcheck.check_servers(
        ["pool.test", "203.0.113.9", "gone.test", "0.0.0.0"],
        probe=fake_probe({"198.51.100.1": good(), "198.51.100.2": {"ok": False, "reason": "no answer"},
                          "203.0.113.9": good(stratum=1)}),
        resolver=resolver_for({"pool.test": ["198.51.100.1", "198.51.100.2"]}))
    by = {s["server"]: s for s in report["servers"]}
    assert by["pool.test"]["kind"] == "hostname"
    assert [a["ok"] for a in by["pool.test"]["addresses"]] == [True, False]
    assert by["203.0.113.9"]["addresses"][0]["ok"]
    assert "cannot be resolved" in by["gone.test"]["error"]
    assert by["0.0.0.0"]["addresses"][0]["reason"] == "not a usable unicast address"
    assert ntpcheck.usable_addresses(report) == ["203.0.113.9", "198.51.100.1"]      # stratum 1 first


def test_a_falseticker_is_dropped_when_three_or_more_agree():
    table = {f"198.51.100.{i}": good(offset=o) for i, o in ((1, 0.01), (2, -0.02), (3, 0.0), (4, 40.0))}
    report = ntpcheck.check_servers(list(table), probe=fake_probe(table))
    assert set(ntpcheck.usable_addresses(report)) == {"198.51.100.1", "198.51.100.2", "198.51.100.3"}
    bad = [r for s in report["servers"] for r in s["addresses"] if not r["ok"]]
    assert len(bad) == 1 and "disagrees" in bad[0]["reason"]


def test_clock_warning_when_everything_disagrees_with_this_machine():
    table = {f"198.51.100.{i}": good(offset=3600.0) for i in (1, 2, 3)}
    report = ntpcheck.check_servers(list(table), probe=fake_probe(table))
    assert len(ntpcheck.usable_addresses(report)) == 3
    assert "clock differs" in ntpcheck.clock_warning(report)
    assert ntpcheck.clock_warning(ntpcheck.check_servers(["198.51.100.1"], probe=fake_probe(
        {"198.51.100.1": good(offset=0.1)}))) == ""


def test_an_address_returned_by_two_names_is_asked_once():
    calls = []

    def probe(address, **_):
        calls.append(address)
        return fake_probe({address: good()})(address)
    report = ntpcheck.check_servers(["a.test", "b.test"], probe=probe,
                                    resolver=resolver_for({"a.test": ["198.51.100.1"], "b.test": ["198.51.100.1"]}))
    assert calls == ["198.51.100.1"]
    assert report["servers"][1]["addresses"][0]["reason"] == "already listed above"


# ------------------------------------------------------------------ refresh (what dhcpd did per request)
def test_refresh_keeps_good_old_addresses_and_tops_up():
    previous = {"pool.test": {"addresses": ["198.51.100.1", "198.51.100.2"], "checked": 0, "error": "", "stale": False}}
    state, _ = ntpcheck.refresh(
        ["pool.test"], previous, per_name=3,
        probe=fake_probe({a: good() for a in ("198.51.100.1", "198.51.100.3", "198.51.100.4")}),   # .2 died
        resolver=resolver_for({"pool.test": ["198.51.100.3", "198.51.100.4", "198.51.100.1"]}))
    assert state["pool.test"]["addresses"] == ["198.51.100.1", "198.51.100.3", "198.51.100.4"]
    assert not state["pool.test"]["stale"] and state["pool.test"]["error"] == ""


def test_refresh_is_stable_when_nothing_changed():
    previous = {"p.test": {"addresses": ["198.51.100.1", "198.51.100.2"], "checked": 0, "error": "", "stale": False}}
    probe = fake_probe({a: good() for a in ("198.51.100.1", "198.51.100.2", "198.51.100.9")})
    state, _ = ntpcheck.refresh(["p.test"], previous, per_name=2, probe=probe,
                                resolver=resolver_for({"p.test": ["198.51.100.9", "198.51.100.2"]}))
    assert state["p.test"]["addresses"] == ["198.51.100.1", "198.51.100.2"]       # no churn


def test_refresh_keeps_previous_addresses_when_dns_or_probes_fail():
    previous = {"p.test": {"addresses": ["198.51.100.1"], "checked": 0, "error": "", "stale": False}}
    state, _ = ntpcheck.refresh(["p.test"], previous, probe=fake_probe({}), resolver=resolver_for({}))
    entry = state["p.test"]
    assert entry["addresses"] == ["198.51.100.1"] and entry["stale"] and "cannot be resolved" in entry["error"]


def test_refresh_with_no_history_and_no_answer_gives_an_empty_entry():
    state, _ = ntpcheck.refresh(["p.test"], None, probe=fake_probe({}), resolver=resolver_for({"p.test": ["198.51.100.1"]}))
    entry = state["p.test"]
    assert entry["addresses"] == [] and not entry["stale"] and "no answer" in entry["error"]


def test_refresh_does_not_give_one_address_to_two_names():
    probe = fake_probe({a: good() for a in ("198.51.100.1", "198.51.100.2")})
    state, _ = ntpcheck.refresh(["a.test", "b.test"], None, probe=probe, resolver=resolver_for(
        {"a.test": ["198.51.100.1"], "b.test": ["198.51.100.1", "198.51.100.2"]}))
    assert state["a.test"]["addresses"] == ["198.51.100.1"] and state["b.test"]["addresses"] == ["198.51.100.2"]
