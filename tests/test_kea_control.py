"""The control channel client, against a fake daemon on a real unix socket."""
import os

import pytest

import kea_control as kc
from fake_control import FakeControl


@pytest.fixture
def daemon():
    made = []

    def make(handler=None, **kw):
        fake = FakeControl(handler, **kw)
        made.append(fake)
        return fake
    yield make
    for fake in made:
        fake.close()


def test_a_command_gets_its_reply(daemon):
    fake = daemon(lambda c, a: {"result": 0, "text": "done", "arguments": {"echo": [c, a]}})
    reply = kc.command(fake.path, "lease4-del", {"ip-address": "192.0.2.5"})
    assert reply.ok and reply.text == "done" and reply.arguments == {"echo": ["lease4-del", {"ip-address": "192.0.2.5"}]}
    assert fake.calls == [("lease4-del", {"ip-address": "192.0.2.5"})]


def test_a_command_without_arguments_sends_none(daemon):
    fake = daemon()
    kc.command(fake.path, "status-get")
    assert fake.calls == [("status-get", None)]


def test_result_codes_are_passed_on_not_raised(daemon):
    fake = daemon(lambda c, a: {"result": kc.RESULT_EMPTY, "text": "IPv4 lease not found."})
    reply = kc.command(fake.path, "lease4-del", {"ip-address": "192.0.2.5"})
    assert not reply.ok and reply.result == kc.RESULT_EMPTY and "not found" in reply.text


def test_an_answer_wrapped_in_a_list_is_unwrapped(daemon):
    fake = daemon()
    fake.mode = "list"
    assert kc.command(fake.path, "status-get").ok


def test_an_answer_that_arrives_in_pieces_is_assembled(daemon):
    fake = daemon(lambda c, a: {"result": 0, "arguments": {"leases": list(range(500))}})
    fake.mode = "partial"
    assert len(kc.command(fake.path, "lease4-get-all").arguments["leases"]) == 500


def test_a_big_answer_is_read_completely(daemon):
    fake = daemon(lambda c, a: {"result": 0, "arguments": {"leases": [{"hostname": "h" * 80}] * 20000}})
    assert len(kc.command(fake.path, "lease4-get-all").arguments["leases"]) == 20000


@pytest.mark.parametrize("mode, kind", [("silent", "timeout"), ("garbage", "protocol"), ("empty", "protocol")])
def test_a_daemon_that_misbehaves_raises_a_clear_error(daemon, mode, kind):
    fake = daemon()
    fake.mode = mode
    with pytest.raises(kc.KeaControlError) as excinfo:
        kc.command(fake.path, "status-get", timeout=0.3)
    assert excinfo.value.kind == kind


def test_no_daemon_means_unavailable(tmp_path):
    with pytest.raises(kc.KeaControlError) as excinfo:
        kc.command(str(tmp_path / "missing.sock"), "status-get")
    assert excinfo.value.kind == "unavailable"


def test_json_that_is_not_a_kea_answer_is_a_protocol_error(daemon):
    fake = daemon(lambda c, a: ["not", "an", "answer"])
    with pytest.raises(kc.KeaControlError) as excinfo:
        kc.command(fake.path, "status-get")
    assert excinfo.value.kind == "protocol"


def test_socket_path_refuses_a_directory_part():
    assert kc.socket_path("/run/kea", "kea4-ctrl.sock") == os.path.join("/run/kea", "kea4-ctrl.sock")
    for bad in ("", "../x.sock", "a/b.sock", "/abs.sock"):
        with pytest.raises(ValueError):
            kc.socket_path("/run/kea", bad)
