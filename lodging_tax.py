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
import re

REGIME_CHANGE_DATE = dt.date(2027, 4, 1)

# Booking.comの rateDescription は "2026-10-28 (...) JPY 8500 genius" のように
# 日付ごとの素泊まり単価(清掃費等を含まない)を含むため、これを優先的に使う。
_DATE_RATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\D*?JPY\s*([\d,]+(?:\.\d+)?)")
# Airbnbの rateDescription は合計の "Base Price" (清掃費を含まない素泊まり合計)を含む。
_BASE_PRICE_RE = re.compile(r"Base Price\s+([\d,]+(?:\.\d+)?)\s*JPY")

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


def parse_nightly_rates(rate_description, arrival, departure):
    """Booking.comのrateDescriptionから日付別の素泊まり単価を抽出する。

    戻り値: {date: 単価(円)}。抽出できなければ空dict。
    """
    if not rate_description:
        return {}
    rates = {}
    for m in _DATE_RATE_RE.finditer(rate_description):
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if arrival <= d < departure:
            try:
                rates[d] = float(m.group(2).replace(",", ""))
            except ValueError:
                continue
    return rates


def parse_room_only_price(rate_description):
    """Airbnbのレート詳細から、清掃費を含まない素泊まり合計(Base Price)を抽出する。"""
    if not rate_description:
        return None
    m = _BASE_PRICE_RE.search(rate_description)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def compute_booking_tax(booking):
    """1予約分の宿泊税を計算する。

    可能な場合はBeds24のレート明細(rateDescription)から日別の実単価・清掃費を除いた
    素泊まり料金を使い、取得できない場合は予約合計金額を泊数・人数で按分した平均単価を使う
    (この場合は清掃費等が含まれたままになるため、やや過大評価になる可能性がある)。

    戻り値: dict(total_tax, nights, guests, per_person_night_rate, label, rate_source) または
    None(対象外のステータス・不正な日付の場合)
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
    rate_description = booking.get("rateDescription") or ""

    nightly_rates = parse_nightly_rates(rate_description, arrival, departure)
    use_exact_nightly = len(nightly_rates) == nights

    room_only_price = parse_room_only_price(rate_description)
    if use_exact_nightly:
        base_price = sum(nightly_rates.values())
        rate_source = "日別実額(Booking.com)"
    elif room_only_price is not None:
        base_price = room_only_price
        rate_source = "素泊まり額(Airbnb、清掃費除く)"
    else:
        base_price = booking_amount(booking)
        rate_source = "滞在平均(清掃費等込みの概算)"

    per_person_avg_rate = base_price / nights / guests if base_price else 0

    total = 0
    taxable_nights = 0
    d = arrival
    while d < departure:
        if is_taxable_on(property_id, room_id, d):
            if use_exact_nightly and d in nightly_rates:
                per_person_rate_today = nightly_rates[d] / guests
            else:
                per_person_rate_today = per_person_avg_rate
            total += bracket_rate(per_person_rate_today, d) * guests
            taxable_nights += 1
        d += dt.timedelta(days=1)

    if taxable_nights == 0:
        return None  # 簡易宿所・民泊許可のため現行制度では課税対象外

    return {
        "total_tax": round(total),
        "nights": nights,
        "taxable_nights": taxable_nights,
        "guests": guests,
        "per_person_night_rate": round(per_person_avg_rate),
        "label": bracket_label(per_person_avg_rate, arrival),
        "rate_source": rate_source,
    }
