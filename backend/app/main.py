from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import models  # noqa: F401  确保表注册到 metadata
from .config import settings
from .db import engine, init_db
from .routers import auth, capacity, courses, goals, groups, reviews, tasks
from .services import resources
from .services.llm import LLMUnavailable, chat_json
from .services import llm_cache

app = FastAPI(title="登阶")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(goals.router)
app.include_router(tasks.router)
app.include_router(capacity.router)
app.include_router(reviews.router)
app.include_router(courses.router)
app.include_router(groups.router)

resources.load_resources()
init_db()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@app.get("/api/health")
def health():
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT COUNT(*) FROM users"))
    except Exception:
        db_status = "error"

    if not settings.deepseek_api_key:
        model_status = "unconfigured"
    elif settings.fallback_llm_api_key:
        model_status = "fallback_configured"
    else:
        model_status = "configured"

    return {
        "ok": True,
        "app_env": settings.app_env,
        "db": db_status,
        "model": model_status,
    }


@app.get("/api/llm/ping")
async def llm_ping():
    try:
        result = await chat_json('返回 {"ok":true}', '{"ok": bool}')
        from_cache = result.pop("_from_cache", False)
        if from_cache:
            return {"ok": True, "model": settings.deepseek_model, "fallback": "cache"}
        return {"ok": True, "model": settings.deepseek_model}
    except LLMUnavailable:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "reason": "unavailable", "fallback": "none"},
        )


@app.get("/api/health/deep")
def health_deep():
    db_status = "ok"
    table_count = 0
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM sqlite_master WHERE type='table'"))
            table_count = result.scalar()
    except Exception:
        db_status = "error"

    if not settings.deepseek_api_key:
        llm_status = "unconfigured"
    elif settings.fallback_llm_api_key:
        llm_status = "fallback_configured"
    else:
        llm_status = "configured"

    return {
        "db": db_status,
        "tables": table_count,
        "llm": llm_status,
        "cache_entries": llm_cache.count(),
        "term_start": settings.term_start,
    }


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")
