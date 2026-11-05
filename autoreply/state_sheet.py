"""処理済みメッセージ・送信済み催促を記録するGoogleスプレッドシートの場所を読み込む。

Renderの無料プランはディスクが一時的(再起動のたびに消える)ため、SQLiteではなく
Google Sheetsに状態を保存する。state_sheet.json はスプレッドシートIDを含むため
公開リポジトリには含めず.gitignore対象。デプロイ先ではSTATE_SHEET_JSON環境変数
(ファイルの中身をそのまま文字列として設定)を優先し、ローカルファイルがあれば
そちらで上書きする。"""
import json
import os
from pathlib import Path

STATE_SHEET_PATH = Path(__file__).parent / "state_sheet.json"


def _load():
    merged = {}
    env_json = os.environ.get("STATE_SHEET_JSON", "").strip()
    if env_json:
        merged.update(json.loads(env_json))
    if STATE_SHEET_PATH.exists():
        merged.update(json.loads(STATE_SHEET_PATH.read_text()))
    return merged


STATE_SHEET = _load()
