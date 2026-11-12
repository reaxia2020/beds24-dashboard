"""Googleスプレッドシート(宿泊者名簿フォーム回答)への読み書き共通処理。"""
import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _load_credentials(scopes):
    key_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if key_json:
        return Credentials.from_service_account_info(json.loads(key_json), scopes=scopes)
    if key_file:
        return Credentials.from_service_account_file(key_file, scopes=scopes)
    raise RuntimeError(
        "GOOGLE_SERVICE_ACCOUNT_JSON か GOOGLE_SERVICE_ACCOUNT_FILE を設定してください。"
    )


def get_sheet_client():
    return gspread.authorize(_load_credentials(SCOPES))


def get_drive_credentials():
    """宿泊者名簿のパスポート画像ファイル(Drive)を読み取るための認証情報。
    サービスアカウントのメールアドレスは creds.service_account_email で確認できる
    (Driveフォルダ側をこのアドレスに共有する必要がある)。"""
    return _load_credentials(DRIVE_SCOPES)
