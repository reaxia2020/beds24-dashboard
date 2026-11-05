import datetime as dt
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "autoreply.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beds24_message_id INTEGER UNIQUE,
            property_id INTEGER,
            booking_id INTEGER,
            guest_message TEXT,
            category TEXT,
            draft_reply TEXT,
            auto_sent INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registry_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER,
            tier TEXT,
            sent_date TEXT,
            sent_at TEXT,
            UNIQUE(booking_id, sent_date)
        )
        """
    )
    conn.commit()
    conn.close()


def reminder_already_sent_today(booking_id, sent_date):
    """その予約に対して、指定の日(YYYY-MM-DD)に既に催促を送っているか。
    未提出が続く限り、この予約の名簿情報が無いと宿泊できないため、1日1回まで毎日送り続ける。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM registry_reminders WHERE booking_id = ? AND sent_date = ?",
        (booking_id, sent_date),
    ).fetchone()
    conn.close()
    return row is not None


def mark_reminder_sent(booking_id, tier, sent_date):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO registry_reminders (booking_id, tier, sent_date, sent_at) "
        "VALUES (?, ?, ?, ?)",
        (booking_id, tier, sent_date, dt.datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def list_reminders(limit=200):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM registry_reminders ORDER BY sent_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_processed(beds24_message_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM messages WHERE beds24_message_id = ?", (beds24_message_id,)
    ).fetchone()
    conn.close()
    return row is not None


def save_message(
    beds24_message_id, property_id, booking_id, guest_message, category, draft_reply, auto_sent
):
    conn = get_conn()
    conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (beds24_message_id, property_id, booking_id, guest_message, category,
             draft_reply, auto_sent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            beds24_message_id,
            property_id,
            booking_id,
            guest_message,
            category,
            draft_reply,
            1 if auto_sent else 0,
            dt.datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def list_messages(limit=200):
    """レビュー対象のメッセージ一覧。seed_baseline.pyで登録した過去分(baseline_seed)は
    単なる重複防止用の記録であり、レビュー対象ではないため除外する。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE category != 'baseline_seed' "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
