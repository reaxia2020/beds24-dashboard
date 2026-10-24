import datetime as dt
import hmac
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from beds24_client import Beds24Client, Beds24Error
import finance
import lodging_tax
import minpaku
import official_reviews
from airroi_client import AirroiClient, AirroiError, summarize_comparables

st.set_page_config(page_title="Beds24 ダッシュボード", layout="wide")

load_dotenv(Path(__file__).parent / ".env")


def check_password():
    """共有リンクで誰でも見られないよう、簡易パスワード認証をかける。"""

    def password_entered():
        try:
            correct = st.secrets["APP_PASSWORD"]
        except (KeyError, FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            correct = os.environ.get("APP_PASSWORD", "")
        if hmac.compare_digest(st.session_state.get("password_input", ""), correct):
            st.session_state["password_correct"] = True
            del st.session_state["password_input"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct"):
        return True

    st.title("Beds24 ダッシュボード")
    st.text_input("パスワード", type="password", key="password_input", on_change=password_entered)
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("パスワードが違います")
    return False


if not check_password():
    st.stop()

OTA_METRICS_PATH = Path(__file__).parent / "ota_metrics.json"

# AirROIの比較検索に使う物件スペック(寝室数・定員・バスルーム数)
PROPERTY_SPECS = {
    328093: {"bedrooms": 1, "guests": 2, "baths": 1},   # The Nest Koenji
    328094: {"bedrooms": 1, "guests": 4, "baths": 1},   # あやめマンション201
    329645: {"bedrooms": 2, "guests": 9, "baths": 1},   # kiryo MODEビル
    329647: {"bedrooms": 3, "guests": 11, "baths": 1},  # kiryo
    341983: {"bedrooms": 2, "guests": 8, "baths": 1},   # Kiryo Tama Riverside
}


@st.cache_resource
def get_client():
    return Beds24Client()


@st.cache_resource
def get_airroi_client():
    return AirroiClient()


def booking_amount(booking):
    if booking.get("price") not in (None, ""):
        try:
            return float(booking["price"])
        except (TypeError, ValueError):
            pass
    total = 0.0
    found = False
    for item in booking.get("invoiceItems", []) or []:
        amount = item.get("amount")
        if amount is not None:
            try:
                total += float(amount)
                found = True
            except (TypeError, ValueError):
                continue
    return total if found else None


COUNTRY_NAMES_JA = {
    "JP": "日本", "US": "アメリカ", "GB": "イギリス", "CA": "カナダ", "AU": "オーストラリア",
    "NZ": "ニュージーランド", "FR": "フランス", "DE": "ドイツ", "IT": "イタリア", "ES": "スペイン",
    "PT": "ポルトガル", "NL": "オランダ", "BE": "ベルギー", "CH": "スイス", "AT": "オーストリア",
    "SE": "スウェーデン", "NO": "ノルウェー", "DK": "デンマーク", "FI": "フィンランド", "IE": "アイルランド",
    "PL": "ポーランド", "CZ": "チェコ", "GR": "ギリシャ", "RU": "ロシア", "UA": "ウクライナ",
    "CN": "中国", "TW": "台湾", "HK": "香港", "KR": "韓国", "KP": "北朝鮮",
    "TH": "タイ", "VN": "ベトナム", "PH": "フィリピン", "MY": "マレーシア", "SG": "シンガポール",
    "ID": "インドネシア", "IN": "インド", "PK": "パキスタン", "BD": "バングラデシュ", "LK": "スリランカ",
    "AE": "アラブ首長国連邦", "SA": "サウジアラビア", "IL": "イスラエル", "TR": "トルコ", "QA": "カタール",
    "EG": "エジプト", "ZA": "南アフリカ", "NG": "ナイジェリア", "KE": "ケニア", "MA": "モロッコ",
    "BR": "ブラジル", "MX": "メキシコ", "AR": "アルゼンチン", "CL": "チリ", "CO": "コロンビア",
    "PE": "ペルー", "CU": "キューバ",
    "MN": "モンゴル", "KZ": "カザフスタン", "AM": "アルメニア", "GE": "ジョージア",
    "RO": "ルーマニア", "HU": "ハンガリー", "BG": "ブルガリア", "HR": "クロアチア", "RS": "セルビア",
    "SK": "スロバキア", "SI": "スロベニア", "IS": "アイスランド", "LU": "ルクセンブルク",
    "MO": "マカオ", "BN": "ブルネイ", "KH": "カンボジア", "LA": "ラオス", "MM": "ミャンマー",
    "NP": "ネパール",
}


def country_name_ja(code):
    if not code:
        return "不明"
    return COUNTRY_NAMES_JA.get(code.upper(), code.upper())


def property_name_map(properties):
    return {p.get("id"): p.get("name", "不明") for p in properties}


# 予約停止中などの理由でカレンダーに表示したくない部屋 (propertyId, roomId)
CALENDAR_EXCLUDED_ROOMS = {
    (328093, 680832),  # The Nest Koenji 201(予約停止中)
}


def unit_label_map(properties):
    """(propertyId, roomId, unitId) -> 表示名 ('物件名 部屋名')"""
    labels = {}
    for p in properties:
        pname = p.get("name", "")
        for rt in p.get("roomTypes", []) or []:
            for u in rt.get("units", []) or []:
                uname = u.get("name", "")
                label = uname if uname.startswith(pname) else f"{pname} {uname}".strip()
                labels[(p.get("id"), rt.get("id"), u.get("id"))] = label
    return labels


def load_ota_metrics():
    if OTA_METRICS_PATH.exists():
        return json.loads(OTA_METRICS_PATH.read_text())
    return {}


def save_ota_metrics(data):
    OTA_METRICS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))


