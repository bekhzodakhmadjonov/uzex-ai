"""Pool-lot, deal costs and schedule, AI-arbiter draft (deterministic template), and the audit chain."""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from .common import FEE_CLEARING, FEE_EXCHANGE_BUYER, money, q, q1, rule, today
from .trust import deposit_for

# ---------------------------------------------------------------- pool-lot


@lru_cache(maxsize=64)
def typical_min_volume(fk: str, unit: str) -> float:
    """10th percentile of deal volume over the last 12 months: below this size a buyer is not on the exchange today."""
    t = today()
    r = q1("select quantile_cont(volume,0.10) p10 from deals where family_key=? and unit=? and deal_date>? and deal_date<=?",
           [fk, unit, t - timedelta(days=365), t])
    return float(r["p10"] or 0)


SIM_MEMBERS = [("Xaridor B", 0.22), ("Xaridor C", 0.31), ("Xaridor D", 0.17), ("Xaridor E", 0.26), ("Xaridor F", 0.12)]


def pool(item: dict, lot: dict) -> dict[str, Any]:
    qty = float(item.get("qty") or 0)
    target = typical_min_volume(item["family_key"], item["unit"])
    if lot.get("lot_size"):
        target = max(target, float(lot["lot_size"]))
    if qty >= target:
        return {"needed": False, "target": target, "text": f"Hajmingiz ({qty:g} {item['unit_short']}) odatiy minimal bitimdan katta: pul-lot shart emas."}
    members = [{"name": "Siz", "qty": qty, "simulated": False}]
    filled = qty
    for name, share in SIM_MEMBERS:
        if filled >= target:
            break
        rest = target - filled
        add = rest if rest <= target * share * 1.5 else target * share
        add = round(add, 1) if item["unit_short"] == "t" else float(round(add, -2))
        members.append({"name": name, "qty": add, "simulated": True})
        filled += add
    price = lot["expected_price"]
    for m in members:
        m["amount"] = m["qty"] * price
        m["deposit"] = m["amount"] * float(lot.get("deposit_pct") or 10) / 100
    return {"needed": True, "target": round(target, 1), "filled": round(filled, 1), "fill_pct": round(min(filled / target, 1) * 100),
            "members": members, "unit_price": price, "closes_in_days": 7,
            "text": f"Bugun bu tovar bo'yicha bitimlarning 90% i {target:g} {item['unit_short']} dan katta. Kichik talablar birlashib shu hajmga yetadi va hammasi bitta yagona narxda oladi.",
            "refund": "Lot 7 kun ichida to'lmasa, har bir ishtirokchining zakalati avtomatik qaytariladi.",
            "note": "B–F xaridorlar demo uchun simulyatsiya qilingan."}

# ---------------------------------------------------------------- deal


def deal(item: dict, lot: dict, buyer_trust: dict) -> dict[str, Any]:
    t = today()
    qty = float(item.get("qty") or 0)
    price = lot["expected_price"]
    amount = qty * price
    dep = deposit_for(float(lot.get("deposit_pct") or 10), buyer_trust)
    fee_x, fee_c = amount * FEE_EXCHANGE_BUYER, amount * FEE_CLEARING
    d0 = t + timedelta(days=1)
    pay_due = d0 + timedelta(days=int(lot.get("pay_term_days") or 0))
    deliv_due = pay_due + timedelta(days=int(lot.get("delivery_term_days") or 0))  # data: delivery due = payment date + term
    return {"qty": qty, "unit_price": price, "amount": amount, "fee_exchange": fee_x, "fee_clearing": fee_c,
            "deposit": dep, "deposit_amount": amount * dep["pct"] / 100, "total": amount + fee_x + fee_c,
            "lines": [
                {"k": "Tovar qiymati", "v": money(amount), "why": f"{qty:g} {item['unit_short']} × {money(price)}"},
                {"k": "Birja yig'imi (0,13%)", "v": money(fee_x), "why": "UZEX tarifi, 01.10.2026 dan"},
                {"k": "Kliring (0,05%)", "v": money(fee_c), "why": "Hisob-kliring palatasi"},
                {"k": f"Zakalat ({dep['pct']:g}%)", "v": money(amount * dep["pct"] / 100), "why": dep["why"] + " Bitim bajarilsa qaytadi."},
            ],
            "schedule": [
                {"date": d0.isoformat(), "event": "Bitim tuziladi (keyingi savdo sessiyasi)"},
                {"date": pay_due.isoformat(), "event": f"To'lov muddati ({lot.get('pay_term_days')} kun). O'tib ketsa: 170-band, zakalat sotuvchiga o'tadi"},
                {"date": deliv_due.isoformat(), "event": f"Yetkazish muddati ({lot.get('delivery_term_days')} kun). O'tib ketsa: 171-band, zakalat xaridorga o'tadi"},
            ],
            "rules": [rule("170"), rule("171")]}

# ---------------------------------------------------------------- arbiter


