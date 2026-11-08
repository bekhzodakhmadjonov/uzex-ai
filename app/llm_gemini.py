"""Gemini implementation of the Planner protocol (google-genai SDK).

Same prompts and JSON schema as the Claude planner (app/llm.py), so the agent is
provider-agnostic. Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment.
"""
from __future__ import annotations

import json
import os
from typing import Any

from google import genai
from google.genai import types

from .llm import ADJUDICATE_SCHEMA, LANG_NAME, PLAN_SCHEMA, _ADJUDICATE_RULES, _COMPOSE_RULES, _SYSTEM_RULES, _load_schema_doc, adjudicate_prompt

# preference order when UZEX_GEMINI_MODEL / UZEX_GEMINI_MODEL_COMPOSE are not set (verified 05.11.2026 against the account)
# Model choice is set in .env and justified in docs/architecture.html (benchmarks: tests/eval_hard.py).
DEFAULT_PLAN = "gemini-3.8-flash"
DEFAULT_COMPOSE = "gemini-3.1-flash-lite"
_RETRY = types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=5.0, exp_base=2.0, jitter=0.5, http_status_codes=[403, 429, 500, 502, 503, 504])
REQUEST_TIMEOUT_MS = 18_000  # two attempts leave room for the failover model inside the plan budget (app/agent.py)
PLAN_THINKING = os.environ.get("UZEX_GEMINI_PLAN_THINKING", "low")
COMPOSE_THINKING = os.environ.get("UZEX_GEMINI_COMPOSE_THINKING", "low")


class GeminiPlanner:
    name = "gemini"

    def __init__(self, model: str | None = None, compose_model: str | None = None):
        self.client = genai.Client(http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS, retry_options=_RETRY))  # key from env
        self.model = model or os.environ.get("UZEX_GEMINI_MODEL") or DEFAULT_PLAN
        self.compose_model = compose_model or os.environ.get("UZEX_GEMINI_MODEL_COMPOSE") or DEFAULT_COMPOSE
        self.schema_doc = _load_schema_doc()

    def _gen(self, system: str, contents: list[types.Content] | str, schema: dict | None, max_tokens: int, temperature: float = 0.0,
             model: str | None = None, thinking: str | None = None) -> str:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if schema else None,
            response_json_schema=schema if schema else None,
            thinking_config=types.ThinkingConfig(thinking_level=thinking) if thinking else None,
        )
        resp = self.client.models.generate_content(model=model or self.model, contents=contents, config=cfg)
        u = resp.usage_metadata
        self.last_usage = {"provider": "gemini", "model": model or self.model, "in": getattr(u, "prompt_token_count", None),
                           "out": getattr(u, "candidates_token_count", None), "thoughts": getattr(u, "thoughts_token_count", None),
                           "cached": getattr(u, "cached_content_token_count", None)}
        self.last_finish = str(getattr(resp.candidates[0], "finish_reason", "") if resp.candidates else "")
        text = resp.text or ""
        if not text and resp.candidates:
            parts = resp.candidates[0].content.parts or []
            text = "".join(getattr(p, "text", "") or "" for p in parts)
        return text

    def plan(self, question: str, lang: str, hints_text: str, history: list[dict[str, str]], error: str | None = None) -> dict[str, Any]:
        system = _SYSTEM_RULES.replace("{lang}", LANG_NAME.get(lang, lang)) + "\n\n# DATABASE DOCUMENTATION\n\n" + self.schema_doc
        contents: list[types.Content] = []
        for h in history[-3:]:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=h["question"])]))
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps({"sql": h.get("sql", ""), "answer": h.get("answer", "")[:300]}, ensure_ascii=False))]))
        user = f"QUESTION ({LANG_NAME.get(lang, lang)}): {question}\n\nRESOLVED ENTITY HINTS:\n{hints_text}"
        if error:
            user += f"\n\nYOUR PREVIOUS SQL FAILED OR WAS REJECTED. Error: {error}\nFix it and return corrected JSON."
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user)]))
        text = self._gen(system, contents, PLAN_SCHEMA, max_tokens=4000, thinking=PLAN_THINKING)
        data = json.loads(text)
        data.setdefault("chart", {"type": "none", "x": "", "y": ""})
        data["_usage"] = dict(self.last_usage)
        return data

    def compose(self, question: str, lang: str, sql: str, assumptions: list[str], rows_json: str, n_rows: int, extra: str = "") -> str:
        system = _COMPOSE_RULES.replace("{lang}", LANG_NAME.get(lang, lang))
        user = (f"QUESTION: {question}\n\nSQL RUN:\n{sql}\n\nASSUMPTIONS: {json.dumps(assumptions, ensure_ascii=False)}\n\n"
                f"RESULT ROWS ({n_rows} rows, showing up to 30):\n{rows_json}\n{extra}")
        text = self._gen(system, user, None, max_tokens=4000, model=self.compose_model, thinking=COMPOSE_THINKING).strip()
        if "MAX_TOKENS" in self.last_finish:  # a cut-off template would print a sentence that stops mid-word
            return ""
        return text

    def adjudicate(self, question: str, a: dict, b: dict) -> str:
        text = self._gen(_ADJUDICATE_RULES, adjudicate_prompt(question, a, b), ADJUDICATE_SCHEMA, max_tokens=1500, thinking=PLAN_THINKING)
        return json.loads(text)["choice"]
