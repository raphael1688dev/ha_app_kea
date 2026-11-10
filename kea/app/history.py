"""Saved versions of the settings (so a change can be rolled back) and the audit log of API calls.

A settings *revision* is a short hash of the settings content, so it needs no counter and cannot get out of step:
two clients that read the same settings see the same revision, and any change gives a new one.
"""
import hashlib
import json
import os
import threading
import time

KEEP_VERSIONS = 50
AUDIT_MAX_BYTES = 2 * 1024 * 1024
REVISION_LENGTH = 12


def revision_of(settings):
    """A stable short identifier of the settings content (key order and whitespace do not matter)."""
    text = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:REVISION_LENGTH]


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class History:
    """Numbered copies of the settings (file names start with a counter). Only the last KEEP_VERSIONS are kept."""

    def __init__(self, directory, keep=KEEP_VERSIONS, clock=time.time):
        self.dir = os.path.join(directory, "history")
        self.keep = keep
        self.clock = clock
        self._lock = threading.Lock()

    def _files(self):
        try:
            return sorted(n for n in os.listdir(self.dir) if n.endswith(".json"))
        except OSError:
            return []

    def save(self, settings, source, note=""):
        """Store *settings*. Returns the record, or None when the newest version already is this content."""
        revision = revision_of(settings)
        with self._lock:
            files = self._files()
            if files and files[-1].endswith(f"-{revision}.json"):
                return None
            os.makedirs(self.dir, exist_ok=True)
            now = self.clock()
            record = {"revision": revision, "time": now, "source": str(source)[:120], "note": str(note or "")[:500]}
            last = int(files[-1].split("-", 1)[0]) if files and files[-1].split("-", 1)[0].isdigit() else 0
            name = f"{last + 1:010d}-{revision}.json"          # a counter, not the clock: two saves can share a millisecond
            _write_json(os.path.join(self.dir, name), dict(record, settings=settings))
            for old in self._files()[:-self.keep]:
                try:
                    os.remove(os.path.join(self.dir, old))
                except OSError:
                    pass
            return record

    def list(self):
        """Records (without the settings), newest first."""
        out = []
        for name in reversed(self._files()):
            data = self._read(name)
            if data:
                out.append({k: data.get(k) for k in ("revision", "time", "source", "note")})
        return out

    def get(self, revision):
        """The newest saved version with this revision (record plus ``settings``), or None."""
        for name in reversed(self._files()):
            if name.endswith(f"-{revision}.json"):
                return self._read(name)
        return None

    def _read(self, name):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) and "revision" in data else None
        except (OSError, ValueError):
            return None


class Audit:
    """An append-only JSON-lines log. It never gets tokens or request bodies, only who did what and the outcome."""

    def __init__(self, path, max_bytes=AUDIT_MAX_BYTES, clock=time.time):
        self.path = path
        self.max_bytes = max_bytes
        self.clock = clock
        self._lock = threading.Lock()

    def record(self, **entry):
        entry = dict(time=round(self.clock(), 3), **entry)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass                     # auditing must never break the request it describes

    def tail(self, limit=100):
        """The newest *limit* entries, newest first."""
        limit = max(1, min(int(limit), 1000))
        lines = []
        for path in (self.path + ".1", self.path):
            try:
                with open(path, encoding="utf-8") as fh:
                    lines += fh.read().splitlines()
            except OSError:
                pass
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))
