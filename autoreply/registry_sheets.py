"""物件ごとの宿泊者名簿フォーム回答シートの場所を読み込む。
spreadsheet_id は共有スプレッドシートのURL中の長いID部分、gid は対象シートタブのgid。
サービスアカウントに各シートを共有しておくこと(新高円寺は編集者、他は閲覧者でよい)。

registry_sheets.json はスプレッドシートIDを含むため公開リポジトリには含めず.gitignore対象。
デプロイ先ではREGISTRY_SHEETS_JSON環境変数(ファイルの中身をそのまま文字列として設定)を
優先し、ローカルファイルがあればそちらで上書きする(ローカル開発時の利便性のため)。"""
import json
import os
from pathlib import Path

REGISTRY_SHEETS_PATH = Path(__file__).parent / "registry_sheets.json"


def _load():
    merged = {}
    env_json = os.environ.get("REGISTRY_SHEETS_JSON", "").strip()
    if env_json:
        merged.update(json.loads(env_json))
    if REGISTRY_SHEETS_PATH.exists():
        merged.update(json.loads(REGISTRY_SHEETS_PATH.read_text()))
    # キーをintに正規化(JSON上は文字列キーになるため)
    return {int(pid): info for pid, info in merged.items()}


REGISTRY_SHEETS = _load()
