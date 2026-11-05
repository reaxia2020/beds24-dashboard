import json
import os
from pathlib import Path

FAQ_PATH = Path(__file__).parent / "faq_knowledge.json"


def load_faq():
    """FAQ知識ベースを読み込む。faq_knowledge.jsonは鍵の暗証番号等を含むため
    公開リポジトリには含めず.gitignore対象。デプロイ先ではFAQ_KNOWLEDGE_JSON環境変数
    (ファイルの中身をそのまま文字列として設定)を優先し、ローカルファイルがあれば
    そちらで上書きする(ローカル開発時の利便性のため)。"""
    merged = {"common": {}, "properties": {}}
    env_json = os.environ.get("FAQ_KNOWLEDGE_JSON", "").strip()
    if env_json:
        merged.update(json.loads(env_json))
    if FAQ_PATH.exists():
        merged.update(json.loads(FAQ_PATH.read_text()))
    return merged
