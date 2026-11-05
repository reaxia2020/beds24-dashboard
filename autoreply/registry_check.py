"""宿泊者名簿(Googleフォーム)の未提出者に催促メッセージを送る日次バッチ。

Railway上でcronジョブとして `python -m autoreply.registry_check` を1日1回実行する想定。
Beds24のWebhookとは独立しており、/webhooks/beds24 のメッセージ処理とは別経路。

突合方法: フォーム回答の「チェックイン日・チェックアウト日」とBeds24予約の到着日・出発日が
一致するかどうかで判定する(メールアドレスは予約経由によって一致しないことがあるため使わない)。
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from beds24_client import Beds24Client  # noqa: E402

from . import storage
from .google_sheets import get_sheet_client
from .registry_sheets import REGISTRY_SHEETS

# 到着日までの残り日数がこの値以下になったら、該当tierの催促を送る。
# 複数該当する場合は上(緊急度が高い方)を優先して1通だけ選ぶ。
REMINDER_TIERS = [
    ("reminder_urgent", 1),  # 当日・前日
    ("reminder_7day", 7),
]

MESSAGE_TEMPLATES = {
    "reminder_7day": (
        "Thank you for your reservation.\n"
        "We have not yet received your guest registration information, which is required "
        "under the Japanese Hotel Business Act. Please submit it using the form we sent "
        "earlier as soon as possible.\n"
        "If the information is not provided, we may unfortunately need to decline your stay.\n\n"
        "予約のお礼を申し上げます。日本の旅館業法に基づく宿泊者名簿の情報がまだ届いておりません。"
        "お送りしたフォームより、お早めにご入力をお願いいたします。"
        "ご入力いただけない場合、宿泊をお断りする場合がございます。"
    ),
    "reminder_urgent": (
        "Good morning. Please accept our apologies if this message crosses paths with your "
        "submission.\nAt this time, we have still not been able to confirm your guest "
        "registration information. Your check-in is today or tomorrow. If we are unable to "
        "confirm it by check-in, we regret that we may have to decline your stay in accordance "
        "with Japanese law. We kindly ask for your immediate cooperation in submitting it.\n\n"
        "おはようございます。入れ違いでしたら申し訳ございません。宿泊者名簿の情報がまだ確認できて"
        "おりません。チェックインが本日または明日に迫っております。チェックインまでにご確認でき"
        "ない場合、法令に基づき宿泊をお断りする場合がございますので、至急のご協力をお願いいたします。"
    ),
}


def _parse_sheet_date(raw):
    """シート上の日付表記(2025/11/29 など)をISO形式(YYYY-MM-DD)に正規化する。"""
    raw = (raw or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def load_submitted_date_pairs(gc, property_id):
    """このフォームで既に提出済みの(チェックイン日, チェックアウト日)の組の集合を返す。"""
    sheet_info = REGISTRY_SHEETS.get(property_id)
    if not sheet_info:
        return set()
    sh = gc.open_by_key(sheet_info["spreadsheet_id"])
    ws = sh.get_worksheet_by_id(sheet_info["gid"])
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return set()
    pairs = set()
    for row in rows[1:]:
        if len(row) < 4:
            continue
        checkin = _parse_sheet_date(row[2])
        checkout = _parse_sheet_date(row[3])
        if checkin and checkout:
            pairs.add((checkin, checkout))
    return pairs


def upcoming_bookings(client, property_id, days_ahead=10):
    today = dt.date.today()
    bookings = client.get_bookings(
        propertyId=property_id,
        arrivalFrom=today.isoformat(),
        arrivalTo=(today + dt.timedelta(days=days_ahead)).isoformat(),
    )
    return [b for b in bookings if b.get("status") in ("new", "confirmed")]


def main():
    storage.init_db()
    client = Beds24Client()
    gc = get_sheet_client()
    today = dt.date.today()
    sent_count = 0

    for property_id in REGISTRY_SHEETS:
        submitted = load_submitted_date_pairs(gc, property_id)
        for booking in upcoming_bookings(client, property_id):
            arrival = booking.get("arrival")
            departure = booking.get("departure")
            booking_id = booking.get("id")
            if (arrival, departure) in submitted:
                continue  # 提出済み

            days_until_checkin = (dt.date.fromisoformat(arrival) - today).days
            if storage.reminder_already_sent_today(booking_id, today.isoformat()):
                continue  # 今日はもう送った(cronが複数回走っても二重送信しない)

            # 複数のtierに該当する場合、一番緊急度の高いもの(閾値が小さい方)を1つだけ選ぶ
            tier = next(
                (t for t, threshold in REMINDER_TIERS if days_until_checkin <= threshold), None
            )
            if tier is None:
                continue  # まだどのtierの閾値にも入っていない

            client.send_message(booking_id, MESSAGE_TEMPLATES[tier])
            storage.mark_reminder_sent(booking_id, tier, today.isoformat())
            sent_count += 1
            print(f"sent {tier} reminder for booking {booking_id} (property {property_id})")

    print(f"done. reminders sent: {sent_count}")


if __name__ == "__main__":
    main()
