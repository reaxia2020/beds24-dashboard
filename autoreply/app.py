import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse

sys.path.append(str(Path(__file__).parent.parent))
from beds24_client import Beds24Client  # noqa: E402

from . import registry_check, registry_room_assign, storage
from .classifier import classify_and_draft
from .faq_knowledge_loader import load_faq
from .line_notify import notify_irregular, reply_text, send_approval_request

PROPERTY_IDS = [328093, 328094, 329645, 329647, 341983]

app = FastAPI()
client = Beds24Client()


@app.on_event("startup")
def on_startup():
    storage.init_db()


def resolve_room_label(property_faq_entry, room_id, unit_id):
    """物件に複数の部屋(例: Koenjiの101/103/201)がある場合、roomId+unitIdから
    該当する部屋のFAQ情報を特定する。該当なしならNoneを返す(無理に推測しない)。"""
    rooms = property_faq_entry.get("rooms")
    if not rooms:
        return None
    for label, info in rooms.items():
        if info.get("room_id") == room_id and info.get("unit_id") == unit_id:
            return label
    return None


def booking_context_for(booking_id, property_id):
    bookings = client.get_bookings(id=booking_id)
    booking = bookings[0] if bookings else {}
    return {
        "property_id": property_id,
        "room_id": booking.get("roomId"),
        "unit_id": booking.get("unitId"),
        "arrival": booking.get("arrival"),
        "departure": booking.get("departure"),
        "status": booking.get("status"),
    }


def history_for(booking_id):
    msgs = client.get_messages(bookingId=booking_id)
    msgs_sorted = sorted(msgs, key=lambda m: m.get("time") or "")
    return [{"source": m.get("source"), "message": m.get("message")} for m in msgs_sorted]


def process_new_messages():
    """新着(未処理)のゲストメッセージを分類し、FAQなら下書き(または自動送信)を記録する。"""
    faq = load_faq()
    auto_send_enabled = os.environ.get("AUTO_SEND_ENABLED", "false").strip().lower() == "true"
    processed = []

    # メッセージ毎にAPIを呼ぶとSheets APIのレート制限にすぐ達するため、1回だけ取得する
    processed_ids = storage.get_processed_ids()

    for property_id in PROPERTY_IDS:
        try:
            msgs = client.get_messages(propertyId=property_id)
        except Exception as e:
            processed.append({"property_id": property_id, "error": str(e)})
            continue

        guest_msgs = [m for m in msgs if m.get("source") == "guest"]
        for m in guest_msgs:
            beds24_message_id = m.get("id")
            if str(beds24_message_id) in processed_ids:
                continue
            processed_ids.add(str(beds24_message_id))

            booking_id = m.get("bookingId")
            booking_context = booking_context_for(booking_id, property_id)
            history = history_for(booking_id)
            # 今回のメッセージ自体は履歴から除いておく(classify_and_draftに別で渡すため)
            history = [h for h in history if h.get("message") != m.get("message")]

            property_entry = faq.get("properties", {}).get(str(property_id), {})
            room_label = resolve_room_label(
                property_entry, booking_context["room_id"], booking_context["unit_id"]
            )
            property_faq = {
                "common": faq.get("common", {}),
                "property": property_entry,
                "room": property_entry.get("rooms", {}).get(room_label) if room_label else None,
            }

            result = classify_and_draft(
                message=m.get("message"),
                booking_context=booking_context,
                faq_knowledge=property_faq,
                history=history,
            )

            auto_sent = False
            if result["category"] == "faq" and result.get("draft_reply") and auto_send_enabled:
                try:
                    client.send_message(booking_id, result["draft_reply"])
                    auto_sent = True
                except Exception as e:
                    result["reason"] += f" / 自動送信失敗: {e}"
            elif result["category"] == "irregular":
                notify_irregular(property_id, booking_id, m.get("message"), result.get("reason"))

            # LINEでの承認待ちをレビュー画面から追えるよう、通知より先に記録しておく
            storage.save_message(
                beds24_message_id=beds24_message_id,
                property_id=property_id,
                booking_id=booking_id,
                guest_message=m.get("message"),
                category=result["category"],
                draft_reply=result.get("draft_reply"),
                auto_sent=auto_sent,
            )

            if result["category"] == "faq" and result.get("draft_reply") and not auto_sent:
                send_approval_request(
                    beds24_message_id, property_id, booking_id, m.get("message"), result.get("draft_reply")
                )

            processed.append(
                {
                    "property_id": property_id,
                    "booking_id": booking_id,
                    "category": result["category"],
                    "auto_sent": auto_sent,
                }
            )

    return processed


