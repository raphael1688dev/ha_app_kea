"""A stand-in for a Kea daemon's control socket: answers JSON commands on a unix socket from a Python handler.

Socket paths are limited to about 100 characters, so the socket lives in a short temporary directory, not in pytest's
(long) tmp_path.
"""
import json
import os
import shutil
import socket
import tempfile
import threading


class FakeControl:
    def __init__(self, handler=None, name="kea4-ctrl.sock"):
        self.dir = tempfile.mkdtemp(prefix="kc-")
        os.chmod(self.dir, 0o750)
        self.path = os.path.join(self.dir, name)
        self.name = name
        self.handler = handler or (lambda command, arguments: {"result": 0, "text": "ok"})
        self.calls = []                     # (command, arguments) in the order received
        self.mode = "normal"                # normal | silent | garbage | empty | list | partial
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                try:
                    conn.settimeout(2)
                    data = conn.recv(1 << 20)
                    request = json.loads(data)
                except (ValueError, OSError):
                    continue
                self.calls.append((request.get("command"), request.get("arguments")))
                if self.mode == "silent":
                    threading.Event().wait(1.0)
                    continue
                if self.mode == "empty":
                    continue
                if self.mode == "garbage":
                    conn.sendall(b"not json at all")
                    continue
                answer = self.handler(request.get("command"), request.get("arguments"))
                body = json.dumps([answer] if self.mode == "list" else answer).encode()
                if self.mode == "partial":              # two writes with a pause: the client must wait for the rest
                    conn.sendall(body[:5])
                    threading.Event().wait(0.1)
                    conn.sendall(body[5:])
                else:
                    conn.sendall(body)

    def close(self):
        self.stop = True
        self.thread.join(1)
        self.sock.close()
        shutil.rmtree(self.dir, ignore_errors=True)
