"""Settings and tokens of the agent API (``api.json`` next to settings.json, mode 0600).

A token is shown once, when it is created; only its SHA-256 hash is stored (the tokens are long random strings, so a plain
hash is enough). Scopes decide what a token may do, see docs/agent-api.md. This module has no web code.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time

SCOPES = ("read", "propose", "apply", "lease-delete", "restart")
DEFAULT_PORT = 8100
UI_PORT = 8099                                   # the Ingress Web UI; the API must not take it
DEFAULT_CIDRS = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "::1/128"]
MAX_TOKENS = 20
MAX_CIDRS = 20
TOKEN_PREFIX = "kea_"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
LAST_USED_RESOLUTION = 60                        # seconds: do not rewrite the file on every request


class ApiConfigError(ValueError):
    pass


def _hash(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class ApiConfig:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self._data = {"enabled": False, "port": DEFAULT_PORT, "allowed_cidrs": list(DEFAULT_CIDRS), "tokens": []}
        self.load()

    # ----------------------------------------------------------- storage
    def load(self):
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (OSError, ValueError):
                return
            if not isinstance(raw, dict):
                return
            data = self._data
            data["enabled"] = raw.get("enabled") is True
            port = raw.get("port")
            data["port"] = port if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535 else DEFAULT_PORT
            cidrs = [c for c in raw.get("allowed_cidrs", []) if isinstance(c, str) and self._cidr(c)]
            data["allowed_cidrs"] = cidrs or list(DEFAULT_CIDRS)
            data["tokens"] = [t for t in raw.get("tokens", []) if isinstance(t, dict) and t.get("id") and t.get("hash")
                              and isinstance(t.get("scopes"), list)]

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _cidr(text):
        try:
            return ipaddress.ip_network(text.strip(), strict=False)
        except ValueError:
            return None

    # ----------------------------------------------------------- settings
    @property
    def enabled(self):
        return self._data["enabled"]

    @property
    def port(self):
        return self._data["port"]

    def public(self):
        """Everything the Web UI may show: no hashes."""
        with self._lock:
            d = self._data
            return {"enabled": d["enabled"], "port": d["port"], "allowed_cidrs": list(d["allowed_cidrs"]),
                    "scopes": list(SCOPES), "tokens": [self._public_token(t) for t in d["tokens"]]}

    @staticmethod
    def _public_token(t):
        return {"id": t["id"], "name": t.get("name", ""), "scopes": [s for s in t["scopes"] if s in SCOPES],
                "created": t.get("created"), "last_used": t.get("last_used")}

    def set_config(self, enabled, port, cidrs):
        """Validate and store the switch, the port and the allowed networks. Raises ApiConfigError."""
        if not isinstance(enabled, bool):
            raise ApiConfigError("'enabled' must be true or false")
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ApiConfigError("the port must be a number between 1024 and 65535")
        if port == UI_PORT:
            raise ApiConfigError(f"port {UI_PORT} is used by the Web UI")
        if isinstance(cidrs, str):
            cidrs = [c for c in re.split(r"[,\s]+", cidrs.strip()) if c]
        if not isinstance(cidrs, list) or not cidrs or len(cidrs) > MAX_CIDRS:
            raise ApiConfigError(f"give 1 to {MAX_CIDRS} networks, for example 192.168.1.0/24")
        clean = []
        for item in cidrs:
            net = self._cidr(item) if isinstance(item, str) else None
            if net is None:
                raise ApiConfigError(f"not a network: {item!r} (write it like 192.168.1.0/24)")
            if net.prefixlen == 0:
                raise ApiConfigError("0.0.0.0/0 would allow every address; list the networks you really use")
            clean.append(str(net))
        with self._lock:
            self._data.update(enabled=enabled, port=port, allowed_cidrs=clean)
            self._save()

    def ip_allowed(self, address):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        with self._lock:
            return any(ip.version == net.version and ip in net
                       for net in (self._cidr(c) for c in self._data["allowed_cidrs"]) if net)

    # ----------------------------------------------------------- tokens
    def create_token(self, name, scopes):
        """``(public record, secret)``. The secret is not stored and cannot be shown again."""
        if not isinstance(name, str) or not _NAME_RE.match(name.strip()):
            raise ApiConfigError("the name must be 1 to 40 letters, digits, spaces, dots, dashes or underscores")
        name = name.strip()
        if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
            raise ApiConfigError(f"scopes must be from: {', '.join(SCOPES)}")
        scopes = [s for s in SCOPES if s in scopes or s == "read"]          # read is always included
        with self._lock:
            if len(self._data["tokens"]) >= MAX_TOKENS:
                raise ApiConfigError(f"at most {MAX_TOKENS} tokens; delete one you no longer use")
            if any(t.get("name", "").lower() == name.lower() for t in self._data["tokens"]):
                raise ApiConfigError(f"there already is a token named {name!r}")
            secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
            record = {"id": secrets.token_hex(4), "name": name, "scopes": scopes, "hash": _hash(secret),
                      "created": int(self.clock()), "last_used": None}
            self._data["tokens"].append(record)
            self._save()
            return self._public_token(record), secret

    def delete_token(self, token_id):
        with self._lock:
            before = len(self._data["tokens"])
            self._data["tokens"] = [t for t in self._data["tokens"] if t["id"] != token_id]
            if len(self._data["tokens"]) == before:
                return False
            self._save()
            return True

    def authenticate(self, secret):
        """The public record of the token with this secret, or None. Compares in constant time."""
        if not isinstance(secret, str) or not secret.startswith(TOKEN_PREFIX) or len(secret) > 200:
            return None
        digest = _hash(secret)
        found = None
        with self._lock:
            for token in self._data["tokens"]:
                if hmac.compare_digest(token["hash"], digest):
                    found = token
            if found is None:
                return None
            now = int(self.clock())
            if found.get("last_used") is None or now - found["last_used"] >= LAST_USED_RESOLUTION:
                found["last_used"] = now
                try:
                    self._save()
                except OSError:
                    pass
            return self._public_token(found)