@app.post("/webhooks/beds24")
def webhook_beds24(background_tasks: BackgroundTasks):
    """Beds24からの新着メッセージ通知を受けて、新着ゲストメッセージをまとめて処理する。
    通知の中身を厳密にパースするのではなく、受信をトリガーに全物件の新着を確認する設計。
    処理に数秒〜十数秒かかるため、Webhook自体は即座に200を返し、処理はバックグラウンドで行う。"""
    background_tasks.add_task(process_new_messages)
    return {"status": "accepted"}


def _verify_line_signature(body: bytes, signature: str) -> bool:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _handle_line_postback(data_str, reply_token):
    """承認/却下ボタンのpostbackを処理する。dataは 'action=approve&id=123' 形式。"""
    parsed = parse_qs(data_str)
    action = (parsed.get("action") or [None])[0]
    message_id = (parsed.get("id") or [None])[0]
    if not action or not message_id:
        return

    record = storage.get_message(message_id)
    if not record:
        reply_text(reply_token, f"#{message_id} が見つかりませんでした(既に処理済みかもしれません)。")
        return

    if action == "approve":
        try:
            client.send_message(record.get("booking_id"), record.get("draft_reply"))
            storage.update_message_status(message_id, "TRUE")
            reply_text(reply_token, f"#{message_id} を承認して送信しました。")
        except Exception as e:
            reply_text(reply_token, f"#{message_id} の送信に失敗しました: {e}")
    elif action == "reject":
        storage.update_message_status(message_id, "REJECTED")
        reply_text(reply_token, f"#{message_id} を却下しました(送信していません)。")


@app.post("/webhooks/line")
async def webhook_line(request: Request):
    """LINEからのボタン操作(postback)を受け取り、FAQ回答案の承認/却下を処理する。
    なりすまし防止のため、X-Line-Signatureを検証できたリクエストのみ処理する。"""
    body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    if not _verify_line_signature(body, signature):
        return {"status": "invalid signature"}

    payload = json.loads(body)
    for event in payload.get("events", []):
        if event.get("type") == "postback":
            _handle_line_postback(
                event.get("postback", {}).get("data", ""), event.get("replyToken")
            )
    return {"status": "ok"}


@app.post("/cron/daily")
def cron_daily():
    """GitHub Actionsから1日1回呼び出される日次バッチ。宿泊者名簿の部屋自動判定と、
    未提出者への催促送信を行う(催促は実際にゲストへ送信される)。"""
    room_assign_updates = registry_room_assign.main()
    reminders_sent = registry_check.main()
    return {"room_assign_updates": room_assign_updates, "reminders_sent": reminders_sent}


CLEANING_PROPERTY_IDS = [328093, 328094, 329645]  # kiryo(生野東)・Tama Riversideは清掃会社向けの対象外


def build_cleaning_schedule(days_ahead=60):
    """今日から指定日数分の、物件・部屋ごとのチェックアウト(清掃要)予定を作る。
    同じ部屋に同日中に次のゲストが入る場合は、清掃会社が優先度を判断できるよう
    「同日チェックイン」フラグを立てる。"""
    faq = load_faq()
    today = dt.date.today()
    horizon = today + dt.timedelta(days=days_ahead)
    rows = []

    for property_id in CLEANING_PROPERTY_IDS:
        entry = faq.get("properties", {}).get(str(property_id), {})
        property_name = entry.get("name", str(property_id))

        departures = client.get_bookings(
            propertyId=property_id,
            departureFrom=today.isoformat(),
            departureTo=horizon.isoformat(),
        )
        departures = [b for b in departures if b.get("status") not in ("black", "cancelled")]

        arrivals = client.get_bookings(
            propertyId=property_id,
            arrivalFrom=today.isoformat(),
            arrivalTo=horizon.isoformat(),
        )
        arrivals = [a for a in arrivals if a.get("status") not in ("black", "cancelled")]
        same_day_arrival_keys = {
            (a.get("roomId"), a.get("unitId"), a.get("arrival")) for a in arrivals
        }

        for b in departures:
            room_id, unit_id = b.get("roomId"), b.get("unitId")
            room_label = resolve_room_label(entry, room_id, unit_id) or entry.get(
                "room", ""
            ).split("。")[0]
            guests = (b.get("numAdult") or 0) + (b.get("numChild") or 0)
            rows.append(
                {
                    "date": b.get("departure"),
                    "property": property_name,
                    "room": room_label,
                    "guests": guests,
                    "same_day_checkin": (room_id, unit_id, b.get("departure"))
                    in same_day_arrival_keys,
                }
            )

    rows.sort(key=lambda r: (r["date"], r["property"], r["room"]))
    return rows