st.title("Beds24 ダッシュボード")

try:
    client = get_client()
except Beds24Error as e:
    st.error(str(e))
    st.stop()

with st.sidebar:
    st.header("期間フィルター(予約・売上)")
    today = dt.date.today()
    date_from = st.date_input("開始日", today - dt.timedelta(days=30))
    date_to = st.date_input("終了日", today + dt.timedelta(days=60))
    st.caption("チェックイン日(arrival)を基準にフィルターします")
    if st.button("データを再取得", use_container_width=True):
        st.cache_data.clear()


@st.cache_data(ttl=300)
def load_properties():
    return client.get_properties(includeAllRooms="true")


@st.cache_data(ttl=300)
def load_bookings(arrival_from, arrival_to):
    return client.get_bookings(arrivalFrom=arrival_from, arrivalTo=arrival_to)


try:
    all_properties = load_properties()
except Beds24Error as e:
    st.error(f"物件情報の取得でエラーが発生しました: {e}")
    st.stop()

with st.sidebar:
    st.header("物件フィルター")
    all_property_names = [p.get("name") for p in all_properties]
    selected_property_names = st.multiselect(
        "表示する物件(未選択=全物件)", all_property_names
    )

properties = (
    [p for p in all_properties if p.get("name") in selected_property_names]
    if selected_property_names
    else all_properties
)
selected_property_ids = {p.get("id") for p in properties}

try:
    bookings_all = load_bookings(date_from.isoformat(), date_to.isoformat())
except Beds24Error as e:
    st.error(f"予約情報の取得でエラーが発生しました: {e}")
    st.stop()

bookings = [b for b in bookings_all if b.get("propertyId") in selected_property_ids]

name_map = property_name_map(properties)
room_labels = unit_label_map(properties)


def room_label(b):
    key = (b.get("propertyId"), b.get("roomId"), b.get("unitId"))
    return room_labels.get(key) or name_map.get(b.get("propertyId"), b.get("propertyId"))


(
    tab_bookings,
    tab_calendar,
    tab_revenue,
    tab_listing,
    tab_ota,
    tab_minpaku,
    tab_pricing,
    tab_profit,
    tab_tax,
) = st.tabs(
    [
        "予約",
        "カレンダー",
        "売上",
        "リスティング問題点",
        "OTA評価(手動入力)",
        "民泊180日管理",
        "単価調整(市場比較)",
        "粗利分析",
        "東京都宿泊税",
    ]
)

def booking_row(b):
    return {
        "物件・部屋": room_label(b),
        "ゲスト": f"{b.get('firstName', '')} {b.get('lastName', '')}".strip(),
        "チャネル": b.get("referer") or "不明",
        "チェックイン": b.get("arrival"),
        "チェックアウト": b.get("departure"),
        "ステータス": b.get("status"),
        "金額": booking_amount(b),
    }


with tab_bookings:
    st.subheader(f"予約一覧 ({date_from} 〜 {date_to})")
    if not bookings:
        st.info("この期間の予約はありません。")
    else:
        df = pd.DataFrame([booking_row(b) for b in bookings])
        st.dataframe(df, use_container_width=True)

        st.markdown("#### 物件別")
        for p in properties:
            pid = p.get("id")
            p_bookings = [b for b in bookings if b.get("propertyId") == pid]
            if not p_bookings:
                continue
            with st.expander(f"🏠 {p.get('name')}({len(p_bookings)}件)"):
                st.dataframe(
                    pd.DataFrame([booking_row(b) for b in p_bookings]),
                    use_container_width=True,
                )

