"""宿泊者名簿(Googleフォーム回答)の1行が、実際に提出完了と言えるかどうかを判定する。

単に「チェックイン日・チェックアウト日が一致する行がある」だけでは、
人数分の氏名が埋まっていない・外国籍なのにパスポート画像が無い、といった
記入漏れを見逃してしまう。ここでは列名(ヘッダー文字列)を手がかりに、
どの物件のシートでも共通して使えるチェックを行う。

判定に迷う・情報が読み取れない場合は、必ず「不完全」側に倒す
(宿泊者名簿なしで鍵情報を渡してしまうより、念のため催促を続ける方が安全なため)。
"""

NAME_HEADER_SUBSTR = "氏名/Name"
GUEST_COUNT_HEADER_SUBSTR = "宿泊人数/Number of Guests"
NATIONALITY_HEADER_SUBSTR = "国籍別人数/Number of People by Nationality"
JAPAN_NATIONALITY_SUBSTR = "[日本/Japan]"
PASSPORT_HEADER_SUBSTR = "パスポート画像をアップロード"


def _col_indices(header, substr):
    return [i for i, h in enumerate(header) if substr in h]


def _cell(row, idx):
    return row[idx].strip() if idx is not None and idx < len(row) else ""


def _to_int(text):
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def check_row_complete(header, row, min_guest_count=1):
    """宿泊者名簿の1行が完結しているかを判定する。

    header: シートのヘッダー行(row_values(1))
    row: 判定対象の1行(get_all_values()の1要素)
    min_guest_count: Beds24予約側の実際の宿泊人数(numAdult+numChild)。
                      フォーム記載の人数がこれを下回っていないかも確認する。

    戻り値: (is_complete: bool, reason: str)
    """
    name_cols = _col_indices(header, NAME_HEADER_SUBSTR)
    filled_names = sum(1 for idx in name_cols if _cell(row, idx))
    if filled_names == 0:
        return False, "氏名が1件も入力されていません"

    guest_count_cols = _col_indices(header, GUEST_COUNT_HEADER_SUBSTR)
    declared_count = _to_int(_cell(row, guest_count_cols[0])) if guest_count_cols else None
    if declared_count is None:
        return False, "宿泊人数が未入力です"

    required_count = max(declared_count, min_guest_count, 1)
    if filled_names < required_count:
        return False, f"人数分の氏名が不足しています(必要{required_count}名・入力{filled_names}名)"

    # 国籍別人数のうち日本以外が1人でもいれば、パスポート画像が必須。
    # ただしこの欄自体が空欄(未記入)の場合は「日本人しかいない」と決めつけず、
    # 安全側に倒して要確認(不完全)とする。
    nationality_cols = _col_indices(header, NATIONALITY_HEADER_SUBSTR)
    if not nationality_cols:
        return False, "国籍別人数の列が見つかりません"

    nationality_values = {}
    any_nationality_filled = False
    for idx in nationality_cols:
        header_label = header[idx]
        val = _to_int(_cell(row, idx))
        if val is not None:
            any_nationality_filled = True
        nationality_values[header_label] = val or 0

    if not any_nationality_filled:
        return False, "国籍別人数が未記入のため、外国籍の有無を確認できません"

    non_japan_total = sum(
        v for label, v in nationality_values.items() if JAPAN_NATIONALITY_SUBSTR not in label
    )
    if non_japan_total > 0:
        passport_cols = _col_indices(header, PASSPORT_HEADER_SUBSTR)
        passport_value = _cell(row, passport_cols[0]) if passport_cols else ""
        if not passport_value:
            return False, f"外国籍の宿泊者が{non_japan_total}名いますが、パスポート画像が未添付です"

    return True, None
