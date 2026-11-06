"""Talk to a running Kea daemon over its control channel (a unix socket, JSON in, JSON out).

Kea 3.0 serves commands such as ``config-reload``, ``status-get``, ``statistic-get-all`` itself, and
``lease4-get-all``, ``lease4-del`` and friends once the ``libdhcp_lease_cmds.so`` hook is loaded. One command is
sent per connection and the daemon answers with one JSON document. Standard library only.

Kea 3.0 only accepts control sockets in one directory (``KEA_CONTROL_SOCKET_DIR``, mode 0750), which is why callers
pass a socket *file name* and this module joins it to that directory.
"""
import json
import os
import socket

DEFAULT_TIMEOUT = 5.0
MAX_RESPONSE = 64 * 1024 * 1024          # a big lease table is a few MB; anything above this is not a Kea answer

# "result" codes of a Kea command answer
RESULT_OK = 0
RESULT_ERROR = 1
RESULT_UNSUPPORTED = 2
RESULT_EMPTY = 3                         # nothing matched (for example lease4-del of an address with no lease)
RESULT_CONFLICT = 4
RESULT_PARTIAL = 5                       # the command failed after changing part of the running configuration


class KeaControlError(Exception):
    """The command could not be delivered or the answer was not understood (not: Kea said no, see :class:`Reply`)."""

    def __init__(self, message, kind="unavailable"):
        super().__init__(message)
        self.kind = kind                 # unavailable | timeout | protocol


class Reply:
    """A Kea answer: ``result`` (see RESULT_*), ``text`` and ``arguments``."""

    def __init__(self, raw):
        if isinstance(raw, list) and len(raw) == 1:      # answers relayed by the Control Agent are wrapped in a list
            raw = raw[0]
        if not isinstance(raw, dict) or not isinstance(raw.get("result"), int):
            raise KeaControlError("the answer is not a Kea command answer", "protocol")
        self.result = raw["result"]
        self.text = str(raw.get("text") or "")
        arguments = raw.get("arguments")
        self.arguments = arguments if isinstance(arguments, dict) else {}

    @property
    def ok(self):
        return self.result == RESULT_OK

    def __repr__(self):
        return f"Reply(result={self.result}, text={self.text!r})"


def socket_path(directory, name):
    """The socket file for *name* inside Kea's control socket directory. A name with a path part is refused."""
    if not name or os.path.basename(name) != name:
        raise ValueError("the socket name must be a file name without a directory")
    return os.path.join(directory, name)


def command(path, name, arguments=None, timeout=DEFAULT_TIMEOUT):
    """Send one command to the daemon listening on the unix socket *path* and return its :class:`Reply`."""
    request = {"command": name}
    if arguments is not None:
        request["arguments"] = arguments
    payload = json.dumps(request).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(payload)
            chunks, size = [], 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise KeaControlError("the answer is too large", "protocol")
                try:                                           # Kea may keep the connection open: stop at a full document
                    return Reply(json.loads(b"".join(chunks)))
                except ValueError:
                    continue
    except socket.timeout:
        raise KeaControlError(f"no answer to {name} within {timeout:g} s", "timeout") from None
    except OSError as exc:
        raise KeaControlError(f"cannot reach the control socket: {exc.strerror or exc}", "unavailable") from None
    data = b"".join(chunks)
    if not data:
        raise KeaControlError("the daemon closed the connection without an answer", "protocol")
    try:
        return Reply(json.loads(data))
    except ValueError:
        raise KeaControlError("the answer is not valid JSON", "protocol") from None