with tab_calendar:
    st.subheader("カレンダー")
    st.caption("部屋ごとに、予約で埋まっている日を一覧で確認できます。")

    cal_col1, cal_col2 = st.columns([2, 1])
    with cal_col1:
        cal_start = st.date_input("表示開始日", dt.date.today(), key="cal_start")
    with cal_col2:
        cal_days = st.number_input(
            "表示日数", min_value=7, max_value=60, value=14, step=7, key="cal_days"
        )
    cal_end = cal_start + dt.timedelta(days=int(cal_days))

    @st.cache_data(ttl=300)
    def load_calendar_bookings(prop_ids, from_date, to_date):
        data = []
        for pid in prop_ids:
            data.extend(
                client.get_bookings(propertyId=pid, arrivalFrom=from_date, arrivalTo=to_date)
            )
        return data

    cal_prop_ids = tuple(p.get("id") for p in properties)
    try:
        cal_bookings = load_calendar_bookings(
            cal_prop_ids,
            (cal_start - dt.timedelta(days=60)).isoformat(),
            cal_end.isoformat(),
        )
    except Beds24Error as e:
        st.error(f"予約データの取得に失敗しました: {e}")
        cal_bookings = []

    date_list = [cal_start + dt.timedelta(days=i) for i in range(int(cal_days))]
    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"]
    date_cols = [f"{d.month}/{d.day}({weekday_jp[d.weekday()]})" for d in date_list]

    room_keys = [
        k
        for k in room_labels
        if k[0] in selected_property_ids and (k[0], k[1]) not in CALENDAR_EXCLUDED_ROOMS
    ]
    room_keys.sort(key=lambda k: (name_map.get(k[0], ""), room_labels[k]))

    CHANNEL_COLORS = {
        "Booking.com": "#cfe2ff",
        "Airbnb": "#ffd6d6",
    }
    DEFAULT_CHANNEL_COLOR = "#e5e5e5"

    row_data = {}
    cell_booking = {}
    rows_order = []
    for k in room_keys:
        label = room_labels[k]
        if label not in row_data:
            row_data[label] = ["" for _ in date_list]
            rows_order.append(label)

    for b in cal_bookings:
        if b.get("status") not in ("new", "confirmed"):
            continue
        key = (b.get("propertyId"), b.get("roomId"), b.get("unitId"))
        label = room_labels.get(key)
        if label is None or label not in row_data:
            continue
        try:
            arrival = dt.date.fromisoformat(b["arrival"])
            departure = dt.date.fromisoformat(b["departure"])
        except (TypeError, ValueError, KeyError):
            continue
        guest = b.get("lastName") or b.get("firstName") or "ゲスト"
        for i, d in enumerate(date_list):
            if arrival <= d < departure:
                row_data[label][i] = guest
                cell_booking[(label, date_cols[i])] = b

    if not rows_order:
        st.info("表示できる部屋がありません。")
    else:
        cal_df = pd.DataFrame(
            [row_data[label] for label in rows_order], index=rows_order, columns=date_cols
        )

        def style_grid(_data):
            styles = pd.DataFrame("", index=cal_df.index, columns=cal_df.columns)
            for label in rows_order:
                for col in date_cols:
                    b = cell_booking.get((label, col))
                    if b is None:
                        continue
                    color = CHANNEL_COLORS.get(b.get("referer"), DEFAULT_CHANNEL_COLOR)
                    styles.loc[label, col] = f"background-color: {color}"
            return styles

        event = st.dataframe(
            cal_df.style.apply(style_grid, axis=None),
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-cell",
            key="calendar_grid",
        )
        st.caption(
            "🟦 Booking.com　🟥 Airbnb　⬜ その他のチャネル(ステータスが new/confirmed の予約のみ反映)。"
            " マスをクリックすると、その予約の詳細(人数・国籍・料金)を下に表示します。"
        )

        selected_cells = event.selection.cells if event and event.selection else []
        if selected_cells:
            row_idx, col_name = selected_cells[0]
            label = rows_order[row_idx]
            b = cell_booking.get((label, col_name))
            if b is None:
                st.info("この日は空室です。")
            else:
                st.markdown(f"#### {label} — {col_name} の予約詳細")
                d1, d2, d3, d4 = st.columns(4)
                guests = int(b.get("numAdult") or 0) + int(b.get("numChild") or 0)
                d1.metric("宿泊人数", f"{guests}人")
                d2.metric("国籍", country_name_ja(b.get("country")))
                d3.metric("料金", f"¥{booking_amount(b):,.0f}" if booking_amount(b) else "不明")
                d4.metric("チャネル", b.get("referer") or "不明")
                st.caption(
                    f"ゲスト: {b.get('firstName', '')} {b.get('lastName', '')} / "
                    f"チェックイン: {b.get('arrival')} / チェックアウト: {b.get('departure')} / "
                    f"ステータス: {b.get('status')}"
                )

