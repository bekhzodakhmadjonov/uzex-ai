"""FastAPI app: /api/ask, /api/health, /api/examples and the static web UI."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from . import db  # noqa: E402
from .agent import answer_question  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
LOG_DIR = os.path.join(ROOT, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

app = FastAPI(title="UZEX Deals Assistant", version="0.1")
_pool = ThreadPoolExecutor(max_workers=8)
_sessions: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))
_planner = None


def planner():
    global _planner
    if _planner is None:
        from .providers import build_planner
        _planner = build_planner()
    return _planner


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
            "provider": getattr(p, "name", p.__class__.__name__), "models": models,
            "fake_llm": bool(os.environ.get("UZEX_FAKE_LLM"))}


@app.get("/api/examples")
def examples():
    with open(os.path.join(STATIC, "examples.json"), encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/ask")
async def ask(inp: AskIn):
    sid = inp.session_id or str(uuid.uuid4())
    history = list(_sessions[sid])
    loop = asyncio.get_running_loop()
    t0 = time.time()
    try:
        result: dict[str, Any] = await loop.run_in_executor(_pool, answer_question, inp.question, history, planner(), inp.language)
    except Exception as e:  # noqa: BLE001
        _log({"ts": t0, "session": sid, "question": inp.question, "error": repr(e)})
        raise HTTPException(500, "internal error") from e
    _sessions[sid].append({"question": inp.question, "sql": result.get("sql", ""), "answer": result.get("answer", "")})
    result["session_id"] = sid
    _log({"ts": t0, "session": sid, **{k: v for k, v in result.items() if k not in ("rows",)}, "n_rows": result.get("n_rows")})
    return JSONResponse(result)


def _log(rec: dict[str, Any]) -> None:
    try:
        with open(os.path.join(LOG_DIR, "qa.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
