"""処理済みメッセージ・送信済み催促の記録。

Render無料プランのディスクは再起動のたびに消えるため、SQLiteではなく
Googleスプレッドシート(autoreply_state)に保存する。"""
import datetime as dt

from .google_sheets import get_sheet_client
from .state_sheet import STATE_SHEET

_gc = None
_sh = None
_messages_ws = None
_reminders_ws = None


def _client():
    global _gc
    if _gc is None:
        _gc = get_sheet_client()
    return _gc


def _spreadsheet():
    global _sh
    if _sh is None:
        _sh = _client().open_by_key(STATE_SHEET["spreadsheet_id"])
    return _sh


def _messages_sheet():
    global _messages_ws
    if _messages_ws is None:
        _messages_ws = _spreadsheet().get_worksheet_by_id(STATE_SHEET["messages_gid"])
    return _messages_ws


def _reminders_sheet():
    global _reminders_ws
    if _reminders_ws is None:
        _reminders_ws = _spreadsheet().get_worksheet_by_id(STATE_SHEET["reminders_gid"])
    return _reminders_ws


def init_db():
    """互換性のために残しているが、シート側は事前にヘッダー行を作成済みなので何もしない。"""
    pass


def get_processed_ids():
    """既に記録済みのbeds24_message_idの集合を1回のAPI呼び出しで取得する。
    メッセージ毎にAPIを呼ぶとSheets APIのレート制限にすぐ達するため、
    ループの外で1回だけ呼び、メモリ上の集合でチェックすること。"""
    col = _messages_sheet().col_values(1)
    return set(col[1:])


def is_processed(beds24_message_id):
    """単発確認用。大量のメッセージをループでチェックする場合はget_processed_idsを使うこと。"""
    return str(beds24_message_id) in get_processed_ids()


def save_message(
    beds24_message_id, property_id, booking_id, guest_message, category, draft_reply, auto_sent
):
    """呼び出し側で既に未処理と確認済みの前提で、確認なしに追記する
    (ループ内で毎回is_processedを呼ぶとレート制限に達するため)。"""
    _messages_sheet().append_row(
        [
            beds24_message_id,
            property_id,
            booking_id,
            guest_message or "",
            category,
            draft_reply or "",
            "TRUE" if auto_sent else "FALSE",
            dt.datetime.utcnow().isoformat(),
        ],
        value_input_option="USER_ENTERED",
    )


def save_messages_bulk(rows):
    """[(beds24_message_id, property_id, booking_id, guest_message, category,
    draft_reply, auto_sent, created_at), ...] をまとめて追記する(初期シード用)。"""
    if rows:
        _messages_sheet().append_rows(list(rows), value_input_option="USER_ENTERED")


def get_message(beds24_message_id):
    """LINEの承認/却下ボタンから参照する際に、1件分の予約ID・下書き文面を取得する。"""
    records = _messages_sheet().get_all_records()
    for r in records:
        if str(r.get("beds24_message_id")) == str(beds24_message_id):
            return r
    return None


def update_message_status(beds24_message_id, status):
    """LINEでの承認/却下結果をauto_sent列に反映する(status: 'TRUE' または 'REJECTED')。"""
    col = _messages_sheet().col_values(1)
    try:
        row_idx = col.index(str(beds24_message_id)) + 1  # 1-indexedかつヘッダー込みなので+1
    except ValueError:
        return False
    _messages_sheet().update_cell(row_idx, 7, status)  # 7列目 = auto_sent
    return True


def list_messages(limit=200):
    records = _messages_sheet().get_all_records()
    records = [r for r in records if r.get("category") != "baseline_seed"]
    records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    out = []
    for r in records[:limit]:
        out.append(
            {
                "created_at": r.get("created_at"),
                "property_id": r.get("property_id"),
                "booking_id": r.get("booking_id"),
                "guest_message": r.get("guest_message"),
                "category": r.get("category"),
                "draft_reply": r.get("draft_reply"),
                "auto_sent": r.get("auto_sent") == "TRUE",
            }
        )
    return out


def reminder_already_sent_today(booking_id, sent_date):
    records = _reminders_sheet().get_all_records()
    return any(
        str(r.get("booking_id")) == str(booking_id) and r.get("sent_date") == sent_date
        for r in records
    )


def reminder_ever_sent(booking_id, tier):
    """日付を問わず、指定tierがこの予約に一度でも送られているか(チェックイン案内のような
    「1回だけ送れば良い」ものの重複送信防止に使う)。"""
    records = _reminders_sheet().get_all_records()
    return any(
        str(r.get("booking_id")) == str(booking_id) and r.get("tier") == tier
        for r in records
    )


def mark_reminder_sent(booking_id, tier, sent_date):
    _reminders_sheet().append_row(
        [booking_id, tier, sent_date, dt.datetime.utcnow().isoformat()],
        value_input_option="USER_ENTERED",
    )


def list_reminders(limit=200):
    records = _reminders_sheet().get_all_records()
    records.sort(key=lambda r: r.get("sent_at", ""), reverse=True)
    return records[:limit]
