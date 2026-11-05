"""Googleスプレッドシート(宿泊者名簿フォーム回答)への読み書き共通処理。"""
import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheet_client():
    key_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if key_json:
        creds = Credentials.from_service_account_info(json.loads(key_json), scopes=SCOPES)
    elif key_file:
        creds = Credentials.from_service_account_file(key_file, scopes=SCOPES)
    else:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON か GOOGLE_SERVICE_ACCOUNT_FILE を設定してください。"
        )
    return gspread.authorize(creds)
