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

from leases import read_leases
from manager import LOG_SOURCES, KeaManager, load_options, log

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "static", "index.html")
MAX_BODY = 1024 * 1024


def require_json(request: Request):
    if "application/json" not in (request.headers.get("content-type") or ""):
        raise HTTPException(415, "Content-Type must be application/json")


def create_app(manager, allowed_ips):
    @asynccontextmanager
    async def lifespan(_app):
        manager.startup()
        threading.Thread(target=manager.watch, daemon=True).start()
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
        return {
            "dhcp4": read_leases(manager.lease_path("dhcp4")),
            "dhcp6": read_leases(manager.lease_path("dhcp6"))
            if "dhcp6" in manager.enabled else [],
        }

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

    return app


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
    uvicorn.run(create_app(manager, allowed), host=bind, port=port,
                log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