with tab_revenue:
    st.subheader("売上サマリー")
    if not bookings:
        st.info("この期間の予約がないため、売上はありません。")
    else:
        rows = []
        for b in bookings:
            amount = booking_amount(b)
            if amount is None:
                continue
            rows.append(
                {
                    "物件": name_map.get(b.get("propertyId"), b.get("propertyId")),
                    "チャネル": b.get("referer") or "不明",
                    "チェックイン月": (b.get("arrival") or "")[:7],
                    "金額": amount,
                }
            )
        if not rows:
            st.warning(
                "予約データから金額を取得できませんでした。"
                " 下の「生データを見る」で price / invoiceItems の実際の構造を確認してください。"
            )
        else:
            df = pd.DataFrame(rows)
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("合計売上", f"{df['金額'].sum():,.0f}")
                by_month = df.groupby("チェックイン月")["金額"].sum()
                st.bar_chart(by_month)
            with col2:
                by_property = df.groupby("物件")["金額"].sum()
                st.bar_chart(by_property)
            with col3:
                by_channel = df.groupby("チャネル")["金額"].sum()
                st.bar_chart(by_channel)

            st.markdown("#### 物件別")
            for p in properties:
                pname = p.get("name")
                p_df = df[df["物件"] == pname]
                if p_df.empty:
                    continue
                st.markdown(f"##### 🏠 {pname}")
                pcol1, pcol2 = st.columns(2)
                with pcol1:
                    st.metric("売上", f"{p_df['金額'].sum():,.0f}")
                    st.bar_chart(p_df.groupby("チェックイン月")["金額"].sum())
                with pcol2:
                    st.bar_chart(p_df.groupby("チャネル")["金額"].sum())

    with st.expander("生データを見る(デバッグ用)"):
        st.json(bookings[:3] if bookings else [])

with tab_listing:
    st.subheader("リスティングの問題点")
    st.caption(
        "説明文・写真はBeds24内部には保存されず、Airbnb/Booking.com側で直接管理されているため"
        "(各チャネルの管理画面で直接ご確認ください)、ここではBeds24の設定データから"
        "実際に検出できる問題のみをチェックしています。"
    )
    if not properties:
        st.info("物件情報が取得できませんでした。")
    else:
        issue_rows = []
        for p in properties:
            name = p.get("name") or str(p.get("id"))
            issues = []
            if not p.get("name"):
                issues.append("物件名が未設定")

            for rt in p.get("roomTypes", []) or []:
                rt_name = rt.get("name", "")
                if not rt.get("minPrice"):
                    issues.append(f"「{rt_name}」の最低価格(minPrice)が未設定")
                if not rt.get("rackRate"):
                    issues.append(f"「{rt_name}」の基準料金(rackRate)が未設定")

            for issue in issues:
                issue_rows.append({"物件": name, "問題点": issue})

        if not issue_rows:
            st.success("料金設定の問題点は見つかりませんでした。")
        else:
            st.dataframe(pd.DataFrame(issue_rows), use_container_width=True)

        with st.expander("生データを見る(デバッグ用)"):
            st.json(properties[:2] if properties else [])

