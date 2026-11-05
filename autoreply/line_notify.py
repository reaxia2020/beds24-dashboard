"""イレギュラー判定されたメッセージをLINE公式アカウント経由でオーナーに通知する。

LINE Messaging APIのBroadcast API(友だち全員に送信)を使う。
このBotの友だちはオーナー本人のみを想定しているため、宛先を個別管理する必要がない。
"""
import os

import requests

LINE_API_URL = "https://api.line.me/v2/bot/message/broadcast"


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
            LINE_API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except requests.RequestException:
        pass  # 通知失敗はレビュー画面自体の動作をブロックしない
