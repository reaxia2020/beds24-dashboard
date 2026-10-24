import os

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.airroi.com"
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


class AirroiError(RuntimeError):
    pass


class AirroiClient:
    def __init__(self):
        load_dotenv(ENV_PATH)
        self._key = os.environ.get("AIRROI_API_KEY", "").strip()

    def _get(self, path, params=None):
        if not self._key:
            raise AirroiError(".env に AIRROI_API_KEY を設定してください。")
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers={"X-API-KEY": self._key, "Accept": "application/json"},
            params=params or {},
            timeout=20,
        )
        if not resp.ok:
            raise AirroiError(f"{path} の取得に失敗しました ({resp.status_code}): {resp.text}")
        return resp.json()

    def get_comparables(self, latitude, longitude, bedrooms, guests, baths=1, exclude_listing_id=None):
        data = self._get(
            "/listings/comparables",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "bedrooms": bedrooms,
                "guests": guests,
                "baths": baths,
            },
        )
        listings = data.get("listings", [])
        if exclude_listing_id is not None:
            listings = [
                l for l in listings if l.get("listing_info", {}).get("listing_id") != exclude_listing_id
            ]
        return listings


def summarize_comparables(listings):
    rates = []
    occupancies = []
    for l in listings:
        pm = l.get("performance_metrics") or {}
        if pm.get("ttm_avg_rate"):
            rates.append(pm["ttm_avg_rate"])
        if pm.get("ttm_occupancy") is not None:
            occupancies.append(pm["ttm_occupancy"])

    if not rates:
        return {"count": 0, "avg_rate": None, "median_rate": None, "avg_occupancy": None}

    sorted_rates = sorted(rates)
    mid = len(sorted_rates) // 2
    median_rate = (
        sorted_rates[mid]
        if len(sorted_rates) % 2 == 1
        else (sorted_rates[mid - 1] + sorted_rates[mid]) / 2
    )

    return {
        "count": len(rates),
        "avg_rate": round(sum(rates) / len(rates)),
        "median_rate": round(median_rate),
        "avg_occupancy": round(sum(occupancies) / len(occupancies), 3) if occupancies else None,
        "min_rate": round(min(rates)),
        "max_rate": round(max(rates)),
    }
