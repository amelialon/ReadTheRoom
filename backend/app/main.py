"""Central API. The bedside server on port 8000 is a different process."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .db import open_pool
from .routes import devices, patients, requests, sensors
from .routes import settings as settings_routes
from .settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = app.state.settings or get_settings()
        app.state.settings = resolved
        app.state.pool = open_pool(resolved.database_url)
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(
        title="Read the Room",
        version="0.1.0",
        summary="Central request API for the nurse dashboard and bedside device.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def cors_and_no_store(request: Request, call_next):
        # Origins are read per request so Railway can set CORS_ORIGINS at startup.
        configured = app.state.settings
        allowed = configured.cors_origin_list if configured is not None else ["http://localhost:5173"]
        origin = request.headers.get("origin")
        if request.method == "OPTIONS" and origin in allowed:
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Max-Age": "600",
                    "Cache-Control": "no-store",
                    "Vary": "Origin",
                },
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        if origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/health", tags=["Health"])
    def health(request: Request):
        try:
            with request.app.state.pool.connection() as conn:
                conn.execute("select 1")
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "error"},
            )
        return {"status": "ok", "database": "ok"}

    app.include_router(requests.router)
    app.include_router(patients.router)
    app.include_router(devices.router)
    app.include_router(sensors.router)
    app.include_router(settings_routes.router)
    return app


app = create_app()
