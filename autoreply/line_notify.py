"""LINE公式アカウント経由でオーナーに通知し、FAQ回答案を承認制で送信するためのヘルパー。

LINE Messaging APIのBroadcast API(友だち全員に送信)を使う。
このBotの友だちはオーナー本人のみを想定しているため、宛先を個別管理する必要がない。
承認・却下はボタン付きテンプレートメッセージ(postbackアクション)で行い、
ボタンが押された結果はWebhook(/webhooks/line)で受け取って処理する。
"""
import os

import requests

BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_API_URL = BROADCAST_URL  # 後方互換用エイリアス


def notify_irregular(property_id, booking_id, guest_message, reason):
    """イレギュラー案件が検知されたことをLINEに通知する(トークンが無い場合は何もしない)。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        return

    text = (
        "🟠 要確認メッセージがあります\n"
        f"物件ID: {property_id} / 予約ID: {booking_id}\n"
        f"理由: {reason}\n"
        f"ゲストメッセージ:\n{(guest_message or '')[:300]}\n\n"
        "詳細はレビュー画面で確認してください:\n"
        "https://beds24-dashboard.onrender.com/review"
    )

    try:
        requests.post(
            BROADCAST_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except requests.RequestException:
        pass  # 通知失敗はレビュー画面自体の動作をブロックしない


def _truncate(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def send_approval_request(beds24_message_id, property_id, booking_id, guest_message, draft_reply):
    """FAQ回答案をLINEに送り、ボタンで承認/却下できるようにする(トークンが無ければ何もしない)。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        return

    body_text = (
        f"物件:{property_id} / 予約:{booking_id}\n"
        f"Q: {_truncate(guest_message, 60)}\n"
        f"A案: {_truncate(draft_reply, 60)}"
    )
    message = {
        "type": "template",
        "altText": f"[{beds24_message_id}] 承認待ちのFAQ回答案があります",
        "template": {
            "type": "buttons",
            "title": f"要承認 #{beds24_message_id}"[:40],
            "text": _truncate(body_text, 160),
            "actions": [
                {
                    "type": "postback",
                    "label": "承認して送信",
                    "data": f"action=approve&id={beds24_message_id}",
                    "displayText": f"#{beds24_message_id} を承認します",
                },
                {
                    "type": "postback",
                    "label": "却下(送らない)",
                    "data": f"action=reject&id={beds24_message_id}",
                    "displayText": f"#{beds24_message_id} を却下します",
                },
            ],
        },
    }

    try:
        requests.post(
            BROADCAST_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messages": [message]},
            timeout=10,
        )
    except requests.RequestException:
        pass


def reply_text(reply_token, text):
    """postbackイベントへの応答として、承認/却下の結果をLINEに返信する(Reply APIは無料枠対象外)。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token or not reply_token:
        return
    try:
        requests.post(
            REPLY_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except requests.RequestException:
        pass