with tab_ota:
    st.subheader("OTA評価(手動入力)")
    st.caption(
        "Airbnb・Booking.comにはAPIで個人が直接繋ぐ方法がないため、"
        "各OTAの管理画面で確認した数値をここに手入力して管理します。"
    )

    ota_metrics = load_ota_metrics()

    def fmt(value):
        return "-" if value in (None, "") else str(value)

    summary_rows = []
    for p in properties:
        pid = str(p.get("id"))
        m = ota_metrics.get(pid, {})
        air = m.get("airbnb", {})
        booking = m.get("booking", {})
        summary_rows.append(
            {
                "物件": p.get("name"),
                "Airbnb スーパーホスト": "○" if air.get("superhost") else "-",
                "Airbnb 評価": fmt(air.get("rating")),
                "Airbnb レビュー数": fmt(air.get("reviews")),
                "Booking Geniusレベル": fmt(booking.get("genius_level")),
                "Booking 評価": fmt(booking.get("rating")),
                "Booking レビュー数": fmt(booking.get("reviews")),
                "最終更新日": m.get("updated", "未入力"),
            }
        )
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    st.markdown("#### 入力・更新")
    options = {p.get("name"): str(p.get("id")) for p in properties}
    selected_name = st.selectbox("物件を選択", list(options.keys()))
    selected_id = options[selected_name]
    current = ota_metrics.get(selected_id, {})
    current_air = current.get("airbnb", {})
    current_booking = current.get("booking", {})

    if st.button("Beds24の公式APIから評価・レビュー数を取得", key=f"fetch_reviews_{selected_id}"):
        selected_property = next(p for p in properties if str(p.get("id")) == selected_id)
        room_ids = [rt["id"] for rt in selected_property.get("roomTypes", [])]
        try:
            air_result = official_reviews.get_airbnb_summary(client, room_ids)
            booking_result = official_reviews.get_booking_summary(client, int(selected_id))

            ota_metrics.setdefault(selected_id, {})
            ota_metrics[selected_id]["airbnb"] = {
                "superhost": current_air.get("superhost", False),
                "rating": air_result["avg_rating"] or 0,
                "reviews": air_result["count"],
            }
            ota_metrics[selected_id]["booking"] = {
                "genius_level": current_booking.get("genius_level", 0),
                "rating": booking_result["avg_rating"] or 0,
                "reviews": booking_result["count"],
            }
            ota_metrics[selected_id]["updated"] = dt.date.today().isoformat()
            save_ota_metrics(ota_metrics)
            st.success(
                f"取得しました: Airbnb 評価{air_result['avg_rating']}/レビュー{air_result['count']}件 "
                f" / Booking.com 評価{booking_result['avg_rating']}/レビュー{booking_result['count']}件"
            )
            st.rerun()
        except Beds24Error as e:
            st.error(str(e))

    st.caption(
        "評価・レビュー数はBeds24の公式API(/channels/airbnb/reviews, /channels/booking/reviews)から取得します。"
        " スーパーホスト・Geniusレベルは公式APIで取得できないため、下のフォームで手入力してください。"
    )

    with st.form("ota_metrics_form"):
        col_air, col_booking = st.columns(2)
        with col_air:
            st.markdown("**Airbnb**")
            air_superhost = st.checkbox("スーパーホスト", value=bool(current_air.get("superhost")))
            air_rating = st.number_input(
                "評価 (0〜5)", min_value=0.0, max_value=5.0, step=0.1,
                value=float(current_air.get("rating") or 0),
            )
            air_reviews = st.number_input(
                "レビュー数", min_value=0, step=1, value=int(current_air.get("reviews") or 0)
            )
        with col_booking:
            st.markdown("**Booking.com**")
            booking_genius = st.selectbox(
                "Geniusレベル", [0, 1, 2, 3],
                index=[0, 1, 2, 3].index(int(current_booking.get("genius_level") or 0)),
            )
            booking_rating = st.number_input(
                "評価 (0〜10)", min_value=0.0, max_value=10.0, step=0.1,
                value=float(current_booking.get("rating") or 0),
            )
            booking_reviews = st.number_input(
                "レビュー数", min_value=0, step=1,
                value=int(current_booking.get("reviews") or 0),
                key="booking_reviews",
            )

        if st.form_submit_button("保存"):
            ota_metrics[selected_id] = {
                "airbnb": {
                    "superhost": air_superhost,
                    "rating": air_rating,
                    "reviews": air_reviews,
                },
                "booking": {
                    "genius_level": booking_genius,
                    "rating": booking_rating,
                    "reviews": booking_reviews,
                },
                "updated": dt.date.today().isoformat(),
            }
            save_ota_metrics(ota_metrics)
            st.success(f"{selected_name} の情報を保存しました。")
            st.rerun()

