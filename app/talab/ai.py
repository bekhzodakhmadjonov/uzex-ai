"""AI layer of UZEX TALAB: Claude (main) and Gemini (voice + backup) understand text, smeta photos and voice,
explain figures and draft arbiter decisions.

Provider chain (env UZEX_TALAB_AI_PROVIDER, default "claude,gemini" when both keys exist): each call goes to the
first provider that can take the input; on any error it falls to the next one, and finally to the deterministic
code path. Claude does not take audio, so voice goes to Gemini.

Rules that keep it safe and cheap:
- AI proposes, code verifies: every family must exist in the catalog, every number the AI returns
  is cross-checked against the deterministic parser, and on conflict the code's value wins.
- Explanations and arbiter drafts may not introduce any number that is not in the facts given;
  otherwise the deterministic template is used.
- Personal data (INN, phone numbers) is masked before anything leaves the server.
- Every answer is cached (data/talab_cache.json), so a demo replays without new API calls.
- If the API is off or fails, the caller gets the deterministic result and a visible reason.

Modes (env): UZEX_TALAB_AI=0 disables the API; UZEX_TALAB_FAKE_AI=1 returns AI-shaped results built
from the deterministic parser (for offline UI tests, no cost); UZEX_FAKE_LLM=1 also disables the API.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from typing import Any

from .common import ROOT, families, norm, regions, today
from .parse import parse

GEMINI_MODEL = os.environ.get("UZEX_TALAB_AI_MODEL", "gemini-3.1-flash-lite")
CLAUDE_MODEL = os.environ.get("UZEX_TALAB_CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_EFFORT = os.environ.get("UZEX_TALAB_CLAUDE_EFFORT", "low")
_claude = None
_tl = threading.local()


def providers() -> list[str]:
    keys = {"claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}
    order = [x.strip() for x in os.environ.get("UZEX_TALAB_AI_PROVIDER", "claude,gemini").split(",") if x.strip()]
    return [x for x in order if keys.get(x)]


def chain_name() -> str:
    names = {"claude": CLAUDE_MODEL, "gemini": GEMINI_MODEL}
    return " → ".join(names[p] for p in providers())


MODEL = GEMINI_MODEL  # kept for cache-key compatibility of older entries
CACHE_PATH = ROOT / "data" / "talab_cache.json"
_lock = threading.Lock()
_client = None


def mode() -> str:
    if os.environ.get("UZEX_TALAB_FAKE_AI") == "1":
        return "fake"
    if os.environ.get("UZEX_TALAB_AI") == "0" or os.environ.get("UZEX_FAKE_LLM") == "1":
        return "off"
    return "live" if providers() else "off"


def _cache() -> dict:
    if not hasattr(_cache, "d"):
        try:
            _cache.d = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache.d = {}
    return _cache.d


def _cache_put(k: str, v: Any) -> None:
    with _lock:
        d = _cache()
        d[k] = v
        try:
            CACHE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def _key(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode())
    return h.hexdigest()[:24]


def mask(text: str) -> str:
    text = re.sub(r"\+?998[\s\-]?\(?\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}", "[telefon]", text)
    return re.sub(r"(?<!\d)\d{9}(?!\d)", "[STIR]", text)


def _strict(schema: dict) -> dict:
    """Claude structured outputs need additionalProperties:false on every object, and a nullable enum
    written as anyOf (an enum may not mix a type list with null)."""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
        t = out.get("type")
        if isinstance(t, list) and "null" in t and "enum" in out:
            base = [x for x in t if x != "null"][0]
            vals = [v for v in out.pop("enum") if v is not None]
            out.pop("type")
            out["anyOf"] = [{"type": base, "enum": vals}, {"type": "null"}]
        return out
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


def _gen_claude(system: str, parts: list, schema: dict | None, max_tokens: int) -> str:
    global _claude
    import anthropic
    if _claude is None:
        _claude = anthropic.Anthropic(timeout=30.0, max_retries=1)
    content = []
    for p in parts:
        if isinstance(p, dict):
            mime = p["mime"]
            b64 = base64.standard_b64encode(p["data"]).decode()
            if mime.startswith("image/"):
                content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
            elif mime == "application/pdf":
                content.append({"type": "document", "source": {"type": "base64", "media_type": mime, "data": b64}})
            else:
                raise ValueError(f"claude does not take {mime}")
        else:
            content.append({"type": "text", "text": p})
    oc: dict = {"effort": CLAUDE_EFFORT}
    if schema:
        oc["format"] = {"type": "json_schema", "schema": _strict(schema)}
    resp = _claude.messages.create(model=CLAUDE_MODEL, max_tokens=max(max_tokens, 4000),
                                   system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                   messages=[{"role": "user", "content": content}], output_config=oc)
    if resp.stop_reason == "refusal":
        raise RuntimeError("claude refusal")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("claude max_tokens")
    return next(b.text for b in resp.content if b.type == "text")


def _gen(system: str, parts: list, schema: dict | None, max_tokens: int = 2000) -> str:
    """Try each provider in order; remember which model answered (thread-local) for the UI and the cache."""
    errors = []
    for prov in providers():
        if prov == "claude" and any(isinstance(p, dict) and p["mime"].startswith("audio/") for p in parts):
            continue
        try:
            t0 = time.time()
            text = _gen_claude(system, parts, schema, max_tokens) if prov == "claude" else _gen_gemini(system, parts, schema, max_tokens)
            _tl.model = CLAUDE_MODEL if prov == "claude" else GEMINI_MODEL
            _tl.ms = round((time.time() - t0) * 1000)
            _tl.fallback = errors[:]
            return text
        except Exception as e:  # noqa: BLE001 - any provider error falls to the next one
            errors.append(f"{prov}: {type(e).__name__}")
    raise RuntimeError("; ".join(errors) or "no provider")


def used_model() -> str:
    return getattr(_tl, "model", "")


def _gen_gemini(system: str, parts: list, schema: dict | None, max_tokens: int = 2000) -> str:
    global _client
    from google import genai
    from google.genai import types
    if _client is None:
        _client = genai.Client(http_options=types.HttpOptions(timeout=20_000))
    contents = [types.Content(role="user", parts=[
        types.Part.from_bytes(data=p["data"], mime_type=p["mime"]) if isinstance(p, dict) else types.Part.from_text(text=p) for p in parts])]
    cfg = types.GenerateContentConfig(system_instruction=system, temperature=0.0, max_output_tokens=max_tokens,
                                      response_mime_type="application/json" if schema else None,
                                      response_json_schema=schema if schema else None)
    resp = _client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=cfg)
    return resp.text or ""

# ------------------------------------------------------------------ understand


def _parse_schema() -> dict:
    fams = sorted(families().keys())
    regs = sorted(regions().keys())
    return {"type": "object", "properties": {
        "transcript": {"type": "string", "description": "Exact text heard or read (for audio/image), in the original language"},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "family_key": {"type": "string", "enum": fams},
            "qty": {"type": ["number", "null"]},
            "unit": {"type": ["string", "null"], "enum": ["t", "kg", "dona", "m3", "m2", "m", "l", None]},
            "price_max": {"type": ["number", "null"], "description": "max price per unit in so'm, only if stated"},
            "budget": {"type": ["number", "null"], "description": "total budget in so'm, only if stated"},
            "source_line": {"type": "string"}}, "required": ["family_key", "qty", "unit", "source_line"]}},
        "region_id": {"type": ["string", "null"], "enum": regs + [None]},
        "deadline": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD"},
        "not_on_exchange": {"type": "array", "items": {"type": "string"}, "description": "requested goods not in the catalog"},
        "intent": {"type": "string", "enum": ["need", "question", "greeting", "other"]},
        "reply": {"type": "string", "description": "for non-need messages: 1-3 short friendly sentences, no numbers"},
        "asked_family": {"type": ["string", "null"], "enum": fams + [None], "description": "good the user asks about, if any"},
        "suggestions": {"type": "array", "items": {"type": "string"}, "description": "up to 3 example needs the user could send next"},
        "note": {"type": "string"}}, "required": ["transcript", "items", "region_id", "deadline", "not_on_exchange", "intent", "reply"]}


def _system_parse() -> str:
    cat = "; ".join(f"{k}={v['name_uz']} ({v['unit']})" for k, v in families().items())
    reg = "; ".join(f"{k}={v['name_uz']}" for k, v in regions().items())
    return (f"You read a buyer's need for the UZEX commodity exchange (Uzbek Latin/Cyrillic or Russian; text, a photo of a "
            f"construction estimate, or speech). Today is {today().isoformat()}. Extract only what is stated. Never invent "
            f"numbers, prices or dates. Map goods to catalog keys: {cat}. Goods not in the catalog go to not_on_exchange. "
            f"Regions: {reg}. 'dekabrgacha'/'до декабря' means before the month starts (YYYY-12-01). "
            f"Put the exact words you read or heard in transcript. "
            f"If the message is NOT a purchase need (a greeting, a question about the exchange or this app, a question about a "
            f"price), set intent accordingly, leave items empty and write reply: 1-3 short friendly sentences in the user's "
            f"language (Uzbek Latin by default) that answer using ONLY the KNOWLEDGE below and then invite the user to say what "
            f"they need (good, quantity, deadline, region). Do not state any number in reply except numbers that appear in "
            f"KNOWLEDGE; the app shows live prices itself. If they ask about a good's price, set asked_family. Give up to 3 "
            f"short example needs in suggestions. Use the previous conversation, if given, to resolve follow-ups.\n\nKNOWLEDGE:\n{knowledge()}")


def knowledge() -> str:
    from .common import rules
    r = rules()
    lines = ["UZEX Talab: talab birjasi. Xaridor nima kerakligini yozadi, aytadi yoki smeta rasmini yuklaydi; AI talabnoma tuzadi, "
             "agent mos lotlarni qidiradi, Qo'riqchi (kod) narx shifti, sotuvchi ishonchi va muddatni tekshiradi; xaridor ERI bilan "
             "imzolaydi, pilotda broker tasdiqlaydi. Kichik xaridorlar birga olish (pul-lot) orqali birlashib zavod narxida oladi.",
             "Talabnoma: tovar, hajm, narx shifti, muddat va hudud yozilgan imzolanadigan buyurtma.",
             "Zakalat: savdoda qatnashish uchun vaqtincha bloklanadigan pul, odatda 10%; bitim bajarilsa qaytadi.",
             "Yig'imlar: birja yig'imi xaridordan 0,13%, kliring 0,05%.",
             "Adolatli narx: so'nggi 8 haftadagi real bitimlar narxining o'rtadagi yarmi (P25–P75).",
             "Ishonch balli 0–100: qatnashish davri, aylanma, o'z vaqtida va to'liq yetkazish; 60 dan past sotuvchi o'tkazilmaydi."]
    lines += [f"Qoida {k}-band: {r[k]['uz']}" for k in ("94", "165", "170", "171", "298", "299", "303") if k in r]
    return "\n".join(lines)


def _fake(text: str | None, kind: str) -> dict:
    from .api import SCENARIOS
    t = text if text else SCENARIOS[0]["text"]
    d = parse(t)
    return {"transcript": t, "items": [{"family_key": i["family_key"], "qty": i["qty"], "unit": i["unit_short"], "price_max": i["price_max"],
                                        "budget": i["budget"], "source_line": i["text"]} for i in d["items"]],
            "region_id": d["context"]["region_id"], "deadline": d["context"]["deadline"], "not_on_exchange": d["unmatched"],
            "note": "demo AI rejimi" + ("" if kind == "text" else f" ({kind}: namunaviy smeta)")}


UNIT_FACTOR = {("t", "килограмм"): 1000, ("kg", "тонна"): 0.001}


def _valid_date(s: str | None) -> str | None:
    """A deadline must be a real ISO date not before today (a smeta's own date is not a deadline)."""
    from datetime import date as _d
    try:
        d = _d.fromisoformat(str(s)[:10])
    except Exception:
        return None
    return d.isoformat() if d >= today() else None


def understand(text: str | None = None, file_b64: str | None = None, mime: str | None = None, median_price: dict | None = None,
               history: list[dict] | None = None) -> dict[str, Any]:
    """AI reads text/photo/voice -> talabnoma items; the deterministic parser verifies.

    Returns {"source": "ai"|"fake"|"code", "reason", "transcript", "items" (parse-shaped), "checks", "questions", ...}
    """
    kind = "text" if not file_b64 else ("image" if (mime or "").startswith("image/") else "audio")
    m = mode()
    raw = None
    reason = None
    t0 = time.time()
    if m == "fake":
        raw = _fake(text, kind)
    elif m == "live":
        data = base64.b64decode(file_b64) if file_b64 else None
        hist = [{"role": h.get("role", "user"), "text": mask(str(h.get("text", "")))[:500]} for h in (history or [])[-6:]]
        k = _key("understand", chain_name(), mask(text or ""), data or b"", mime or "", hist)
        raw = _cache().get(k)
        if raw is None:
            try:
                parts: list = []
                if data:
                    parts.append({"data": data, "mime": mime})
                    parts.append("Read this and extract the need.")
                if hist:
                    parts.append("Previous conversation:\n" + "\n".join(f"{h['role']}: {h['text']}" for h in hist))
                if text:
                    parts.append(mask(text))
                raw = json.loads(_gen(_system_parse(), parts, _parse_schema()))
                raw["_model"] = used_model()
                raw["_fallback"] = getattr(_tl, "fallback", [])
                if not raw["_fallback"]:  # a backup answer is not cached: next time the main provider is tried again
                    _cache_put(k, raw)
            except Exception as e:  # noqa: BLE001
                reason = f"AI javob bermadi ({type(e).__name__}); dastur tahlilchisi ishladi."
                raw = None
    else:
        reason = "AI o'chirilgan; dastur tahlilchisi ishladi."

    transcript = (raw or {}).get("transcript") or text or ""
    code = parse(transcript, median_price=median_price) if transcript else {"items": [], "unmatched": [], "questions": [], "context": {}}
    if kind == "text":
        ai_empty = raw is not None and not raw.get("items")
        no_qty = all(i["qty"] is None for i in code["items"])
        if (not code["items"] and (raw is None or ai_empty)) or (ai_empty and raw.get("intent") != "need") \
                or (raw is None and no_qty and QUESTION.search(transcript)):
            return _chat(transcript, raw, m, reason)
    if raw is None:
        code["source"] = "code"
        code["reason"] = reason if (text or not file_b64) else (reason or "") + " Rasm yoki ovozni o'qish uchun AI kerak."
        code["transcript"] = transcript
        code["checks"] = []
        code["kind"] = kind
        return code

    # merge: AI items verified against the code parser and the catalog
    fams = families()
    by_fam = {i["family_key"]: i for i in code["items"]}
    items, checks = [], []
    for n, a in enumerate(raw.get("items", [])):
        fk = a.get("family_key")
        if fk not in fams:
            checks.append({"status": "fail", "text": f"AI katalogda yo'q tovarni aytdi: {fk}; olib tashlandi"})
            continue
        f = fams[fk]
        qty = a.get("qty")
        if qty is not None and (a.get("unit"), f["unit"]) in UNIT_FACTOR:
            qty = qty * UNIT_FACTOR[(a.get("unit"), f["unit"])]
        c = by_fam.get(fk)
        typed = kind == "text"  # the user typed it: the code reads the user's own words, so the code wins
        if c and c["qty"] is not None and qty is not None and abs(c["qty"] - qty) > max(0.01, abs(c["qty"]) * 0.01):
            if typed:
                checks.append({"status": "warn", "text": f"{f['name_uz']}: AI hajmni {qty:g} dedi, matnda {c['qty']:g}; matndagi qiymat olindi"})
                qty = c["qty"]
            else:
                checks.append({"status": "warn", "text": f"{f['name_uz']}: dastur {c['qty']:g} deb o'qidi, AI {qty:g}; AI qiymati olindi, tekshiring"})
        elif c and c["qty"] is not None:
            checks.append({"status": "ok", "text": f"{f['name_uz']}: AI va dastur bir xil tushundi ({c['qty']:g} {c['unit_short']})"})
        elif qty is not None and kind == "text" and not re.search(re.escape(f"{qty:g}".split('.')[0]), transcript.replace(" ", "")):
            checks.append({"status": "warn", "text": f"{f['name_uz']}: AI aytgan {qty:g} matnda topilmadi; tasdiqlang"})
        price = a.get("price_max")
        if typed and c and c.get("price_max") is not None:
            if price is not None and abs(price - c["price_max"]) > 1:
                checks.append({"status": "warn", "text": f"{f['name_uz']}: narx shiftini AI {price:,.0f} dedi, matnda {c['price_max']:,.0f}; matndagi olindi".replace(",", " ")})
            price = c["price_max"]
        dl = _valid_date(raw.get("deadline")) if not typed else None
        dl = dl or (c or {}).get("deadline") or _valid_date(raw.get("deadline"))
        rid = (c or {}).get("region_id") or (raw.get("region_id") if raw.get("region_id") in regions() else None)
        base = c or parse(f["name_uz"])["items"][0]
        it = {**base, "id": f"T{len(items) + 1}", "family_key": fk, "family_uz": f["name_uz"], "unit": f["unit"], "qty": qty,
              "price_max": price, "price_source": "user" if price else None, "budget": a.get("budget"),
              "deadline": dl, "region_id": rid, "region_uz": regions()[rid]["name_uz"] if rid else None,
              "text": a.get("source_line") or base.get("text", ""), "missing": [], "by": "ai+code" if c else "ai"}
        for fld in ("qty", "deadline", "region_id"):
            if not it.get(fld):
                it["missing"].append({"region_id": "region"}.get(fld, fld))
        items.append(it)
    for c in code["items"]:
        if c["family_key"] not in {i["family_key"] for i in items}:
            c["by"] = "code"
            c["id"] = f"T{len(items) + 1}"
            items.append(c)
            checks.append({"status": "warn", "text": f"{c['family_uz']}: AI o'tkazib yubordi, dastur topdi; qo'shildi"})
    questions = []
    if any("qty" in i["missing"] for i in items):
        questions += [{"field": "qty", "q": f"{i['family_uz']}: qancha kerak?"} for i in items if "qty" in i["missing"]]
    if items and not items[0].get("deadline"):
        questions.append({"field": "deadline", "q": "Qachongacha kerak?"})
    if items and not items[0].get("region_id"):
        questions.append({"field": "region", "q": "Qaysi viloyatga yetkazish kerak?"})
    return {"source": "fake" if m == "fake" else "ai", "model": raw.get("_model", "") if m == "live" else "demo", "fallback": raw.get("_fallback", []), "kind": kind, "transcript": transcript,
            "items": items, "unmatched": (raw.get("not_on_exchange") or []), "questions": questions,
            "checks": checks, "note": raw.get("note", ""), "ms": round((time.time() - t0) * 1000),
            "context": {"today": today().isoformat()}}

# ------------------------------------------------------------------ conversation (non-need messages)

GLOSS = {
    r"zakalat|zalog|zadatok": "Zakalat — savdoda qatnashish uchun vaqtincha bloklanadigan pul, odatda bitim summasining 10%. Bitim bajarilsa, to'liq qaytadi.",
    r"talabnoma": "Talabnoma — nima kerakligingiz yozilgan buyurtma: tovar, hajm, narx shifti, muddat va hudud. Siz imzolaysiz, agent shu shartlar bilan sotuvchi qidiradi.",
    r"kliring|yig'im|komissiya": "Birja yig'imi xaridordan 0,13%, kliring 0,05%. Pul kliring palatasi orqali xavfsiz o'tadi.",
    r"ishonch|reyting": "Ishonch balli 0–100: sotuvchi oldin o'z vaqtida va to'liq yetkazganmi. 60 dan past sotuvchini Qo'riqchi o'tkazmaydi.",
    r"pul-lot|birga ol": "Birga olish: kichik xaridorlar birlashib, katta lotni zavod narxida oladi. Yig'ilmasa, pul avtomatik qaytadi.",
    r"qo'riqchi|qoriqchi": "Qo'riqchi — AI emas, aniq qoida: narx shiftdan oshgan, ishonchi past yoki muddatga ulgurmaydigan lotni o'tkazmaydi.",
    r"broker": "Broker — birjadagi rasmiy vakil. TALAB'da u talabnomangizni bir bosishda tasdiqlaydi.",
    r"qanday ishlaydi|bu nima|nima qila olasan|kimsan": "Men UZEX Talab agentiman: nima kerakligini aytasiz, men talabnoma tuzaman, mos lotlarni topaman va xavflisini to'xtataman. Imzoni siz qo'yasiz.",
}
QUESTION = re.compile(r"\?|qancha|necha pul|narxi|nima\b|qanday|nega|qachon|сколько|цена|почём|что такое|как ", re.I)
GREET = re.compile(r"^\s*(salom|assalom|assalomu|hello|hi|privet|привет|здравствуй|салом|ассалом|qalaysiz|yaxshimisiz)", re.I)


def _chat(text: str, raw: dict | None, m: str, reason: str | None) -> dict[str, Any]:
    """Greeting / question / unclear message: a short reply (AI, checked) + live figures from code + next-step suggestions."""
    from . import lots as lots_mod
    from . import market as market_mod
    from .parse import find_family
    fam = (raw or {}).get("asked_family") if (raw or {}).get("asked_family") in families() else find_family(norm(text))
    facts = None
    if fam:
        b = market_mod.band(fam)
        sig = market_mod.signal(fam, today() + __import__("datetime").timedelta(days=60))
        n_lots = len(lots_mod.candidate_lots(fam, None, limit=200))
        facts = {"family_key": fam, "family_uz": families()[fam]["name_uz"], "unit_short": b["unit_short"], "p25": b["p25"], "p50": b["p50"],
                 "p75": b["p75"], "n": b["n"], "why": b["why"], "lots": n_lots, "signal": sig["text"]}
    reply, src = None, "template"
    if raw and raw.get("reply"):
        if _grounded(raw["reply"], knowledge()):
            reply, src = raw["reply"].strip(), ("ai" if m == "live" else "fake")
        else:
            reason = "AI javobida tasdiqlanmagan raqam bor edi; shablon ko'rsatildi"
    if not reply:
        if GREET.search(text):
            reply = ("Assalomu alaykum! Men UZEX Talab agentiman. Nima kerakligini oddiy so'z bilan yozing: qaysi tovar, qancha, "
                     "qachongacha va qayerga. Masalan: “Dekabrgacha Samarqandga 8 tonna sement kerak”.")
        elif any(re.search(k, norm(text)) for k in GLOSS):
            reply = next(v for k, v in GLOSS.items() if re.search(k, norm(text))) + " Nima kerakligini yozsangiz, talabnoma tuzib beraman."
        elif fam:
            reply = f"{families()[fam]['name_uz']} bo'yicha bugungi real narxlar quyida. Sotib olmoqchi bo'lsangiz, hajm, muddat va hududni yozing."
        else:
            reply = "Tushunmadim. Nima kerakligini yozing: tovar, qancha, qachongacha va qayerga. Yoki smeta rasmini yuklang."
    from .api import SCENARIOS
    sugg = [x for x in (raw or {}).get("suggestions", []) if isinstance(x, str) and 3 < len(x) < 140][:3] or [s["text"] for s in SCENARIOS[1:4]]
    if fam and not (raw or {}).get("suggestions"):
        sugg = [f"Dekabrgacha Samarqandga 8 {facts['unit_short']} {families()[fam]['name_uz'].lower()} kerak"] + sugg[:2]
    return {"mode": "chat", "source": src, "model": (raw or {}).get("_model", "") if m == "live" else "demo", "kind": "text", "transcript": text,
            "reply": reply, "facts": facts, "suggestions": sugg, "reason": reason, "items": [], "questions": [], "checks": [],
            "unmatched": [], "intent": (raw or {}).get("intent") or ("greeting" if GREET.search(text) else "other")}

# ------------------------------------------------------------------ explain / arbiter


def _digits(s: str) -> set[str]:
    """Numbers as written: '1 014 444' is one number (space thousands), 'P25 946 654' is 946654 (P25 is a label)."""
    found = re.findall(r"(?<![A-Za-z\d])(\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)", s)
    return {d.replace(" ", "").replace(" ", "").replace(",", ".") for d in found}


def _grounded(out: str, facts: str) -> bool:
    fd = _digits(facts)
    fd_flat = {x.replace(".", "") for x in fd}
    for d in _digits(out):
        if d in fd or d.replace(".", "") in fd_flat or len(d.replace(".", "")) <= 1:
            continue
        return False
    return True


TONE = {"oddiy": "Write for a person who has never used an exchange: plain Uzbek (Latin), short sentences, no jargon, an everyday comparison if it helps.",
        "biznes": "Write for a business buyer: concise Uzbek (Latin), exchange terms allowed, focus on money and risk.",
        "pro": "Write for a broker: dense Uzbek (Latin), cite rule clauses and exact figures."}


def explain(topic: str, facts: str, fallback: str, mode_: str = "oddiy") -> dict[str, Any]:
    """2-3 sentence explanation of given facts. May not add numbers; otherwise the template is shown."""
    m = mode()
    if m != "live":
        return {"text": fallback, "source": "template", "reason": "demo rejimi" if m == "fake" else "AI o'chirilgan"}
    k = _key("explain", chain_name(), topic, facts, mode_)
    hit = _cache().get(k)
    if hit:
        return hit
    try:
        out = _gen(f"You explain one figure on the UZEX exchange app. {TONE.get(mode_, TONE['oddiy'])} 2-3 sentences. "
                   "Use ONLY the facts given. Do not add any number that is not in the facts. No greetings.",
                   [f"Topic: {topic}\nFacts:\n{facts}"], None, 400).strip()
        res = {"text": out, "source": "ai"} if out and _grounded(out, facts) else {"text": fallback, "source": "template", "reason": "AI javobida faktlarda yo'q raqam bor edi; shablon ko'rsatildi"}
    except Exception as e:  # noqa: BLE001
        res = {"text": fallback, "source": "template", "reason": f"AI javob bermadi ({type(e).__name__})"}
    if res["source"] == "ai":
        _cache_put(k, res)
    return res


def arbiter_text(case: dict) -> dict[str, Any]:
    facts = "\n".join(f"{f['k']}: {f['v']}" for f in case["facts"]) + "\nQoida 171: " + case["rules"][0]["uz"] + "\nQoida 170: " + case["rules"][1]["uz"]
    fallback = case["finding"]
    m = mode()
    if m != "live":
        return {"text": fallback, "source": "template", "reason": "demo rejimi" if m == "fake" else "AI o'chirilgan"}
    k = _key("arbiter", chain_name(), facts)
    hit = _cache().get(k)
    if hit:
        return hit
    try:
        out = _gen("You draft (not decide) a decision for the UZEX Arbitration Commission in Uzbek (Latin). Structure: "
                   "1) Aniqlangan holatlar, 2) Qo'llaniladigan qoida, 3) Tavsiya etilgan qaror loyihasi, 4) Qo'shimcha talab qilinadigan dalillar. "
                   "Use ONLY the facts and rule texts given; never invent numbers, dates or facts. End with: 'Yakuniy qarorni komissiya a'zosi imzolaydi.'",
                   [facts], None, 900).strip()
        res = {"text": out, "source": "ai"} if out and _grounded(out, facts) else {"text": fallback, "source": "template", "reason": "AI loyihasida faktlarda yo'q raqam bor edi; shablon ko'rsatildi"}
    except Exception as e:  # noqa: BLE001
        res = {"text": fallback, "source": "template", "reason": f"AI javob bermadi ({type(e).__name__})"}
    if res["source"] == "ai":
        _cache_put(k, res)
    return res
