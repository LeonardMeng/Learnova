"""The FastAPI app: middleware, error handling and shared dependencies. Routes live in
api/routes/ and only translate between HTTP and the services."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from notion_client.errors import APIResponseError, RequestTimeoutError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from learnova.config import Settings, load_settings
from learnova.errors import ConflictError, LearnovaError, describe
from learnova.models import Workspace
from learnova.services.workspaces import WorkspaceService
from learnova.storage.database import Repositories, open_repositories

LOCAL_HOSTS = ["127.0.0.1", "localhost"]


class Busy(ConflictError):
    pass


class WorkspaceLocks:
    """One sync or review per workspace at a time; a second one gets 409 instead of waiting."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @contextmanager
    def hold(self, name: str, what: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(name, threading.Lock())
        if not lock.acquire(blocking=False):
            raise Busy(f"A sync or review is already running for {name!r}; try again when it ends.")
        try:
            yield
        finally:
            lock.release()


def create_app(
    repos_factory: Callable[[], Repositories] | None = None,
    frontend_dist: Path | None | str = "auto",
) -> FastAPI:
    """`frontend_dist`: the built web app to serve at /; "auto" finds frontend/dist, None serves
    the API only."""
    from learnova.api.routes import issues, notes, points, quiz, status, workspaces

    app = FastAPI(
        title="Learnova",
        summary="Your study notes, reviewed and quizzed. Local use only: no authentication.",
    )
    app.state.repos_factory = repos_factory or (
        lambda: open_repositories(load_settings().database_url)
    )
    app.state.repos = None
    app.state.repos_lock = threading.Lock()
    app.state.locks = WorkspaceLocks()

    @app.middleware("http")
    async def require_json(request: Request, call_next):
        # A web page can POST a form or plain text to another site without asking the browser
        # first; it can't send JSON that way. So any POST that isn't JSON is refused.
        if request.method == "POST":
            ctype = request.headers.get("content-type", "")
            if not ctype.split(";")[0].strip().lower() == "application/json":
                return JSONResponse(
                    {"error": "Send the request body as JSON.", "code": "JsonRequired"},
                    status_code=415,
                )
        return await call_next(request)

    # Added last, so they run first: refuse other host names (DNS rebinding), then CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(load_settings().api.allowed_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    async def known_error(request: Request, exc: Exception):
        status_code, message = describe(exc) or (500, str(exc))
        return JSONResponse({"error": message, "code": type(exc).__name__}, status_code)

    # Learnova's own errors plus the Notion and network errors `describe` knows. Anything else is
    # a bug: FastAPI answers 500 and the traceback goes to the server log.
    for exc_type in (LearnovaError, APIResponseError, RequestTimeoutError, httpx.TransportError):
        app.add_exception_handler(exc_type, known_error)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    for module in (workspaces, notes, points, issues, status, quiz):
        app.include_router(module.router)
    if frontend_dist is not None:
        _serve_frontend(app, None if frontend_dist == "auto" else Path(frontend_dist))
    return app


def find_frontend(start: Path | None = None) -> Path | None:
    """frontend/dist in the Learnova project this code runs from, once `npm run build` ran."""
    here = (start or Path(__file__)).resolve()
    for directory in here.parents:
        dist = directory / "frontend" / "dist"
        if (dist / "index.html").is_file():
            return dist
    return None


def _serve_frontend(app: FastAPI, dist: Path | None = None) -> None:
    """Serve the built web app at / (with its assets), so `learnova serve` is all you need.
    Any path that isn't an API route or a file gets index.html: the app's router handles it."""
    dist = dist or find_frontend()
    if dist is None:
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"error": "No such API endpoint.", "code": "NotFound"}, 404)
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist.resolve()):
            return FileResponse(file)
        return FileResponse(dist / "index.html")


# --- dependencies --------------------------------------------------------------------------


def get_repos(request: Request) -> Repositories:
    app = request.app
    if app.state.repos is None:
        with app.state.repos_lock:
            if app.state.repos is None:
                app.state.repos = app.state.repos_factory()
    return app.state.repos


def get_settings() -> Settings:
    """Read on every request, so edits to config.toml apply without a restart."""
    return load_settings()


def get_locks(request: Request) -> WorkspaceLocks:
    return request.app.state.locks


def workspace_named(repos: Repositories, name: str) -> Workspace:
    return WorkspaceService(repos).current(name=name)
