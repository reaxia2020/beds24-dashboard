import datetime as dt
import json
from pathlib import Path

EXPENSES_PATH = Path(__file__).parent / "expenses.json"

COUNTABLE_STATUSES = {"new", "confirmed"}


def load_expenses():
    if EXPENSES_PATH.exists():
        return json.loads(EXPENSES_PATH.read_text())
    return {}


def save_expenses(data):
    EXPENSES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def default_expense():
    return {"rent": 0, "cleaning_cost": 0, "fixed_cost": 0, "variable_rate": 0.0}


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def month_range(start_month, num_months):
    """start_month: date (day ignored, uses 1st). Returns list of month_key strings."""
    keys = []
    y, m = start_month.year, start_month.month
    for _ in range(num_months):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def booking_amount(booking):
    if booking.get("price") not in (None, ""):
        try:
            return float(booking["price"])
        except (TypeError, ValueError):
            pass
    total = 0.0
    found = False
    for item in booking.get("invoiceItems", []) or []:
        amount = item.get("amount")
        if amount is not None:
            try:
                total += float(amount)
                found = True
            except (TypeError, ValueError):
                continue
    return total if found else None


def prorate_booking(booking, month_keys_set):
    """1泊ごとに該当月へ売上・OTA手数料・泊数を按分して割り振る。

    戻り値: {month_key: {"nights": int, "revenue": float, "commission": float}}
    チェックイン日の属する月には予約件数もカウントする(呼び出し側で加算)。
    """
    if booking.get("status") not in COUNTABLE_STATUSES:
        return {}
    try:
        arrival = dt.date.fromisoformat(booking["arrival"])
        departure = dt.date.fromisoformat(booking["departure"])
    except (TypeError, ValueError, KeyError):
        return {}
    nights = (departure - arrival).days
    if nights <= 0:
        return {}

    price = booking_amount(booking) or 0.0
    commission = float(booking.get("commission") or 0)
    per_night_revenue = price / nights
    per_night_commission = commission / nights

    result = {}
    d = arrival
    while d < departure:
        mk = month_key(d)
        if mk in month_keys_set:
            cur = result.setdefault(mk, {"nights": 0, "revenue": 0.0, "commission": 0.0})
            cur["nights"] += 1
            cur["revenue"] += per_night_revenue
            cur["commission"] += per_night_commission
        d += dt.timedelta(days=1)
    return result


def compute_monthly_pnl(bookings_by_property, expenses, month_keys):
    """
    bookings_by_property: {propertyId: [booking, ...]}
    expenses: {str(propertyId): {"rent", "cleaning_cost", "fixed_cost", "variable_rate"}}
    month_keys: 対象月のリスト (例: ["2026-07", ..., "2027-03"])

    戻り値: {propertyId: {month_key: {metrics...}}, "_total": {month_key: {metrics...}}}
    """
    month_keys_set = set(month_keys)
    out = {}
    total = {mk: _empty_metrics() for mk in month_keys}

    for pid, bookings in bookings_by_property.items():
        exp = expenses.get(str(pid), default_expense())
        per_month = {mk: _empty_metrics() for mk in month_keys}

        for b in bookings:
            share = prorate_booking(b, month_keys_set)
            if not share:
                continue
            arrival_mk = (b.get("arrival") or "")[:7]
            for mk, part in share.items():
                m = per_month[mk]
                m["nights"] += part["nights"]
                m["revenue"] += part["revenue"]
                m["commission"] += part["commission"]
            if arrival_mk in month_keys_set:
                per_month[arrival_mk]["bookings"] += 1

        for mk in month_keys:
            m = per_month[mk]
            m["net_payment"] = m["revenue"] - m["commission"]
            m["rent"] = float(exp.get("rent") or 0)
            m["cleaning_cost"] = float(exp.get("cleaning_cost") or 0) * m["bookings"]
            m["fixed_cost"] = float(exp.get("fixed_cost") or 0)
            m["variable_cost"] = m["revenue"] * float(exp.get("variable_rate") or 0)
            m["gross_profit"] = (
                m["net_payment"] - m["rent"] - m["cleaning_cost"] - m["fixed_cost"] - m["variable_cost"]
            )

            t = total[mk]
            t["nights"] += m["nights"]
            t["revenue"] += m["revenue"]
            t["commission"] += m["commission"]
            t["bookings"] += m["bookings"]
            t["net_payment"] += m["net_payment"]
            t["rent"] += m["rent"]
            t["cleaning_cost"] += m["cleaning_cost"]
            t["fixed_cost"] += m["fixed_cost"]
            t["variable_cost"] += m["variable_cost"]
            t["gross_profit"] += m["gross_profit"]

        out[pid] = per_month

    out["_total"] = total
    return out


def _empty_metrics():
    return {
        "nights": 0,
        "bookings": 0,
        "revenue": 0.0,
        "commission": 0.0,
        "net_payment": 0.0,
        "rent": 0.0,
        "cleaning_cost": 0.0,
        "fixed_cost": 0.0,
        "variable_cost": 0.0,
        "gross_profit": 0.0,
    }
