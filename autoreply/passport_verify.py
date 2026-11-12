"""宿泊者名簿にアップロードされたパスポート画像を、Google Driveから取得してAIで検証する。

これまでは「パスポート欄に人数分のファイルリンクがあるか」までしか見ておらず、
リンク先の中身(本当にパスポートか、氏名が名簿の記載と一致するか)は確認していなかった。

検証に失敗・判定できない場合は、安全側に倒して必ず「未検証」扱いにする
(誤って解錠情報を送ってしまうより、念のため保留する方が安全なため)。

前提条件(ユーザー側でのGoogle設定が必要):
1. Google Cloud ConsoleでこのプロジェクトのDrive APIを有効化すること
2. フォームの回答ファイルが保存されているDriveフォルダを、サービスアカウント
   (google_sheets.pyが使っている認証情報のclient_email)に閲覧権限で共有すること
これらが未設定の場合、verify_passport_files() は全件「未検証」として返す。
"""
import base64
import json
import os
import re

import requests
from anthropic import Anthropic

from . import storage
from .google_sheets import get_drive_credentials

DEFAULT_MODEL = "claude-haiku-5-5"

SYSTEM_PROMPT = """あなたは民泊運営者の代わりに、アップロードされた画像が本当にパスポートの
身分事項ページかどうかを確認するアシスタントです。

与えられた画像を見て、以下を判定してください:
- is_passport: 画像がパスポート(旅券)の身分事項ページ(写真・氏名・国籍等が記載されたページ)
  であれば true。無関係な画像・不鮮明すぎて判読不能・パスポート以外の書類であれば false。
- extracted_name: 画像からローマ字で読み取れる氏名(読み取れなければ null)。
  パスポート番号・生年月日・住所など、氏名以外の個人情報は抽出しないこと。

迷ったら必ず is_passport: false としてください。誤って「本人確認できた」と判定するより、
人間が確認する方が常に安全です。

出力は必ず以下のJSON形式のみ。説明文や前置きは一切不要です。
{"is_passport": true または false, "extracted_name": "ROMAJI NAME" または null, "reason": "判定理由を一言"}
"""

_DRIVE_FILE_ID_PATTERNS = [
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
]


def extract_file_id(url):
    url = (url or "").strip()
    for pattern in _DRIVE_FILE_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def _drive_token():
    creds = get_drive_credentials()
    from google.auth.transport.requests import Request

    creds.refresh(Request())
    return creds.token


def download_drive_file(file_id):
    """Drive APIからファイルのメタ情報とバイナリを取得する。
    アクセス権がない・APIが無効などの場合は例外を送出する(呼び出し側で安全側に処理すること)。"""
    token = _drive_token()
    headers = {"Authorization": f"Bearer {token}"}

    meta_resp = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"fields": "id,name,mimeType,size"},
        headers=headers,
        timeout=30,
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    content_resp = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"alt": "media"},
        headers=headers,
        timeout=60,
    )
    content_resp.raise_for_status()
    return meta.get("mimeType", "application/octet-stream"), content_resp.content


def _names_match(extracted_name, guest_names):
    if not extracted_name:
        return False
    norm = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())
    extracted_norm = norm(extracted_name)
    if not extracted_norm:
        return False
    for name in guest_names:
        name_norm = norm(name)
        if name_norm and (name_norm in extracted_norm or extracted_norm in name_norm):
            return True
    return False


def verify_passport_image(mime_type, file_bytes, guest_names):
    """1枚の画像を検証する。戻り値: (is_verified: bool, reason: str)"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return False, "ANTHROPIC_API_KEY未設定のため検証できず、安全側で未検証扱い"
    if not mime_type.startswith("image/"):
        return False, f"画像以外の形式({mime_type})のため安全側で未検証扱い"

    client = Anthropic(api_key=api_key)
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    b64 = base64.b64encode(file_bytes).decode("ascii")

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime_type, "data": b64},
                        },
                        {"type": "text", "text": f"宿泊者名簿に記載された氏名: {', '.join(guest_names) or '(不明)'}"},
                    ],
                }
            ],
        )
        text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
        if text_block is None:
            raise ValueError("応答にtextブロックが見つかりません")
        parsed = json.loads(text_block.text.strip())
        if not parsed.get("is_passport"):
            return False, f"パスポート画像として判定できず: {parsed.get('reason', '')}"
        if not _names_match(parsed.get("extracted_name"), guest_names):
            return False, (
                f"画像から読み取れた氏名(「{parsed.get('extracted_name')}」)が"
                f"名簿記載の氏名と一致しません"
            )
        return True, "パスポート画像を確認済み"
    except Exception as e:
        return False, f"検証処理でエラーが発生したため安全側で未検証扱い: {e}"


def verify_passport_files(passport_value, guest_names):
    """passport_value(カンマ区切りのDriveリンク)の全ファイルを検証する。
    戻り値: (all_verified: bool, reasons: list[str])"""
    urls = [u.strip() for u in (passport_value or "").split(",") if u.strip()]
    if not urls:
        return False, ["パスポート画像がありません"]

    reasons = []
    all_ok = True
    for url in urls:
        file_id = extract_file_id(url)
        if not file_id:
            all_ok = False
            reasons.append(f"Driveリンクからファイルを特定できません: {url}")
            continue

        cached = storage.get_cached_passport_check(file_id)
        if cached is not None:
            ok, reason = cached
            reasons.append(f"{reason}(キャッシュ済み)")
            if not ok:
                all_ok = False
            continue

        try:
            mime_type, file_bytes = download_drive_file(file_id)
        except Exception as e:
            all_ok = False
            reasons.append(f"Driveからファイルを取得できず安全側で未検証扱い({file_id}): {e}")
            continue
        ok, reason = verify_passport_image(mime_type, file_bytes, guest_names)
        storage.cache_passport_check(file_id, ok, reason)
        reasons.append(reason)
        if not ok:
            all_ok = False
    return all_ok, reasons
