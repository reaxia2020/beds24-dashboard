import json
import os

from anthropic import Anthropic

DEFAULT_MODEL = "claude-haiku-5-5"

SYSTEM_PROMPT = """あなたは民泊運営者の代わりにゲストからのメッセージに対応するアシスタントです。
与えられた物件のFAQ知識・予約情報・過去のやりとりを踏まえて、メッセージを分類し、
よくある質問(faq)であれば返信文案を作成してください。

分類ルール:
- "faq": チェックイン方法、Wi-Fi、アクセス、ハウスルール、アーリーチェックイン可否など、
  与えられたFAQ知識で自信を持って正確に答えられる質問。
- "irregular": クレーム、返金、特別対応の依頼、鍵が開かない等のトラブル、
  FAQ知識に記載がない・矛盾する・古い可能性がある場合、単なる雑談や判断に迷う場合。

最重要ルール: 少しでも迷ったら、または必要な情報がFAQ知識に無ければ、
必ず "irregular" として扱ってください。誤った情報をゲストに送るより、
人間が確認する方が常に安全です。

出力は必ず以下のJSON形式のみ。説明文や前置きは一切不要です。
{"category": "faq" または "irregular", "draft_reply": "faqの場合のみ返信文案(ゲストの言語に合わせる)。irregularの場合は null", "reason": "分類理由を一言"}
"""


def classify_and_draft(message, booking_context, faq_knowledge, history=None):
    """ゲストメッセージを分類し、FAQなら返信文案を作る。

    message: 今回受信したゲストメッセージの本文
    booking_context: {"property_name":..., "check_in":..., "check_out":..., "room":...} など
    faq_knowledge: autoreply/faq_knowledge.json を読み込んだdict(該当物件分を渡す)
    history: このゲストとの過去のやりとり(例: [{"source": "guest"/"host", "message": "..."}])

    戻り値: {"category": "faq"|"irregular", "draft_reply": str|None, "reason": str}
    迷う余地があるAPIエラー・パース失敗時は、安全側に倒して必ず "irregular" を返す。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {
            "category": "irregular",
            "draft_reply": None,
            "reason": "ANTHROPIC_API_KEY未設定のため分類できず、安全側でirregular扱い",
        }

    client = Anthropic(api_key=api_key)
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    history_text = ""
    if history:
        lines = [f"[{h.get('source')}] {h.get('message')}" for h in history]
        history_text = "\n".join(lines)

    user_prompt = f"""## 物件情報・FAQ知識
{json.dumps(faq_knowledge, ensure_ascii=False, indent=2)}

## 予約情報
{json.dumps(booking_context, ensure_ascii=False, indent=2)}

## このゲストとの過去のやりとり(古い順)
{history_text or "(過去のやりとりなし)"}

## 今回受信したメッセージ
{message}
"""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # 拡張思考(thinking)が有効なモデルだとcontent[0]がThinkingBlockになることがあるため、
        # 先頭決め打ちではなくtextブロックを探す。
        text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text_block is None:
            raise ValueError(f"応答にtextブロックが見つかりません: {[getattr(b, 'type', None) for b in resp.content]}")
        raw = text_block.text.strip()
        parsed = json.loads(raw)
        category = parsed.get("category")
        if category not in ("faq", "irregular"):
            raise ValueError(f"unexpected category: {category}")
        return {
            "category": category,
            "draft_reply": parsed.get("draft_reply") if category == "faq" else None,
            "reason": parsed.get("reason", ""),
        }
    except Exception as e:
        return {
            "category": "irregular",
            "draft_reply": None,
            "reason": f"分類処理でエラーが発生したため安全側でirregular扱い: {e}",
        }
