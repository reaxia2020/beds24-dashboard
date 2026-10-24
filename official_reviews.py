"""Beds24の公式チャネルAPI経由でAirbnb/Booking.comのレビューを取得する。
(スクレイピングではなく /channels/airbnb/reviews, /channels/booking/reviews を使用)
"""


def get_airbnb_summary(client, room_ids):
    """property配下の全roomIdのAirbnbレビューを集計する。"""
    all_ratings = []
    for room_id in room_ids:
        for r in client.get_airbnb_reviews(room_id):
            if r.get("overall_rating") is not None:
                all_ratings.append(r["overall_rating"])
    if not all_ratings:
        return {"count": 0, "avg_rating": None}
    return {"count": len(all_ratings), "avg_rating": round(sum(all_ratings) / len(all_ratings), 2)}


def get_booking_summary(client, property_id, since="2000-01-01"):
    scores = []
    for r in client.get_booking_reviews(property_id, since=since):
        score = (r.get("scoring") or {}).get("review_score")
        if score is not None:
            scores.append(score)
    if not scores:
        return {"count": 0, "avg_rating": None}
    return {"count": len(scores), "avg_rating": round(sum(scores) / len(scores), 2)}
