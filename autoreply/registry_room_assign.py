"""新高円寺(101/103/201)のように1つの宿泊者名簿フォームを複数の部屋で共有している物件について、
各回答がどの部屋の予約に対応するかをBeds24の予約データ(roomId + unitId)から自動判定し、
回答シートに新しい列として書き込む日次バッチ。

判定ロジック: フォーム回答の「チェックイン日・チェックアウト日」と完全一致するBeds24予約を
物件内の全部屋から探す。該当が1件だけならその部屋番号を書き込み、0件または複数件の場合は
誤判定を避けるため推測せず「要確認」と書く。

使い方: python -m autoreply.registry_room_assign
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from beds24_client import Beds24Client  # noqa: E402

from .faq_knowledge_loader import load_faq
from .google_sheets import get_sheet_client
from .registry_sheets import REGISTRY_SHEETS

RESULT_COLUMN_HEADER = "判定された部屋番号(自動)"

# 複数の部屋で1つのフォームを共有している物件のみ対象。
MULTI_ROOM_PROPERTIES = [328093]


def _parse_sheet_date(raw):
    raw = (raw or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def build_room_lookup(rooms_faq):
    """{(room_id, unit_id): label} の辞書を作る。"""
    lookup = {}
    for label, info in (rooms_faq or {}).items():
        lookup[(info.get("room_id"), info.get("unit_id"))] = label
    return lookup


def find_or_create_result_column(ws):
    header = ws.row_values(1)
    if RESULT_COLUMN_HEADER in header:
        return header.index(RESULT_COLUMN_HEADER) + 1  # 1-indexed
    col = len(header) + 1
    ws.update_cell(1, col, RESULT_COLUMN_HEADER)
    return col


def main():
    faq = load_faq()
    client = Beds24Client()
    gc = get_sheet_client()
    total_updates = 0

    for property_id in MULTI_ROOM_PROPERTIES:
        sheet_info = REGISTRY_SHEETS.get(property_id)
        if not sheet_info:
            print(f"property {property_id}: シート未設定のためスキップ")
            continue

        rooms_faq = faq.get("properties", {}).get(str(property_id), {}).get("rooms", {})
        room_lookup = build_room_lookup(rooms_faq)

        sh = gc.open_by_key(sheet_info["spreadsheet_id"])
        ws = sh.get_worksheet_by_id(sheet_info["gid"])
        rows = ws.get_all_values()
        if len(rows) <= 1:
            print(f"property {property_id}: 回答なし")
            continue

        result_col = find_or_create_result_column(ws)
        header = ws.row_values(1)
        # ヘッダー追加直後は rows[0] が古いままなので、列数を揃えて読み直す
        if len(header) > len(rows[0]):
            rows = ws.get_all_values()

        updates = []
        for i, row in enumerate(rows[1:], start=2):  # 2行目から(1行目はヘッダー)
            existing = row[result_col - 1] if len(row) >= result_col else ""
            if existing:
                continue  # 判定済み
            if len(row) < 4:
                continue

            checkin = _parse_sheet_date(row[2])
            checkout = _parse_sheet_date(row[3])
            if not checkin or not checkout:
                continue

            bookings = client.get_bookings(
                propertyId=property_id, arrivalFrom=checkin, arrivalTo=checkin
            )
            matches = [b for b in bookings if b.get("departure") == checkout]

            if len(matches) == 1:
                b = matches[0]
                label = room_lookup.get((b.get("roomId"), b.get("unitId")))
                value = label if label else "要確認(部屋の対応表に無いroomId/unitId)"
            elif len(matches) == 0:
                value = "要確認(該当する予約が見つかりません)"
            else:
                value = "要確認(同じ日程の予約が複数あり判定不可)"

            updates.append({"range": f"R{i}C{result_col}", "values": [[value]]})

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
        print(f"property {property_id}: {len(updates)}件を判定・記入しました")
        total_updates += len(updates)

    return total_updates


if __name__ == "__main__":
    main()
