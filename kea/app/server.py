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

from manager import LOG_SOURCES, KeaManager, load_options, log

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "static", "index.html")
MAX_BODY = 1024 * 1024


def require_json(request: Request):
    media_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def create_app(manager, allowed_ips):
    @asynccontextmanager
    async def lifespan(_app):
        manager.startup()
        threading.Thread(target=manager.watch, daemon=True).start()
        threading.Thread(target=manager.ntp_loop, daemon=True).start()
        yield
        log("termination signal received, stopping Kea and the Web UI...")
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
        return {"status": manager.status(), "settings": manager.settings}

    @app.get("/api/status")
    def status():
        return manager.status()

    @app.get("/api/leases")
    def leases():
        return manager.get_leases()

    @app.put("/api/settings", dependencies=[Depends(require_json)])
    def put_settings(data: dict = Body(...)):
        result = manager.apply(data)
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
    uvicorn.Server(make_config(create_app(manager, allowed), bind, port)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
