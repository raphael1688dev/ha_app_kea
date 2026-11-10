"""FastAPI server for the Home Assistant Ingress Web UI.

Security model: the port is only meant to be reached through the Supervisor's
Ingress proxy, so every request must originate from the Supervisor
(172.30.32.2). Anything else gets 403. Mutating endpoints additionally require
``Content-Type: application/json`` so a cross-site HTML form cannot trigger them.
"""
import json
import os
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from agent_api import AgentApiServer
from apiauth import ApiConfigError
from manager import LOG_SOURCES, KeaManager, load_options, log

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "static", "index.html")
MAX_BODY = 1024 * 1024


def require_json(request: Request):
    media_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def create_app(manager, allowed_ips, agent=None):
    """The Ingress Web UI. *agent* is the :class:`AgentApiServer` that the "API access" card switches on and off."""
    @asynccontextmanager
    async def lifespan(_app):
        manager.startup()
        threading.Thread(target=manager.watch, daemon=True).start()
        threading.Thread(target=manager.ntp_loop, daemon=True).start()
        if agent:
            agent.apply_config()                              # starts the API when the owner switched it on
        yield
        log("termination signal received, stopping Kea and the Web UI...")
        if agent:
            agent.stop()
        manager.shutdown()

    app = FastAPI(
        title="Kea DHCP", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    @app.middleware("http")
    async def supervisor_only(request: Request, call_next):
        host = request.client.host if request.client else ""
        if host not in allowed_ips:
            return JSONResponse({"error": f"forbidden ({host})"}, status_code=403)
        if "chunked" in (request.headers.get("transfer-encoding") or "").lower():
            return JSONResponse({"error": "chunked request bodies are not accepted"}, status_code=411)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"error": "body too large"}, status_code=413)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    # Routes are plain `def` on purpose: FastAPI runs them in a worker thread,
    # and the manager calls block (subprocess, locks).
    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index():
        return FileResponse(
            INDEX_HTML, media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"})

    @app.get("/api/state")
    def state():
        return {"status": manager.status(), "settings": manager.settings, "revision": manager.revision()}

    @app.get("/api/status")
    def status():
        return manager.status()

    @app.get("/api/leases")
    def leases():
        return manager.get_leases()

    @app.put("/api/settings", dependencies=[Depends(require_json)])
    def put_settings(data: dict = Body(...), revision: str = ""):
        # The page says which version its editor was filled from; if the settings were changed since (through the
        # API, or in another browser window) the save is refused instead of silently undoing that change.
        result = manager.apply(data, expected_revision=revision or None)
        if result.get("stale"):
            result["errors"] = ["The settings were changed somewhere else (another browser window or the API) after this "
                                "page loaded them, so nothing was saved. Click \"Discard changes\" to load the current "
                                "settings, then make your change again."]
            return JSONResponse(result, status_code=409)
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.post("/api/restart", dependencies=[Depends(require_json)])
    def restart():
        result = manager.restart()
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.delete("/api/leases/{family}/{address}", dependencies=[Depends(require_json)])
    def delete_lease(family: str, address: str):
        result = manager.delete_lease(family, address)
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.get("/api/logs")
    def logs(since: int = 0, source: str = "", limit: int = 500):
        if source and source not in LOG_SOURCES:
            raise HTTPException(400, "unknown log source")
        return manager.get_logs(since=since, source=source or None, limit=limit)

    @app.get("/api/interfaces")
    def interfaces():
        return manager.list_interfaces()

    @app.get("/api/backup")
    def backup():
        body = json.dumps(manager.settings, ensure_ascii=False, indent=2)
        return Response(
            body, media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="kea-settings.json"'})

    @app.post("/api/import", dependencies=[Depends(require_json)])
    def import_settings(data: dict = Body(...)):
        result = manager.import_preview(data)
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.post("/api/import/dhcp-server", dependencies=[Depends(require_json)])
    def import_dhcp_server(data: dict = Body(...)):
        result = manager.import_dhcp_server(data.get("text"))
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.get("/api/host-addresses")
    def host_addresses():
        return manager.host_addresses()

    @app.get("/api/ntp")
    def ntp_state():
        return manager.ntp_status()

    @app.post("/api/ntp/check", dependencies=[Depends(require_json)])
    def ntp_check(data: dict = Body(...)):
        result = manager.check_ntp(data.get("servers"), data.get("version", 4))
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.post("/api/ntp/refresh", dependencies=[Depends(require_json)])
    def ntp_refresh():
        result = manager.refresh_ntp(force=True)
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    # ---------------------------------------------------- API access (Settings tab)
    def agent_state():
        return {"config": manager.apiconfig.public(),
                "server": agent.status() if agent else {"running": False, "port": None, "error": ""},
                "proposals": manager.proposals.list(), "history": manager.history.list()[:20],
                "revision": manager.revision()}

    @app.get("/api/agent")
    def get_agent():
        return agent_state()

    @app.put("/api/agent/config", dependencies=[Depends(require_json)])
    def put_agent_config(data: dict = Body(...)):
        try:
            manager.apiconfig.set_config(data.get("enabled"), data.get("port"), data.get("allowed_cidrs"))
        except ApiConfigError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=400)
        if agent:
            agent.apply_config()
        return dict(agent_state(), ok=True)

    @app.post("/api/agent/tokens", dependencies=[Depends(require_json)])
    def create_agent_token(data: dict = Body(...)):
        try:
            record, secret = manager.apiconfig.create_token(data.get("name"), data.get("scopes"))
        except ApiConfigError as exc:
            return JSONResponse({"ok": False, "errors": [str(exc)]}, status_code=400)
        return {"ok": True, "token": record, "secret": secret}

    @app.delete("/api/agent/tokens/{token_id}", dependencies=[Depends(require_json)])
    def delete_agent_token(token_id: str):
        if not manager.apiconfig.delete_token(token_id):
            return JSONResponse({"ok": False, "errors": ["no such token"]}, status_code=404)
        return {"ok": True}

    @app.get("/api/agent/proposals/{proposal_id}")
    def get_agent_proposal(proposal_id: str):
        try:
            return manager.proposals.get(proposal_id)
        except Exception as exc:                              # ProposalError
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/agent/proposals/{proposal_id}/approve", dependencies=[Depends(require_json)])
    def approve_agent_proposal(proposal_id: str):
        result = manager.approve_proposal(proposal_id, approver="Web UI")
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.post("/api/agent/proposals/{proposal_id}/reject", dependencies=[Depends(require_json)])
    def reject_agent_proposal(proposal_id: str):
        result = manager.reject_proposal(proposal_id)
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    @app.post("/api/history/{revision}/rollback", dependencies=[Depends(require_json)])
    def rollback_to(revision: str):
        result = manager.rollback(revision, source="Web UI")
        return JSONResponse(result, status_code=200 if result["ok"] else 400)

    return app


def make_config(app, host, port):
    """uvicorn settings. proxy_headers=False is a security setting: by default uvicorn replaces the client
    address with the X-Forwarded-For header of connections from 127.0.0.1, which would let any process on this
    (host-network) machine pass itself off as the Supervisor and defeat the source-address check."""
    return uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False,
                          proxy_headers=False)


def main():
    options = load_options(os.environ.get("OPTIONS_FILE", "/data/options.json"))
    manager = KeaManager(
        options,
        conf_dir=os.environ.get("KEA_CONF_DIR", "/etc/kea"),
        data_dir=os.environ.get("KEA_DATA_DIR", "/config/kea"),
    )
    allowed = {ip.strip() for ip in
               os.environ.get("KEA_UI_ALLOWED", "172.30.32.2").split(",") if ip.strip()}
    port = int(os.environ.get("KEA_UI_PORT", "8099"))
    bind = os.environ.get("KEA_UI_BIND", "0.0.0.0")

    log(f"Web UI listening on {bind}:{port} (allowed clients: {', '.join(sorted(allowed))})")
    uvicorn.Server(make_config(create_app(manager, allowed, AgentApiServer(manager)), bind, port)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