@lru_cache(maxsize=8)
def default_case(fk: str = "cement") -> int:
    r = q1("""select deal_no from deals where family_key=? and is_delivery_late and year=2026 and delivered_share=1
              order by delivery_delay_days desc, deal_date desc limit 1""", [fk])
    return int(r["deal_no"])


def arbiter(deal_no: int | None = None) -> dict[str, Any]:
    deal_no = deal_no or default_case()
    d = q1("""select deal_no, deal_date, product_uz, family_uz, volume, unit_uz, amount, deposit_pct, seller_name, buyer_name, seller_inn,
              buyer_inn, seller_broker_name, buyer_broker_name, pay_date, pay_due_date, delivery_due_date, delivery_date, delivery_delay_days,
              delivered_share, is_delivery_late, status_uz from deals where deal_no=?""", [deal_no])
    if not d:
        return {"error": "Bitim topilmadi"}
    dep = float(d["amount"]) * float(d["deposit_pct"] or 0) / 100
    late = bool(d["is_delivery_late"])
    paid_on_time = d["pay_date"] is not None and d["pay_due_date"] is not None and d["pay_date"] <= d["pay_due_date"]
    facts = [
        {"k": "Bitim", "v": f"№ {d['deal_no']}, {d['deal_date']:%d.%m.%Y}, {d['product_uz']}, {d['volume']:g} {d['unit_uz']}, {money(d['amount'])}"},
        {"k": "Tomonlar", "v": f"Sotuvchi: {d['seller_name']}; xaridor: {d['buyer_name']}"},
        {"k": "To'lov", "v": f"{d['pay_date']:%d.%m.%Y} da to'langan (muddat {d['pay_due_date']:%d.%m.%Y})" + (" — o'z vaqtida" if paid_on_time else " — kechikkan") if d["pay_date"] else "To'lanmagan"},
        {"k": "Yetkazish", "v": f"Muddat {d['delivery_due_date']:%d.%m.%Y}, haqiqatda {d['delivery_date']:%d.%m.%Y}: {d['delivery_delay_days']} kun kechikkan, {float(d['delivered_share']) * 100:.0f}% yetkazilgan" if d["delivery_date"] else "Yetkazilmagan"},
        {"k": "Zakalat", "v": f"{float(d['deposit_pct']):g}% = {money(dep)}"},
    ]
    if late and paid_on_time:
        finding = (f"Xaridor majburiyatini o'z vaqtida bajargan. Sotuvchi yetkazishni {d['delivery_delay_days']} kun kechiktirgan. "
                   f"171-bandga ko'ra, sotuvchi yetkazish muddatini buzganda Arbitraj komissiyasi bitimni bekor qilib, zakalatni ({money(dep)}) xaridorga o'tkazishi mumkin.")
        options = [
            {"id": "A", "text": f"171-band bo'yicha: zakalat ({money(dep)}) xaridor foydasiga undiriladi.", "note": "Qoidaga to'liq mos. Tovar to'liq yetkazilgan, shuning uchun bitimni bekor qilish shart yoki yo'qligini komissiya hal qiladi."},
            {"id": "B", "text": "Kelishuv: sotuvchi kechikish uchun yozma izoh va dalil beradi (transport hujjatlari). Uzrli sabab bo'lsa, jarima qisman qo'llanadi.", "note": "Qoidalarda qisman jarima yo'q: bu variant faqat tomonlar kelishsa."},
        ]
    else:
        finding = "Kechikish aniqlanmadi yoki xaridor to'lovni kechiktirgan: 170-band holatini alohida ko'rib chiqish kerak."
        options = [{"id": "A", "text": "Dalillarni to'liq yig'ish va komissiyaga yuborish.", "note": ""}]
    return {"deal_no": deal_no, "facts": facts, "finding": finding, "options": options, "rules": [rule("171"), rule("170")],
            "need_from_parties": ["Sotuvchining yozma izohi", "Transport yuk xati (TTN) va qabul dalolatnomasi", "Xaridorning da'vo summasi"],
            "human": "Bu faqat qaror loyihasi. Yakuniy qarorni Arbitraj komissiyasi a'zosi (inson) ko'rib chiqib, imzolaydi.",
            "made_by": "Deterministik shablon: faktlar bazadan, band matni qoidalardan. LLM ishlatilmagan."}

# ---------------------------------------------------------------- audit chain

_AUDIT: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))


def audit(session: str, event: str, payload: dict) -> dict:
    chain = _AUDIT[session]
    prev = chain[-1]["hash"] if chain else "0" * 16
    body = json.dumps({"event": event, "payload": payload, "prev": prev}, ensure_ascii=False, sort_keys=True, default=str)
    h = hashlib.sha256(body.encode()).hexdigest()[:16]
    rec = {"ts": time.strftime("%H:%M:%S"), "event": event, "summary": payload.get("summary", ""), "prev": prev, "hash": h}
    chain.append(rec)
    return rec


def audit_log(session: str) -> list[dict]:
    return list(_AUDIT.get(session, []))
