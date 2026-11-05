import os
import sys
from html import escape
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import HTMLResponse

sys.path.append(str(Path(__file__).parent.parent))
from beds24_client import Beds24Client  # noqa: E402

from . import registry_check, registry_room_assign, storage
from .classifier import classify_and_draft
from .faq_knowledge_loader import load_faq
from .line_notify import notify_irregular

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

            storage.save_message(
                beds24_message_id=beds24_message_id,
                property_id=property_id,
                booking_id=booking_id,
                guest_message=m.get("message"),
                category=result["category"],
                draft_reply=result.get("draft_reply"),
                auto_sent=auto_sent,
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


@app.post("/cron/daily")
def cron_daily():
    """GitHub Actionsから1日1回呼び出される日次バッチ。宿泊者名簿の部屋自動判定と、
    未提出者への催促送信を行う(催促は実際にゲストへ送信される)。"""
    room_assign_updates = registry_room_assign.main()
    reminders_sent = registry_check.main()
    return {"room_assign_updates": room_assign_updates, "reminders_sent": reminders_sent}


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