with tab_minpaku:
    st.subheader("民泊新法 180日ルール管理")
    st.caption(
        "住宅宿泊事業法の算定期間(毎年4/1正午〜翌年3/31)に基づき、"
        "年間180日の上限に対する稼働日数と、マンスリー賃貸で埋めるべき期間を計算します。"
        " 国への届出(2ヶ月に一度)とは別に、日々の目安としてご活用ください。"
    )

    for label, (prop_id, room_id) in minpaku.REGULATED_ROOMS.items():
        if prop_id not in selected_property_ids:
            continue
        try:
            reserved = minpaku.RESERVED_PREMIUM_WINDOWS.get(label, [])
            status = minpaku.compute_room_status(client, room_id, reserved_windows=reserved)
        except Beds24Error as e:
            st.error(f"{label}: データ取得に失敗しました ({e})")
            continue

        st.markdown(f"### {label}")
        st.caption(f"算定期間: {status['fy_start']} 〜 {status['fy_end']}")

        col1, col2, col3 = st.columns(3)
        col1.metric("使用済み日数", f"{status['used_days']} / 180 日")
        col2.metric("残り日数", f"{status['remaining_days']} 日")
        days_left_in_fy = (status["fy_end"] - dt.date.today()).days + 1
        col3.metric("年度末までの残り日数", f"{days_left_in_fy} 日")

        progress = min(1.0, status["used_days"] / minpaku.ANNUAL_LIMIT_DAYS)
        st.progress(progress)

        if status.get("reserved_over_budget"):
            st.error(
                "⚠️ 優先確保したい高需要期の日数が、残りの民泊予算を超えています。"
                " 確保期間を短くするか、他の期間で予算を使わないよう調整してください。"
            )

        if reserved:
            reserved_text = "、".join(f"{s}〜{e}" for s, e in reserved)
            st.caption(f"🌸 優先確保期間: {reserved_text}")

        if not status["gaps"]:
            st.info("今後の空き期間はありません(年度末まで予約で埋まっています)。")
        else:
            suggestion_labels = {
                "minpaku": "🟢 民泊で販売可",
                "monthly": "🔵 マンスリー推奨(お得感価格)",
                "premium": "🌸 高需要期(強気価格で確保)",
            }
            rows = []
            for g in status["gaps"]:
                rows.append(
                    {
                        "開始": g["from"],
                        "終了": g["to"],
                        "泊数": g["nights"],
                        "提案": suggestion_labels.get(g["suggestion"], g["suggestion"]),
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

            monthly_total = sum(g["nights"] for g in status["gaps"] if g["suggestion"] == "monthly")
            premium_total = sum(g["nights"] for g in status["gaps"] if g["suggestion"] == "premium")
            if monthly_total > 0:
                st.warning(
                    f"民泊の残り予算(180日枠)の範囲内で、合計 **{monthly_total}泊** 分は"
                    f" マンスリー賃貸等(お得感価格)への切り替えを検討してください。"
                )
            if premium_total > 0:
                st.success(
                    f"🌸 高需要期として **{premium_total}泊** 分を民泊予算から優先確保しています。"
                    " この期間は強気の価格設定で短期販売してください。"
                )

        st.divider()

    st.caption(
        "⚠️ 180日の上限は国の基準です。物件所在地の自治体によっては条例でさらに短い上限が"
        "設定されている場合があるため、別途ご確認ください。"
    )

with tab_pricing:
    st.subheader("単価調整(AirROI市場比較)")
    st.caption(
        "AirROIの近隣競合データ(直近12ヶ月の平均単価・稼働率)と、自社の実績を比較します。"
        " 取得は物件ごとに有料API呼び出し(1回 約$0.10)が発生するため、ボタンを押したときだけ取得します。"
    )

    if st.button("全物件の市場データを取得", key="fetch_airroi"):
        airroi = get_airroi_client()
        today = dt.date.today()
        results = {}
        for p in properties:
            pid = p.get("id")
            spec = PROPERTY_SPECS.get(pid)
            if not spec:
                continue
            try:
                listings = airroi.get_comparables(
                    latitude=p["latitude"],
                    longitude=p["longitude"],
                    bedrooms=spec["bedrooms"],
                    guests=spec["guests"],
                    baths=spec["baths"],
                )
                summary = summarize_comparables(listings)
            except AirroiError as e:
                summary = {"error": str(e)}
            results[pid] = summary
        st.session_state["airroi_results"] = results
        st.session_state["airroi_fetched_at"] = today.isoformat()

    airroi_results = st.session_state.get("airroi_results")

    if not airroi_results:
        st.info("上のボタンを押すと、市場データを取得します。")
    else:
        st.caption(f"取得日: {st.session_state.get('airroi_fetched_at')}")
        today = dt.date.today()
        rows = []
        for p in properties:
            pid = p.get("id")
            summary = airroi_results.get(pid)
            if not summary:
                continue
            if summary.get("error"):
                rows.append({"物件": p.get("name"), "エラー": summary["error"]})
                continue

            window_end = today + dt.timedelta(days=90)
            room_ids = [rt["id"] for rt in p.get("roomTypes", [])]
            own_bookings = []
            for rid in room_ids:
                # arrivalFrom is far in the past so bookings that checked in before
                # today but are still ongoing (or extend into the window) aren't missed
                own_bookings.extend(
                    client.get_bookings(
                        roomId=rid,
                        arrivalFrom=(today - dt.timedelta(days=400)).isoformat(),
                        arrivalTo=window_end.isoformat(),
                    )
                )
            own_prices = []
            own_nights = 0
            for b in own_bookings:
                if b.get("status") not in ("new", "confirmed"):
                    continue
                a = dt.date.fromisoformat(b["arrival"])
                d = dt.date.fromisoformat(b["departure"])
                n_full = (d - a).days
                # clip to the [today, window_end] reporting window
                overlap = max(0, (min(d, window_end) - max(a, today)).days)
                own_nights += overlap
                if b.get("price") and n_full > 0:
                    own_prices.append(b["price"] / n_full)
            own_avg = round(sum(own_prices) / len(own_prices)) if own_prices else None
            own_occupancy = round(own_nights / 90, 3)

            rows.append(
                {
                    "物件": p.get("name"),
                    "自社 平均単価(今後90日)": own_avg or "-",
                    "市場 平均単価": summary["avg_rate"],
                    "市場 中央値": summary["median_rate"],
                    "市場 価格帯": f"{summary['min_rate']}〜{summary['max_rate']}",
                    "自社 稼働率(今後90日)": f"{own_occupancy*100:.0f}%",
                    "市場 平均稼働率(直近12ヶ月)": (
                        f"{summary['avg_occupancy']*100:.0f}%" if summary["avg_occupancy"] else "-"
                    ),
                    "競合件数": summary["count"],
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption(
            "自社の稼働率は「今後90日の予約済み日数」、市場の稼働率は「直近12ヶ月の実績」なので"
            "単純比較はできませんが、価格帯の目安として参考にしてください。"
        )

with tab_profit:
    st.subheader("粗利分析")
    st.caption(
        "物件ごとに家賃・清掃費・固定費・変動費を設定すると、OTA手数料を差し引いた入金額から"
        "経費を引いた実際の粗利を月別に自動計算します。月をまたぐ予約は泊数で日割りして按分します。"
    )

    expenses = finance.load_expenses()

    with st.expander("経費設定(物件ごと)"):
        profit_options = {p.get("name"): str(p.get("id")) for p in properties}
        profit_selected_name = st.selectbox(
            "物件を選択", list(profit_options.keys()), key="profit_property_select"
        )
        profit_selected_id = profit_options[profit_selected_name]
        current_exp = expenses.get(profit_selected_id, finance.default_expense())

        with st.form("expense_form"):
            rent = st.number_input(
                "家賃(月額、円)", min_value=0, step=1000, value=int(current_exp.get("rent") or 0)
            )
            cleaning_cost = st.number_input(
                "清掃費(1予約あたり、円)",
                min_value=0,
                step=500,
                value=int(current_exp.get("cleaning_cost") or 0),
            )
            fixed_cost = st.number_input(
                "その他固定費(月額、円)",
                min_value=0,
                step=500,
                value=int(current_exp.get("fixed_cost") or 0),
            )
            variable_rate_pct = st.number_input(
                "変動費率(売上に対する%。決済手数料・水道光熱費等の目安)",
                min_value=0.0,
                max_value=100.0,
                step=0.1,
                value=float(current_exp.get("variable_rate") or 0) * 100,
            )
            if st.form_submit_button("保存"):
                expenses[profit_selected_id] = {
                    "rent": rent,
                    "cleaning_cost": cleaning_cost,
                    "fixed_cost": fixed_cost,
                    "variable_rate": variable_rate_pct / 100,
                }
                finance.save_expenses(expenses)
                st.success(f"{profit_selected_name} の経費設定を保存しました。")
                st.rerun()

    today = dt.date.today()
    start_month = dt.date(today.year, today.month, 1)
    for _ in range(3):
        start_month = (start_month - dt.timedelta(days=1)).replace(day=1)
    month_keys = finance.month_range(start_month, 9)
    window_start = start_month
    last_month = dt.date.fromisoformat(month_keys[-1] + "-01")
    window_end = (
        dt.date(last_month.year + 1, 1, 1) if last_month.month == 12
        else dt.date(last_month.year, last_month.month + 1, 1)
    ) - dt.timedelta(days=1)

    @st.cache_data(ttl=300)
    def load_profit_bookings(prop_ids, from_date, to_date):
        data = {}
        for pid in prop_ids:
            data[pid] = client.get_bookings(
                propertyId=pid, arrivalFrom=from_date, arrivalTo=to_date
            )
        return data

    prop_ids = tuple(p.get("id") for p in properties)
    try:
        bookings_by_property = load_profit_bookings(
            prop_ids,
            (window_start - dt.timedelta(days=90)).isoformat(),
            window_end.isoformat(),
        )
    except Beds24Error as e:
        st.error(f"予約データの取得に失敗しました: {e}")
        bookings_by_property = {}

    pnl = finance.compute_monthly_pnl(bookings_by_property, expenses, month_keys)

    metric_rows = [
        ("売上", "revenue", "¥"),
        ("OTA手数料", "commission", "¥-"),
        ("入金額", "net_payment", "¥"),
        ("泊数", "nights", ""),
        ("予約件数", "bookings", ""),
        ("家賃", "rent", "¥-"),
        ("清掃費", "cleaning_cost", "¥-"),
        ("固定費", "fixed_cost", "¥-"),
        ("変動費", "variable_cost", "¥-"),
        ("粗利", "gross_profit", "¥"),
    ]

    def build_table(metrics_by_month):
        table = {}
        for mk in month_keys:
            m = metrics_by_month[mk]
            col = {}
            for label, key, fmt in metric_rows:
                val = m[key]
                if fmt == "¥":
                    col[label] = f"¥{val:,.0f}"
                elif fmt == "¥-":
                    col[label] = f"(¥{val:,.0f})" if val else "-"
                else:
                    col[label] = f"{val:,.0f}"
            table[mk] = col
        return pd.DataFrame(table)

    st.markdown("#### 全物件合計")
    st.dataframe(build_table(pnl["_total"]), use_container_width=True)

    for p in properties:
        pid = p.get("id")
        if pid not in pnl:
            continue
        st.markdown(f"#### 🏠 {p.get('name')}")
        st.dataframe(build_table(pnl[pid]), use_container_width=True)

    csv_rows = []
    for pid in list(prop_ids) + ["_total"]:
        pname = "全物件合計" if pid == "_total" else name_map.get(pid, pid)
        for mk in month_keys:
            m = pnl.get(pid, {}).get(mk)
            if not m:
                continue
            row = {"物件": pname, "月": mk}
            row.update({key: m[key] for _, key, _ in metric_rows})
            csv_rows.append(row)
    if csv_rows:
        csv_data = pd.DataFrame(csv_rows).to_csv(index=False).encode("utf-8-sig")
        st.download_button("CSV出力", csv_data, file_name="粗利分析.csv", mime="text/csv")

    st.caption(
        "入金額 = 売上 − OTA手数料(Beds24の commission フィールド)。"
        " 粗利 = 入金額 − 家賃 − 清掃費(1予約あたり×予約件数) − 固定費 − 変動費(売上×変動費率)。"
        " 予約件数はチェックイン月でカウントします。"
    )

with tab_tax:
    st.subheader("東京都宿泊税(概算)")
    st.caption(
        "現行制度(〜2027/3/31宿泊分)の納税義務者は「旅館・ホテル」許可の施設のみのため、"
        "The Nest Koenji 103・201、あやめマンション201のみを対象としています。"
        " The Nest Koenji 101・kiryo MODEビル・Kiryo Tama Riversideは簡易宿所/民泊許可のため、"
        "見直し後の制度(2027/4/1宿泊分〜、簡易宿所・民泊も課税対象に追加)から対象になります。"
        " 税率は現行(1人1泊1万円未満非課税、1万〜1.5万円未満100円、1.5万円以上200円)と"
        "見直し後(1万3千円未満非課税、それ以上は宿泊料金の3%)に対応しています。"
        " 単価は、Booking.comは日別の実単価、Airbnbは清掃費を除いた素泊まり額が取得できる"
        "場合はそちらを優先し、取得できない場合のみ予約合計金額(清掃費等込み)を泊数・人数で"
        "割った概算値を使います(下表「単価の根拠」列で確認できます)。"
        " あくまで概算です。正式な申告には東京都主税局の最新情報を必ずご確認ください。"
    )

    tokyo_properties = [p for p in properties if lodging_tax.is_tokyo_property(p)]
    if not tokyo_properties:
        st.info("東京都内の物件が見つかりませんでした。")
    else:
        tax_month = st.date_input(
            "対象月", dt.date(today.year, today.month, 1), key="tax_month_input"
        )
        tax_window_start = dt.date(tax_month.year, tax_month.month, 1)
        tax_window_end = (
            dt.date(tax_window_start.year + 1, 1, 1)
            if tax_window_start.month == 12
            else dt.date(tax_window_start.year, tax_window_start.month + 1, 1)
        ) - dt.timedelta(days=1)

        @st.cache_data(ttl=300)
        def load_tax_bookings(prop_ids, from_date, to_date):
            data = []
            for pid in prop_ids:
                data.extend(
                    client.get_bookings(propertyId=pid, arrivalFrom=from_date, arrivalTo=to_date)
                )
            return data

        tokyo_ids = tuple(p.get("id") for p in tokyo_properties)
        try:
            tax_bookings = load_tax_bookings(
                tokyo_ids, tax_window_start.isoformat(), tax_window_end.isoformat()
            )
        except Beds24Error as e:
            st.error(f"予約データの取得に失敗しました: {e}")
            tax_bookings = []

        rows = []
        by_property = {}
        total_tax = 0
        total_nights = 0
        for b in tax_bookings:
            result = lodging_tax.compute_booking_tax(b)
            if result is None:
                continue
            total_tax += result["total_tax"]
            total_nights += result["nights"]
            pname = name_map.get(b.get("propertyId"), b.get("propertyId"))
            stat = by_property.setdefault(pname, {"tax": 0, "nights": 0, "bookings": 0})
            stat["tax"] += result["total_tax"]
            stat["nights"] += result["nights"]
            stat["bookings"] += 1
            rows.append(
                {
                    "物件": pname,
                    "チェックイン": b.get("arrival"),
                    "チェックアウト": b.get("departure"),
                    "ゲスト": f"{b.get('firstName', '')} {b.get('lastName', '')}".strip(),
                    "泊数": result["nights"],
                    "人数": result["guests"],
                    "1人1泊単価(目安)": f"¥{result['per_person_night_rate']:,.0f}",
                    "単価の根拠": result["rate_source"],
                    "税区分": result["label"],
                    "宿泊税額": f"¥{result['total_tax']:,.0f}",
                }
            )

        col1, col2 = st.columns(2)
        col1.metric("対象月の宿泊税合計(概算)", f"¥{total_tax:,.0f}")
        col2.metric("対象泊数", f"{total_nights}泊")

        if not rows:
            st.info("この月は対象予約がありません。")
        else:
            st.markdown("#### 物件別")
            summary_rows = [
                {
                    "物件": pname,
                    "予約件数": s["bookings"],
                    "泊数": s["nights"],
                    "宿泊税額": f"¥{s['tax']:,.0f}",
                }
                for pname, s in by_property.items()
            ]
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

            st.markdown("#### 予約一覧")
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
            csv_data = pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "CSV出力", csv_data, file_name="東京都宿泊税.csv", mime="text/csv", key="tax_csv"
            )
