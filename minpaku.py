import datetime as dt

ANNUAL_LIMIT_DAYS = 180

# 民泊新法(住宅宿泊事業法)の対象となる部屋タイプ
# {表示名: (propertyId, roomId)}
REGULATED_ROOMS = {
    "kiryo MODEビル": (329645, 683735),
    "The Nest Koenji 101": (328093, 680830),
}

COUNTABLE_STATUSES = {"new", "confirmed"}
MONTHLY_NIGHT_THRESHOLD = 30  # この泊数以上はマンスリー扱いとして民泊日数から除外

# 高需要期として民泊予算を優先確保しておきたい期間(例: 桜シーズン)
# {表示名: [(開始日, 終了日), ...]}
RESERVED_PREMIUM_WINDOWS = {
    "kiryo MODEビル": [(dt.date(2027, 3, 20), dt.date(2027, 3, 31))],
    "The Nest Koenji 101": [(dt.date(2027, 3, 20), dt.date(2027, 3, 31))],
}


def fiscal_year_bounds(today):
    """住宅宿泊事業法の算定期間: 毎年4/1〜翌年3/31"""
    if today.month >= 4:
        start = dt.date(today.year, 4, 1)
        end = dt.date(today.year + 1, 3, 31)
    else:
        start = dt.date(today.year - 1, 4, 1)
        end = dt.date(today.year, 3, 31)
    return start, end


def _overlap_nights(arrival, departure, lo_bound, hi_bound):
    lo = max(arrival, lo_bound)
    hi = min(departure, hi_bound + dt.timedelta(days=1))
    return max(0, (hi - lo).days)


def _split_gap_by_reserved(lo, hi, reserved_windows):
    """[lo, hi) を reserved_windows との重なりで分割し、(seg_lo, seg_hi, is_reserved) のリストを返す。"""
    points = {lo, hi}
    for rs, re in reserved_windows:
        rs_pt, re_pt = rs, re + dt.timedelta(days=1)
        if lo < re_pt and rs_pt < hi:
            points.add(max(lo, rs_pt))
            points.add(min(hi, re_pt))
    points = sorted(points)
    segments = []
    for i in range(len(points) - 1):
        seg_lo, seg_hi = points[i], points[i + 1]
        if seg_lo >= seg_hi:
            continue
        is_reserved = any(rs <= seg_lo <= re for rs, re in reserved_windows)
        segments.append((seg_lo, seg_hi, is_reserved))
    return segments


def compute_room_status(client, room_id, today=None, reserved_windows=None):
    """指定した roomId について、当年度の稼働日数・残日数・今後の空き期間を計算する。"""
    today = today or dt.date.today()
    fy_start, fy_end = fiscal_year_bounds(today)

    bookings = client.get_bookings(
        roomId=room_id,
        arrivalFrom=(fy_start - dt.timedelta(days=5)).isoformat(),
        arrivalTo=fy_end.isoformat(),
    )

    used_days = 0
    future_intervals = []
    for b in bookings:
        if b.get("status") not in COUNTABLE_STATUSES:
            continue
        arrival = dt.date.fromisoformat(b["arrival"])
        departure = dt.date.fromisoformat(b["departure"])
        if (departure - arrival).days >= MONTHLY_NIGHT_THRESHOLD:
            # 30泊以上はマンスリー扱いとして民泊日数にカウントしない
            continue
        used_days += _overlap_nights(arrival, departure, fy_start, fy_end)

        lo = max(arrival, today)
        hi = min(departure, fy_end + dt.timedelta(days=1))
        if lo < hi:
            future_intervals.append((lo, hi))

    remaining_days = max(0, ANNUAL_LIMIT_DAYS - used_days)

    # 今後の空き期間(予約済み期間をマージしてから、その隙間を抽出)
    future_intervals.sort()
    merged = []
    for lo, hi in future_intervals:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))

    gaps = []
    cursor = today
    for lo, hi in merged:
        if cursor < lo:
            gaps.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < fy_end + dt.timedelta(days=1):
        gaps.append((cursor, fy_end + dt.timedelta(days=1)))
    gaps = [(lo, hi) for lo, hi in gaps if (hi - lo).days > 0]

    # 高需要期(reserved_windows)を優先確保し、残りの予算を古い日付から「民泊」に割り当て、
    # 使い切った時点で「マンスリー推奨」に切り替える
    reserved_windows = reserved_windows or []
    split_segments = []  # (lo, hi, is_reserved)
    for lo, hi in gaps:
        split_segments.extend(_split_gap_by_reserved(lo, hi, reserved_windows))

    reserved_nights_total = sum((hi - lo).days for lo, hi, is_res in split_segments if is_res)
    budget_left = max(0, remaining_days - reserved_nights_total)
    over_budget = reserved_nights_total > remaining_days

    annotated_gaps = []
    for lo, hi, is_reserved in split_segments:
        nights = (hi - lo).days
        if is_reserved:
            annotated_gaps.append(
                {"from": lo, "to": hi - dt.timedelta(days=1), "nights": nights, "suggestion": "premium"}
            )
            continue
        if budget_left <= 0:
            annotated_gaps.append({"from": lo, "to": hi - dt.timedelta(days=1), "nights": nights, "suggestion": "monthly"})
        elif nights <= budget_left:
            annotated_gaps.append({"from": lo, "to": hi - dt.timedelta(days=1), "nights": nights, "suggestion": "minpaku"})
            budget_left -= nights
        else:
            split_date = lo + dt.timedelta(days=budget_left)
            annotated_gaps.append(
                {"from": lo, "to": split_date - dt.timedelta(days=1), "nights": budget_left, "suggestion": "minpaku"}
            )
            annotated_gaps.append(
                {
                    "from": split_date,
                    "to": hi - dt.timedelta(days=1),
                    "nights": nights - budget_left,
                    "suggestion": "monthly",
                }
            )
            budget_left = 0
    annotated_gaps.sort(key=lambda g: g["from"])

    return {
        "reserved_over_budget": over_budget,
        "fy_start": fy_start,
        "fy_end": fy_end,
        "used_days": used_days,
        "remaining_days": remaining_days,
        "gaps": annotated_gaps,
    }
