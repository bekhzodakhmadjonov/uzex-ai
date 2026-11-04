"""Provider selection and failover.

UZEX_PROVIDER = "gemini" | "claude" | "gemini,claude" | "claude,gemini"   (order = primary, fallback)
Default: every provider whose API key is present, Gemini first if both.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("uzex.providers")


class FallbackPlanner:
    """Tries planners in order; on any exception moves to the next one."""

    def __init__(self, planners: list[Any]):
        if not planners:
            raise RuntimeError("no LLM provider configured: set GEMINI_API_KEY and/or ANTHROPIC_API_KEY in .env")
        self.planners = planners
        self.name = "+".join(getattr(p, "name", p.__class__.__name__) for p in planners)

    def _call(self, method: str, *a, **kw):
        last = None
        for p in self.planners:
            try:
                return getattr(p, method)(*a, **kw)
            except Exception as e:  # noqa: BLE001
                log.warning("provider %s failed in %s: %r", getattr(p, "name", p), method, e)
                last = e
        raise last  # type: ignore[misc]

    def plan(self, *a, **kw):
        return self._call("plan", *a, **kw)

    def compose(self, *a, **kw):
        return self._call("compose", *a, **kw)


def build_planner():
    if os.environ.get("UZEX_FAKE_LLM"):
        from .fake_llm import FakePlanner
        return FakePlanner()
    order = os.environ.get("UZEX_PROVIDER")
    have_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    have_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not order:
        order = ",".join([p for p, ok in (("gemini", have_gemini), ("claude", have_claude)) if ok])
    planners = []
    for name in [x.strip().lower() for x in order.split(",") if x.strip()]:
        if name == "gemini" and have_gemini:
            from .llm_gemini import GeminiPlanner
            planners.append(GeminiPlanner())
        elif name in ("claude", "anthropic") and have_claude:
            from .llm import ClaudePlanner
            planners.append(ClaudePlanner())
    return FallbackPlanner(planners)
