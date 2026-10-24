"""東京都宿泊税の概算計算。

出典: 東京都主税局「宿泊税の見直し」
https://www.tax.metro.tokyo.lg.jp/kazei/leisure/shuk/shuk_minaoshi
- 現行制度(〜2027/3/31宿泊分): 1人1泊1万円未満は非課税。1万円以上1万5千円未満は100円、
  1万5千円以上は200円(定額制)。
- 見直し後(2027/4/1宿泊分〜): 1人1泊1万3千円未満は非課税。それ以上は宿泊料金の3%(定率制)。
  民泊・簡易宿所も新たに課税対象。

注意: これはあくまで概算ツールです。実際の申告・納税にあたっては東京都主税局の最新情報を
必ずご確認ください(切り捨て処理など細部の取扱いは公式情報源で要確認)。
"""

import datetime as dt

REGIME_CHANGE_DATE = dt.date(2027, 4, 1)

OLD_EXEMPT_THRESHOLD = 10_000
OLD_TIER1_THRESHOLD = 15_000
OLD_TIER1_RATE = 100
OLD_TIER2_RATE = 200

NEW_EXEMPT_THRESHOLD = 13_000
NEW_RATE_PERCENT = 0.03

COUNTABLE_STATUSES = {"new", "confirmed"}

# 現行制度(〜2027/3/31宿泊分)の納税義務者は「旅館・ホテル」許可の施設のみで、
# 簡易宿所・民泊許可の施設は見直し後(2027/4/1宿泊分〜)まで課税対象外。
# ここには現行制度で課税対象(=旅館・ホテル許可)が確認できている (propertyId, roomId) のみを登録する。
# The Nest Koenji 101 (680830, 民泊新法届出)・kiryo MODEビル・Kiryo Tama Riverside は
# 簡易宿所/民泊扱いのため、現行制度では対象外として扱う。
CURRENTLY_TAXABLE_ROOMS = {
    (328093, 680831),  # The Nest Koenji 103&201
    (328093, 680832),  # The Nest Koenji 201
    (328094, 680833),  # あやめマンション201
}


def is_tokyo_property(property_data):
    city = (property_data.get("city") or "") + (property_data.get("state") or "")
    return "東京都" in city


def is_taxable_on(property_id, room_id, on_date):
    """その日の宿泊が宿泊税の課税対象施設かどうか(許可種別ベース)。"""
    if on_date >= REGIME_CHANGE_DATE:
        return True  # 見直し後は簡易宿所・民泊も課税対象に追加される
    return (property_id, room_id) in CURRENTLY_TAXABLE_ROOMS


def bracket_rate(per_person_night_rate, on_date):
    """その日の制度(現行/見直し後)に基づく、1人1泊あたりの宿泊税額。"""
    if per_person_night_rate is None:
        return 0
    if on_date >= REGIME_CHANGE_DATE:
        if per_person_night_rate < NEW_EXEMPT_THRESHOLD:
            return 0
        taxable_base = (per_person_night_rate // 100) * 100
        return round(taxable_base * NEW_RATE_PERCENT)
    else:
        if per_person_night_rate < OLD_EXEMPT_THRESHOLD:
            return 0
        if per_person_night_rate < OLD_TIER1_THRESHOLD:
            return OLD_TIER1_RATE
        return OLD_TIER2_RATE


def bracket_label(per_person_night_rate, on_date):
    rate = bracket_rate(per_person_night_rate, on_date)
    if rate == 0:
        return "非課税"
    if on_date >= REGIME_CHANGE_DATE:
        return f"3%定率({rate}円/人泊)"
    return f"{rate}円/人"


def booking_amount(booking):
    if booking.get("price") not in (None, ""):
        try:
            return float(booking["price"])
        except (TypeError, ValueError):
            pass
    return 0.0


def compute_booking_tax(booking):
    """1予約分の宿泊税を計算する。

    戻り値: dict(total_tax, nights, guests, per_person_night_rate, label) または None
    (対象外のステータス・不正な日付の場合)
    """
    if booking.get("status") not in COUNTABLE_STATUSES:
        return None
    try:
        arrival = dt.date.fromisoformat(booking["arrival"])
        departure = dt.date.fromisoformat(booking["departure"])
    except (TypeError, ValueError, KeyError):
        return None
    nights = (departure - arrival).days
    guests = int(booking.get("numAdult") or 0) + int(booking.get("numChild") or 0)
    if nights <= 0 or guests <= 0:
        return None

    property_id = booking.get("propertyId")
    room_id = booking.get("roomId")

    price = booking_amount(booking)
    per_person_night_rate = price / nights / guests if price else 0

    total = 0
    taxable_nights = 0
    d = arrival
    while d < departure:
        if is_taxable_on(property_id, room_id, d):
            total += bracket_rate(per_person_night_rate, d) * guests
            taxable_nights += 1
        d += dt.timedelta(days=1)

    if taxable_nights == 0:
        return None  # 簡易宿所・民泊許可のため現行制度では課税対象外

    return {
        "total_tax": round(total),
        "nights": nights,
        "taxable_nights": taxable_nights,
        "guests": guests,
        "per_person_night_rate": round(per_person_night_rate),
        "label": bracket_label(per_person_night_rate, arrival),
    }
