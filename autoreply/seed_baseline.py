"""初回デプロイ前に1回だけ実行するスクリプト。

process_new_messages() は「DBに記録のないゲストメッセージ」を新着とみなして分類するため、
何もせずに起動すると過去の全メッセージ(数百〜数千件)がAPI呼び出し対象になってしまう。
このスクリプトで既存メッセージを先に「baseline_seed」として記録しておくことで、
サービス開始後は本当に新しいメッセージだけが分類対象になるようにする。

使い方: python -m autoreply.seed_baseline
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from beds24_client import Beds24Client  # noqa: E402

from . import storage

PROPERTY_IDS = [328093, 328094, 329645, 329647, 341983]


def main():
    storage.init_db()
    client = Beds24Client()
    total_seeded = 0
    total_skipped = 0

    for property_id in PROPERTY_IDS:
        page = 1
        while True:
            msgs = client._get(
                "/bookings/messages", params={"propertyId": property_id, "page": page}
            )
            data = msgs.get("data", [])
            for m in data:
                if m.get("source") != "guest":
                    continue
                beds24_message_id = m.get("id")
                if storage.is_processed(beds24_message_id):
                    total_skipped += 1
                    continue
                storage.save_message(
                    beds24_message_id=beds24_message_id,
                    property_id=property_id,
                    booking_id=m.get("bookingId"),
                    guest_message=m.get("message"),
                    category="baseline_seed",
                    draft_reply=None,
                    auto_sent=False,
                )
                total_seeded += 1
            if not msgs.get("pages", {}).get("nextPageExists"):
                break
            page += 1
        print(f"property {property_id}: done")

    print(f"seeded: {total_seeded}, already present: {total_skipped}")


if __name__ == "__main__":
    main()
