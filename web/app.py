"""FastAPI application factory and server entry point."""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import OUTPUT_DIR, RUNTIME_DIR, STATIC_DIR, UPLOAD_DIR
from .database import dispose_database, initialize_database
from .routers import auth, frames, images, management, projects, system
from .security import COOKIE_NAME, current_actor, session_actor


@asynccontextmanager
async def lifespan(app: FastAPI):
    for directory in (RUNTIME_DIR, OUTPUT_DIR, UPLOAD_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        await run_in_threadpool(initialize_database)
        yield
    finally:
        dispose_database()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Photo Dream Image Studio",
        description="FastAPI / PostgreSQL storyboard image platform",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def authenticate(request: Request, call_next):
        path = request.url.path
        public = path.startswith("/static/") or path in {
            "/login",
            "/api/health",
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/options",
        }
        # Same-origin cookie authentication: refuse browser cross-origin writes.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if (origin and origin != expected) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
        actor = await run_in_threadpool(
            session_actor, request.cookies.get(COOKIE_NAME, "")
        )
        if not actor and not public:
            if path.startswith(("/api/", "/uploads/", "/outputs/")):
                return JSONResponse({"detail": "请先登录。"}, status_code=401)
            from urllib.parse import quote

            return RedirectResponse(
                "/login?next="
                + quote(
                    path + ("?" + request.url.query if request.url.query else ""),
                    safe="",
                ),
                status_code=303,
            )
        token = current_actor.set(actor)
        try:
            return await call_next(request)
        finally:
            current_actor.reset(token)

    origins = [
        item.strip()
        for item in os.getenv("CORS_ORIGINS", "").split(",")
        if item.strip()
    ]
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["*"],
        )
    application.include_router(system.router)
    application.include_router(projects.router)
    application.include_router(images.router)
    application.include_router(frames.router)
    application.include_router(auth.router)
    application.include_router(management.router)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/outputs/{filename}", include_in_schema=False)
    @application.get("/uploads/{filename}", include_in_schema=False)
    def protected_file(request: Request, filename: str):
        from pathlib import Path

        if Path(filename).name != filename or filename in {".", ".."}:
            raise HTTPException(404)
        management.authorize_asset(request.url.path)
        directory = (
            OUTPUT_DIR if request.url.path.startswith("/outputs/") else UPLOAD_DIR
        )
        file = directory / filename
        if not file.is_file() or file.resolve().parent != directory.resolve():
            raise HTTPException(404)
        return FileResponse(
            file,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            },
        )

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "console.html")

    @application.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    @application.get("/single", include_in_schema=False)
    def single():
        return FileResponse(STATIC_DIR / "console.html")

    @application.get("/storyboard", include_in_schema=False)
    def storyboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "console.html")

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
