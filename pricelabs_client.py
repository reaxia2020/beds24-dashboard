import os

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.pricelabs.co"
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


class PriceLabsError(RuntimeError):
    pass


class PriceLabsClient:
    def __init__(self):
        load_dotenv(ENV_PATH)
        self._key = os.environ.get("PRICELABS_API_KEY", "").strip()

    def _headers(self):
        if not self._key:
            raise PriceLabsError(".env に PRICELABS_API_KEY を設定してください。")
        return {"X-API-Key": self._key, "Content-Type": "application/json"}

    def list_listings(self, **params):
        resp = requests.get(
            f"{BASE_URL}/v1/listings_minimal", headers=self._headers(), params=params, timeout=20
        )
        if not resp.ok:
            raise PriceLabsError(f"listings_minimal の取得に失敗しました ({resp.status_code}): {resp.text}")
        return resp.json().get("listings", [])

    def find_listing(self, name_contains):
        name_contains = name_contains.lower()
        for l in self.list_listings():
            if name_contains in (l.get("listing_name") or "").lower():
                return l
        return None

    def update_listing(self, listing_id, pms, base=None, min_price=None, max_price=None):
        payload = {"id": listing_id, "pms": pms}
        if base is not None:
            payload["base"] = base
        if min_price is not None:
            payload["min"] = min_price
        if max_price is not None:
            payload["max"] = max_price

        resp = requests.post(
            f"{BASE_URL}/v1/listings",
            headers=self._headers(),
            json={"listings": [payload]},
            timeout=20,
        )
        if not resp.ok:
            raise PriceLabsError(f"価格更新に失敗しました ({resp.status_code}): {resp.text}")
        return resp.json()
