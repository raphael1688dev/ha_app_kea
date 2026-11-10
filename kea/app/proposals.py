"""Changes that a script or an agent *proposes* and a person approves in the Web UI (``proposals.json``).

A proposal holds the complete settings it would produce, the revision it was made against, and a plain description of what
it changes. It can only be applied while the settings still have that revision; otherwise it is ``stale``.
"""
import json
import os
import secrets
import threading
import time

EXPIRE_SECONDS = 7 * 24 * 3600
MAX_PROPOSALS = 100                      # finished ones are dropped first when this is exceeded
MAX_PENDING = 20                         # undecided ones; each holds a full copy of the settings
PENDING, APPLIED, REJECTED, FAILED = "pending", "applied", "rejected", "failed"


class ProposalError(Exception):
    def __init__(self, message, code="invalid"):
        super().__init__(message)
        self.code = code                 # invalid | not_found | stale | not_pending | too_many


class Proposals:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self._items = []
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(raw, list):
            self._items = [p for p in raw if isinstance(p, dict) and p.get("id") and p.get("settings")]

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._items, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _expire(self):
        now = self.clock()
        for p in self._items:
            if p["status"] == PENDING and now - p["created"] > EXPIRE_SECONDS:
                p.update(status=REJECTED, decided=now, result="expired after 7 days without a decision")

    @staticmethod
    def _summary(p):
        return {k: p.get(k) for k in ("id", "created", "token", "note", "base_revision", "changes", "status", "decided", "result")}

    def create(self, token_name, settings, base_revision, changes, note=""):
        with self._lock:
            self._expire()
            if sum(1 for p in self._items if p["status"] == PENDING) >= MAX_PENDING:
                raise ProposalError(f"{MAX_PENDING} proposals are already waiting for a decision; "
                                    "they have to be approved or rejected in the Web UI first", "too_many")
            record = {"id": secrets.token_hex(4), "created": int(self.clock()), "token": token_name,
                      "note": str(note or "")[:500], "base_revision": base_revision, "changes": list(changes),
                      "settings": settings, "status": PENDING, "decided": None, "result": ""}
            self._items.append(record)
            finished = [p for p in self._items if p["status"] != PENDING]
            while len(self._items) > MAX_PROPOSALS and finished:
                self._items.remove(finished.pop(0))
            self._save()
            return self._summary(record)

    def list(self, status=None):
        """Summaries (no settings), newest first."""
        with self._lock:
            self._expire()
            return [self._summary(p) for p in reversed(self._items) if status is None or p["status"] == status]

    def get(self, proposal_id, with_settings=False):
        with self._lock:
            self._expire()
            for p in self._items:
                if p["id"] == proposal_id:
                    return dict(p) if with_settings else self._summary(p)
        raise ProposalError(f"no proposal {proposal_id}", "not_found")

    def decide(self, proposal_id, status, result="", current_revision=None):
        """Mark a pending proposal applied / rejected / failed. Applying needs the settings to be unchanged."""
        with self._lock:
            self._expire()
            for p in self._items:
                if p["id"] != proposal_id:
                    continue
                if p["status"] != PENDING:
                    raise ProposalError(f"proposal {proposal_id} is already {p['status']}", "not_pending")
                if status == APPLIED and current_revision != p["base_revision"]:
                    raise ProposalError("the settings changed after this proposal was made; ask for a new one", "stale")
                p.update(status=status, decided=int(self.clock()), result=str(result)[:1000])
                self._save()
                return self._summary(p)
        raise ProposalError(f"no proposal {proposal_id}", "not_found")

    def is_stale(self, proposal_id, current_revision):
        with self._lock:
            for p in self._items:
                if p["id"] == proposal_id:
                    return p["status"] == PENDING and p["base_revision"] != current_revision
        return False