@app.get("/cleaning-schedule", response_class=HTMLResponse)
def cleaning_schedule():
    """清掃会社にそのまま共有するための、ログイン不要の清掃予定ページ。
    今日から2ヶ月分のチェックアウト予定を日付ごとにまとめて表示する。
    固定の更新間隔はなく、アクセスの都度Beds24から最新データを取得して描画する
    (新規予約・キャンセルも次のアクセス/自動更新で即反映される)。
    開きっぱなしでも最新を保てるよう、5分おきの自動リロードを仕込んでいる。"""
    rows = build_cleaning_schedule()

    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    sections = ""
    today_str = dt.date.today().isoformat()
    for date_str in sorted(by_date.keys()):
        items = by_date[date_str]
        is_today = date_str == today_str
        rows_html = ""
        for item in items:
            badge = (
                '<span class="badge">同日チェックインあり</span>'
                if item["same_day_checkin"]
                else ""
            )
            rows_html += f"""
            <li class="{"same-day" if item["same_day_checkin"] else ""}">
                <span class="property">{escape(item["property"])}</span>
                <span class="room">{escape(str(item["room"]))}</span>
                <span class="guests">{item["guests"]}名</span>
                {badge}
            </li>
            """
        sections += f"""
        <section class="{"today" if is_today else ""}">
            <h2>{escape(date_str)}{" (本日)" if is_today else ""}</h2>
            <ul>{rows_html}</ul>
        </section>
        """

    html = f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="refresh" content="300">
        <title>清掃スケジュール</title>
        <style>
            body {{ font-family: sans-serif; margin: 0; padding: 12px; background: #f7f7f7; }}
            h1 {{ font-size: 20px; }}
            p.note {{ color: #666; font-size: 13px; }}
            section {{ background: #fff; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px;
                       box-shadow: 0 1px 2px rgba(0,0,0,0.1); }}
            section.today {{ border: 2px solid #2b8cbe; }}
            h2 {{ font-size: 16px; margin: 4px 0 8px; }}
            ul {{ list-style: none; padding: 0; margin: 0; }}
            li {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
                  padding: 6px 8px; margin: 0 -8px; border-top: 1px solid #eee; font-size: 14px;
                  border-radius: 6px; }}
            li:first-child {{ border-top: none; }}
            li.same-day {{ background: #fdecea; }}
            .property {{ font-weight: bold; min-width: 160px; }}
            .room {{ color: #333; }}
            .guests {{ color: #888; }}
            .badge {{ background: #e53935; color: #fff; border-radius: 4px; padding: 2px 6px;
                      font-size: 12px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <h1>清掃スケジュール</h1>
        <p class="note">チェックアウト日(清掃対応日)の一覧です(今後2ヶ月分)。開くたびに最新の予約状況を取得し、5分おきに自動更新されます。</p>
        {sections if sections else "<p>今後2ヶ月のチェックアウト予定はありません。</p>"}
    </body>
    </html>
    """
    return html


@app.get("/review", response_class=HTMLResponse)
def review():
    rows = storage.list_messages()
    body_rows = ""
    for r in rows:
        category_badge = "🟢 FAQ" if r["category"] == "faq" else "🟠 要確認"
        sent_badge = "✅送信済" if r["auto_sent"] else "— 未送信"
        body_rows += f"""
        <tr>
            <td>{escape(r["created_at"])}</td>
            <td>{r["property_id"]}</td>
            <td>{r["booking_id"]}</td>
            <td style="max-width:300px; white-space:pre-wrap;">{escape(r["guest_message"] or "")}</td>
            <td>{category_badge}</td>
            <td style="max-width:300px; white-space:pre-wrap;">{escape(r["draft_reply"] or "")}</td>
            <td>{sent_badge}</td>
        </tr>
        """
    html = f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>自動返信レビュー</title>
        <style>
            body {{ font-family: sans-serif; margin: 12px; }}
            table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
            th, td {{ border: 1px solid #ccc; padding: 6px; text-align: left; vertical-align: top; }}
            th {{ background: #f0f0f0; }}
        </style>
    </head>
    <body>
        <h2>自動返信レビュー</h2>
        <p>件数: {len(rows)}</p>
        <table>
            <tr><th>日時</th><th>物件</th><th>予約ID</th><th>ゲストメッセージ</th><th>分類</th><th>返信案</th><th>送信状況</th></tr>
            {body_rows}
        </table>
    </body>
    </html>
    """
    return html
