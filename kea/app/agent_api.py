"""The HTTP API for scripts and AI agents (docs/agent-api.md).

A second web application on its own port, separate from the Ingress Web UI. It is only started when the owner switches it on.
Every request needs a Bearer token; what a token may do is decided by its scopes; clients outside the allowed networks are
turned away before the token is even looked at. All answers are JSON in one envelope.
"""
import collections
import re
import threading
import time
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from manager import LOG_SOURCES
from proposals import ProposalError

API_PREFIX = "/v1"
MAX_BODY = 1024 * 1024
WRITE_LIMIT_PER_MINUTE = 30
READ_LIMIT_PER_MINUTE = 300
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
MAX_HOSTNAME = 253
MAX_LOG_LINE = 2000


def clean_text(value, limit):
    """Text that came from the network: no control characters, bounded length."""
    return _CONTROL_CHARS.sub(" ", str(value or ""))[:limit]


class ApiError(Exception):
    def __init__(self, status, code, message, details=None, headers=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details or [], headers or {}


def error_response(status, code, message, details=None, headers=None):
    return JSONResponse({"error": {"code": code, "message": message, "details": details or []}},
                        status_code=status, headers=headers)


class RateLimiter:
    """At most *limit* calls per token per minute (sliding window, in memory)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._calls = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def check(self, key, limit):
        """Seconds to wait when *key* is over *limit*, else 0 (and the call is counted)."""
        now = self.clock()
        with self._lock:
            calls = self._calls[key]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= limit:
                return max(1, int(60 - (now - calls[0])) + 1)
            calls.append(now)
            return 0


# ------------------------------------------------------------------ request bodies
class ChangeBody(BaseModel):
    """A change as a whole settings document *or* as a list of operations (see docs/agent-api.md)."""
    model_config = ConfigDict(extra="forbid")
    settings: Optional[dict] = Field(None, description="The complete new settings (as returned by GET /v1/settings)")
    operations: Optional[list] = Field(None, description="Granular operations such as add_reservation")
    note: str = Field("", max_length=500, description="Why, in a sentence; kept in the history")


class OperationsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list
    note: str = Field("", max_length=500)


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict
    note: str = Field("", max_length=500)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(..., max_length=64)
    note: str = Field("", max_length=500)


class NtpCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servers: list
    version: int = 4


def create_agent_app(manager, clock=time.time):
    config = manager.apiconfig
    limiter = RateLimiter(clock)
    app = FastAPI(title="Kea DHCP app API", version="1", docs_url=None, redoc_url=None, openapi_url=None,
                  description="API for scripts and AI agents. See docs/agent-api.md in the repository.")

    # --------------------------------------------------------------- plumbing
    @app.exception_handler(ApiError)
    async def _api_error(_request, exc):
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_request, exc):
        details = [f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()]
        return error_response(422, "validation_failed", "the request is not valid", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request, exc):
        codes = {404: "not_found", 405: "method_not_allowed", 413: "too_large"}
        return error_response(exc.status_code, codes.get(exc.status_code, "error"), str(exc.detail))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.client.host if request.client else ""
        request.state.token_name = None
        if not config.ip_allowed(host):
            response = error_response(403, "forbidden", f"{host} is not in the networks allowed to use the API")
        else:
            length = request.headers.get("content-length")
            if "chunked" in (request.headers.get("transfer-encoding") or "").lower():
                response = error_response(411, "error", "chunked request bodies are not accepted")
            elif length and length.isdigit() and int(length) > MAX_BODY:
                response = error_response(413, "too_large", "the request body is too large")
            else:
                response = await call_next(request)
        manager.audit.record(token=request.state.token_name, ip=host, method=request.method,
                             path=request.url.path, status=response.status_code)
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    def require(scope):
        def dependency(request: Request):
            scheme, _, value = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not value.strip():
                raise ApiError(401, "unauthorized", "send the token as 'Authorization: Bearer <token>'",
                               headers={"WWW-Authenticate": "Bearer"})
            token = config.authenticate(value.strip())
            if token is None:
                raise ApiError(401, "unauthorized", "the token is not valid", headers={"WWW-Authenticate": "Bearer"})
            request.state.token_name = token["name"]
            if scope not in token["scopes"]:
                raise ApiError(403, "forbidden", f"this token does not have the '{scope}' scope")
            write = request.method not in ("GET", "HEAD")
            wait = limiter.check((token["id"], write), WRITE_LIMIT_PER_MINUTE if write else READ_LIMIT_PER_MINUTE)
            if wait:
                raise ApiError(429, "rate_limited", f"too many requests; try again in {wait} s",
                               headers={"Retry-After": str(wait)})
            return token
        return Depends(dependency)

    def ok(data, status=200, **meta):
        return JSONResponse({"data": data, "meta": meta}, status_code=status)

    def fail(result, default_code="validation_failed"):
        """Turn a manager result that says ``ok: False`` into the matching error."""
        errors = result.get("errors") or ["the change was refused"]
        if result.get("stale"):
            raise ApiError(412, "stale_revision", errors[0], [f"current revision: {result.get('revision')}"])
        code = result.get("code")
        if code == "not_found":
            raise ApiError(404, "not_found", errors[0])
        if code in ("stale", "not_pending"):
            raise ApiError(409, "stale_revision" if code == "stale" else "conflict", errors[0])
        if result.get("kind") == "kea":
            raise ApiError(409, "kea_refused", "Kea did not accept the change; nothing was changed", errors)
        raise ApiError(422, default_code, errors[0], errors)

    def need_revision(if_match):
        if not if_match:
            raise ApiError(428, "precondition_required",
                           "send the revision you based the change on as the If-Match header (see GET /v1/settings)")
        return if_match.strip().strip('"')

    # --------------------------------------------------------------- read
    @app.get(API_PREFIX + "/health", summary="State of Kea and the app", dependencies=[require("read")])
    def health():
        return ok(manager.health())

    @app.get(API_PREFIX + "/settings", summary="The saved settings and their revision", dependencies=[require("read")])
    def settings():
        return ok(manager.settings, revision=manager.revision())

    @app.get(API_PREFIX + "/leases", summary="Active leases", dependencies=[require("read")])
    def leases(family: Optional[str] = Query(None, pattern="^(dhcp4|dhcp6)$"),
               q: Optional[str] = Query(None, max_length=100)):
        data = manager.get_leases()
        rows = []
        for fam in ("dhcp4", "dhcp6"):
            if family and family != fam:
                continue
            for lease in data[fam]:
                row = dict(lease, family=fam, hostname=clean_text(lease.get("hostname"), MAX_HOSTNAME))
                if q and q.lower() not in " ".join(str(row.get(k) or "") for k in ("address", "identifier", "hostname")).lower():
                    continue
                rows.append(row)
        return ok(rows, count=len(rows), untrusted_fields=["data[].hostname"])

    @app.get(API_PREFIX + "/utilization", summary="Pool usage per subnet", dependencies=[require("read")])
    def utilization():
        return ok(manager.utilization())

    @app.get(API_PREFIX + "/logs", summary="Recent Kea and app log lines", dependencies=[require("read")])
    def logs(since: int = Query(0, ge=0), source: Optional[str] = None, limit: int = Query(200, ge=1, le=1000)):
        if source and source not in LOG_SOURCES:
            raise ApiError(422, "validation_failed", f"source must be one of: {', '.join(LOG_SOURCES)}")
        got = manager.get_logs(since=since, source=source or None, limit=limit)
        entries = [dict(e, line=clean_text(e["line"], MAX_LOG_LINE)) for e in got["entries"]]
        return ok(entries, last_seq=got["last_seq"], untrusted_fields=["data[].line"])

    @app.get(API_PREFIX + "/ntp", summary="NTP host names and the addresses sent for them", dependencies=[require("read")])
    def ntp():
        return ok(manager.ntp_status())

    @app.post(API_PREFIX + "/ntp/check", summary="Check NTP servers (nothing is saved)", dependencies=[require("read")])
    def ntp_check(body: NtpCheckBody):
        result = manager.check_ntp(body.servers, body.version)
        if not result["ok"]:
            raise ApiError(422, "validation_failed", result["errors"][0], result["errors"])
        return ok({"usable": result["usable"], "report": result["report"], "warning": result["warning"]})

    @app.get(API_PREFIX + "/history", summary="Saved versions of the settings", dependencies=[require("read")])
    def history():
        return ok(manager.history.list(), current=manager.revision())

    @app.get(API_PREFIX + "/history/{revision}", summary="One saved version", dependencies=[require("read")])
    def history_one(revision: str):
        version = manager.history.get(revision)
        if version is None:
            raise ApiError(404, "not_found", f"no saved version {revision}")
        return ok(version)

    @app.get(API_PREFIX + "/proposals", summary="Proposals", dependencies=[require("read")])
    def proposals(status: Optional[str] = Query(None, pattern="^(pending|applied|rejected|failed)$")):
        return ok(manager.proposals.list(status))

    @app.get(API_PREFIX + "/proposals/{proposal_id}", summary="One proposal", dependencies=[require("read")])
    def proposal(proposal_id: str):
        try:
            return ok(manager.proposals.get(proposal_id))
        except ProposalError as exc:
            raise ApiError(404, "not_found", str(exc)) from None

    @app.get(API_PREFIX + "/audit", summary="Recent API calls", dependencies=[require("read")])
    def audit(limit: int = Query(100, ge=1, le=1000)):
        return ok(manager.audit.tail(limit))

    @app.get(API_PREFIX + "/openapi.json", summary="This API, machine readable", dependencies=[require("read")])
    def openapi_document():
        return JSONResponse(app.openapi())

    # --------------------------------------------------------------- propose
    @app.post(API_PREFIX + "/validate", summary="Check a change without applying it", dependencies=[require("propose")])
    def validate(body: ChangeBody):
        result = manager.check_change(body.settings, body.operations)
        result.pop("settings", None)
        return ok(result)

    @app.post(API_PREFIX + "/proposals", summary="Propose a change for a person to approve", status_code=201,
              dependencies=[require("propose")])
    def propose(request: Request, body: ChangeBody):
        result = manager.propose_change(request.state.token_name, body.note, body.settings, body.operations)
        if not result["ok"]:
            fail(result)
        return ok(result["proposal"], status=201)

    # --------------------------------------------------------------- apply
    @app.put(API_PREFIX + "/settings", summary="Apply new settings (needs If-Match)", dependencies=[require("apply")])
    def put_settings(request: Request, body: SettingsBody, if_match: Optional[str] = Header(None)):
        revision = need_revision(if_match)
        result = manager.apply(body.settings, source=f"api:{request.state.token_name}", note=body.note,
                               expected_revision=revision)
        if not result["ok"]:
            fail(result)
        return ok({"revision": result["revision"], "action": result["action"], "warnings": result.get("warnings", [])})

    @app.post(API_PREFIX + "/operations", summary="Apply granular operations (needs If-Match)",
              dependencies=[require("apply")])
    def post_operations(request: Request, body: OperationsBody, if_match: Optional[str] = Header(None)):
        revision = need_revision(if_match)
        result = manager.apply_operations(body.operations, source=f"api:{request.state.token_name}", note=body.note,
                                          expected_revision=revision)
        if not result["ok"]:
            fail(result)
        return ok({"revision": result["revision"], "action": result["action"], "warnings": result.get("warnings", [])})

    @app.post(API_PREFIX + "/rollback", summary="Apply an older saved version", dependencies=[require("apply")])
    def rollback(request: Request, body: RollbackBody):
        result = manager.rollback(body.revision, source=f"api:{request.state.token_name}", note=body.note)
        if not result["ok"]:
            fail(result)
        return ok({"revision": result["revision"], "action": result["action"]})

    # --------------------------------------------------------------- the rest
    @app.delete(API_PREFIX + "/leases/{family}/{address}", summary="Delete a lease", dependencies=[require("lease-delete")])
    def delete_lease(family: str, address: str):
        result = manager.delete_lease(family, address)
        if not result["ok"]:
            if any(e.startswith("no lease found") for e in result["errors"]):
                raise ApiError(404, "not_found", result["errors"][0])
            raise ApiError(422, "validation_failed", result["errors"][0], result["errors"])
        return ok({"deleted": address})

    @app.post(API_PREFIX + "/restart", summary="Restart Kea", dependencies=[require("restart")])
    def restart():
        result = manager.restart()
        if not result["ok"]:
            raise ApiError(409, "kea_refused", result["errors"][0], result["errors"])
        return ok(manager.health())

    return app


class AgentApiServer:
    """Runs :func:`create_agent_app` on its own port in a thread, started and stopped as the owner switches it."""

    def __init__(self, manager, bind="0.0.0.0"):
        self.manager = manager
        self.bind = bind
        self.server = None
        self.thread = None
        self.port = None
        self.error = ""
        self._lock = threading.Lock()

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and self.server and self.server.started)

    def apply_config(self):
        """Make the server match api.json: running on the configured port when enabled, stopped otherwise."""
        with self._lock:
            config = self.manager.apiconfig
            if self.running and (not config.enabled or self.port != config.port):
                self._stop()
            if config.enabled and not self.running:
                self._start(config.port)
            return self.status()

    def _start(self, port):
        self.error = ""
        server = uvicorn.Server(uvicorn.Config(create_agent_app(self.manager), host=self.bind, port=port,
                                               log_level="warning", access_log=False, proxy_headers=False))
        def run():
            try:
                server.run()
            except SystemExit:                       # uvicorn exits like that when it cannot bind the port
                pass
        thread = threading.Thread(target=run, daemon=True, name="agent-api")
        thread.start()
        deadline = time.time() + 8
        while time.time() < deadline and thread.is_alive() and not server.started:
            time.sleep(0.05)
        if not (thread.is_alive() and server.started):
            self.error = f"the API could not listen on port {port} (is it already in use?)"
            server.should_exit = True
            return
        self.server, self.thread, self.port = server, thread, port
        self.manager._log(f"agent API listening on port {port}")

    def _stop(self):
        server, thread = self.server, self.thread
        self.server = self.thread = self.port = None
        if server:
            server.should_exit = True
        if thread:
            thread.join(10)
        self.manager._log("agent API stopped")

    def stop(self):
        with self._lock:
            self._stop()

    def status(self):
        return {"running": self.running, "port": self.port, "error": self.error}
