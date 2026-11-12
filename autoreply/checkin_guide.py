"""チェックイン当日、宿泊者名簿の提出が「完全に」確認できたゲストにだけ、
解錠方法(キーボックス暗証番号など)を自動送信する日次バッチ(5分おき実行を想定)。

これまでBeds24側の「イン当日」オートアクションが、名簿の提出状況を一切見ずに
機械的に暗証番号を送ってしまっていた問題への対策。Beds24側のオートアクションからは
暗証番号の記載を削除し、代わりにこちらで名簿の完全性を確認してから送る。

使い方: python -m autoreply.checkin_guide
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from beds24_client import Beds24Client  # noqa: E402

from . import passport_verify, storage
from .faq_knowledge_loader import load_faq
from .google_sheets import get_sheet_client
from .registry_completeness import check_row_complete, passport_check_inputs
from .registry_sheets import REGISTRY_SHEETS

TIER = "checkin_guide"
COUNTABLE_STATUSES = {"new", "confirmed"}


def _parse_sheet_date(raw):
    raw = (raw or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _resolve_room_entry(property_entry, room_id, unit_id):
    """物件のFAQ知識から、該当する部屋(複数部屋がある場合はroom_id/unit_idで特定)の
    解錠案内文を取り出す。建物入口に別途オートロックがある物件(例: kiryo MODEビル)は
    building_entrance_codeもあわせて案内する。見つからなければNone。"""
    rooms = property_entry.get("rooms")
    if rooms:
        for info in rooms.values():
            if info.get("room_id") == room_id and info.get("unit_id") == unit_id:
                return info.get("keybox")
        return None

    keybox = property_entry.get("keybox")
    building_entrance = property_entry.get("building_entrance_code")
    if keybox and building_entrance:
        return f"{building_entrance}\n\n{keybox}"
    return keybox


def _matching_registry_row(gc, property_id, arrival, departure):
    sheet_info = REGISTRY_SHEETS.get(property_id)
    if not sheet_info:
        return None, None
    sh = gc.open_by_key(sheet_info["spreadsheet_id"])
    ws = sh.get_worksheet_by_id(sheet_info["gid"])
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return None, None
    header = rows[0]
    for row in rows[1:]:
        if len(row) < 4:
            continue
        if _parse_sheet_date(row[2]) == arrival and _parse_sheet_date(row[3]) == departure:
            return header, row
    return None, None


def main():
    storage.init_db()
    faq = load_faq()
    client = Beds24Client()
    gc = get_sheet_client()
    today = dt.date.today()
    sent_count = 0

    for property_id in REGISTRY_SHEETS:
        property_entry = faq.get("properties", {}).get(str(property_id), {})
        bookings = client.get_bookings(
            propertyId=property_id, arrivalFrom=today.isoformat(), arrivalTo=today.isoformat()
        )
        for booking in bookings:
            if booking.get("status") not in COUNTABLE_STATUSES:
                continue
            booking_id = booking.get("id")
            if storage.reminder_ever_sent(booking_id, TIER):
                continue  # 既に送信済み

            arrival = booking.get("arrival")
            departure = booking.get("departure")
            header, row = _matching_registry_row(gc, property_id, arrival, departure)
            if row is None:
                print(f"booking {booking_id}: 名簿の対応する回答がまだ見つかりません")
                continue

            guest_count = (booking.get("numAdult") or 0) + (booking.get("numChild") or 0)
            is_complete, reason = check_row_complete(header, row, min_guest_count=guest_count)
            if not is_complete:
                print(f"booking {booking_id}: 名簿が不完全のため見送り({reason})")
                continue

            non_japan_total, passport_value, guest_names = passport_check_inputs(header, row)
            if non_japan_total > 0:
                verified, reasons = passport_verify.verify_passport_files(
                    passport_value, guest_names
                )
                if not verified:
                    print(f"booking {booking_id}: パスポート画像の検証が未完了のため見送り({'; '.join(reasons)})")
                    continue

            entry_text = _resolve_room_entry(
                property_entry, booking.get("roomId"), booking.get("unitId")
            )
            if not entry_text:
                print(f"booking {booking_id}: 解錠方法の案内文が見つかりません(FAQ知識を確認してください)")
                continue

            message = (
                "宿泊者名簿のご提出ありがとうございました。内容を確認いたしましたので、"
                "解錠方法をご案内します。\n"
                "Thank you for completing the guest registration. "
                "Here is how to access the room:\n\n"
                f"{entry_text}"
            )
            client.send_message(booking_id, message)
            storage.mark_reminder_sent(booking_id, TIER, today.isoformat())
            sent_count += 1
            print(f"sent check-in guide for booking {booking_id} (property {property_id})")

    print(f"done. check-in guides sent: {sent_count}")
    return sent_count


if __name__ == "__main__":
    main()
