"""FastAPI app: /api/ask, /api/health, /api/examples and the static web UI.

Protects the LLM quota during judging: per-client rate limit, global rate limit, and a cache
that also guarantees the same question gets the same answer.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from . import db  # noqa: E402
from .agent import answer_question  # noqa: E402
from .dash import dashboard, ticker  # noqa: E402
from .talab.api import router as talab_router  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
LOG_DIR = os.path.join(ROOT, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

app = FastAPI(title="UZEX Deals Assistant", version="0.1")
_pool = ThreadPoolExecutor(max_workers=8)
_sessions: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))
_planner = None
_verifier = None
PER_CLIENT_PER_MIN = int(os.environ.get("UZEX_RATE_CLIENT", "20"))
GLOBAL_PER_MIN = int(os.environ.get("UZEX_RATE_GLOBAL", "60"))
_hits: dict[str, deque] = defaultdict(deque)
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_lock = threading.Lock()
BUSY = {"uz": "Hozir so‘rovlar juda ko‘p, bir daqiqadan keyin qayta urinib ko‘ring.", "ru": "Сейчас слишком много запросов, повторите через минуту."}


def planner():
    global _planner
    if _planner is None:
        from .providers import build_planner, build_verifier
        _planner = build_planner()
        global _verifier
        _verifier = build_verifier()
    return _planner


def _allowed(client: str) -> bool:
    now = time.time()
    with _lock:
        for key, limit in ((client, PER_CLIENT_PER_MIN), ("*", GLOBAL_PER_MIN)):
            q = _hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
        _hits[client].append(now)
        _hits["*"].append(now)
    return True


def _cache_key(question: str, language: str | None) -> str:
    return f"{language or ''}|" + re.sub(r"\s+", " ", question.strip().lower())


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    language: str | None = None  # force 'uz' | 'ru' | 'uz_cyr' | 'en'


@app.on_event("startup")
def _warm():
    db.connection()
    db.meta()


@app.get("/api/health")
def health():
    m = db.meta()
    p = planner()
    models = [getattr(x, "model", "") for x in getattr(p, "planners", [p])]
    return {"ok": True, "data_asof": m.get("data_asof"), "n_deals": m.get("n_deals"),
            "provider": getattr(p, "name", p.__class__.__name__), "models": models, "verifier": getattr(_verifier, "model", None),
            "fake_llm": bool(os.environ.get("UZEX_FAKE_LLM"))}


@app.get("/api/examples")
def examples():
    with open(os.path.join(STATIC, "examples.json"), encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/ask")
async def ask(inp: AskIn, request: Request):
    sid = inp.session_id or str(uuid.uuid4())
    history = list(_sessions[sid])
    key = _cache_key(inp.question, inp.language)
    if not history:
        with _lock:
            hit = _cache.get(key)
        if hit:
            _sessions[sid].append({"question": inp.question, "sql": hit.get("sql", ""), "answer": hit.get("answer", "")})
            return JSONResponse(hit | {"session_id": sid, "cached": True})
    client = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    if not _allowed(client):
        lang = "ru" if re.search(r"[А-Яа-яЁё]", inp.question) else "uz"
        return JSONResponse({"status": "rate_limited", "answer": BUSY[lang], "session_id": sid, "sql": "", "columns": [], "rows": [], "rows_fmt": [], "n_rows": 0, "basis": {}}, status_code=429)
    loop = asyncio.get_running_loop()
    t0 = time.time()
    p = planner()
    try:
        result: dict[str, Any] = await loop.run_in_executor(_pool, answer_question, inp.question, history, p, inp.language, _verifier)
    except Exception as e:  # noqa: BLE001
        _log({"ts": t0, "session": sid, "question": inp.question, "error": repr(e)})
        raise HTTPException(500, "internal error") from e
    _sessions[sid].append({"question": inp.question, "sql": result.get("sql", ""), "answer": result.get("answer", "")})
    _log({"ts": t0, "session": sid, **{k: v for k, v in result.items() if k not in ("rows", "rows_fmt")}})
    result.pop("trace", None)
    result["session_id"] = sid
    if not history and result["status"] in ("ok", "no_rows", "unanswerable", "template"):
        with _lock:
            _cache[key] = result
            while len(_cache) > 1000:
                _cache.popitem(last=False)
    return JSONResponse(result)


def _log(rec: dict[str, Any]) -> None:
    try:
        with open(os.path.join(LOG_DIR, "qa.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


@app.get("/api/dash")
async def dash(request: Request):
    q = request.query_params
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_pool, dashboard, q.get("from"), q.get("to"), q.get("family"), q.get("region"),
                                          q.get("cur"), q.get("annulled"), q.get("opts") == "1")
    except Exception as e:  # noqa: BLE001
        _log({"ts": time.time(), "dash_error": repr(e), "params": dict(q)})
        raise HTTPException(500, "dashboard error") from e


@app.get("/api/ticker")
async def ticker_api():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, ticker)


app.include_router(talab_router)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "talab.html"))


@app.get("/tahlil")
def tahlil():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
