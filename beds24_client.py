import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

BASE_URL = "https://api.beds24.com/v2"
ENV_PATH = Path(__file__).parent / ".env"
TOKEN_CACHE_PATH = Path(__file__).parent / ".token_cache.json"


class Beds24Error(RuntimeError):
    pass


class Beds24Client:
    def __init__(self):
        load_dotenv(ENV_PATH)
        self._token = None
        self._token_expires_at = 0
        self._load_token_cache()

    def _load_token_cache(self):
        if TOKEN_CACHE_PATH.exists():
            data = json.loads(TOKEN_CACHE_PATH.read_text())
            self._token = data.get("token")
            self._token_expires_at = data.get("expires_at", 0)

    def _save_token_cache(self, token, expires_in):
        self._token = token
        self._token_expires_at = time.time() + expires_in - 60
        TOKEN_CACHE_PATH.write_text(
            json.dumps({"token": token, "expires_at": self._token_expires_at})
        )

    def _read_env(self, key):
        import os

        load_dotenv(ENV_PATH, override=True)
        return os.environ.get(key, "").strip()

    def _exchange_invite_code(self, invite_code):
        headers = {"code": invite_code, "accept": "application/json"}
        resp = requests.get(f"{BASE_URL}/authentication/setup", headers=headers, timeout=30)
        if resp.status_code == 405:
            resp = requests.post(f"{BASE_URL}/authentication/setup", headers=headers, timeout=30)
        if not resp.ok:
            raise Beds24Error(
                f"招待コードの交換に失敗しました ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        refresh_token = data["refreshToken"]
        set_key(str(ENV_PATH), "BEDS24_REFRESH_TOKEN", refresh_token)
        set_key(str(ENV_PATH), "BEDS24_INVITE_CODE", "")
        self._save_token_cache(data["token"], data.get("expiresIn", 86400))
        return refresh_token

    def _refresh_access_token(self, refresh_token):
        resp = requests.get(
            f"{BASE_URL}/authentication/token",
            headers={"refreshToken": refresh_token, "accept": "application/json"},
            timeout=30,
        )
        if not resp.ok:
            raise Beds24Error(
                f"アクセストークンの更新に失敗しました ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        self._save_token_cache(data["token"], data.get("expiresIn", 86400))
        # Beds24がトークン更新時にrefreshTokenをローテーションする場合に備えて保存する
        if data.get("refreshToken") and data["refreshToken"] != refresh_token:
            set_key(str(ENV_PATH), "BEDS24_REFRESH_TOKEN", data["refreshToken"])

    def _ensure_token(self):
        if self._token and time.time() < self._token_expires_at:
            return

        refresh_token = self._read_env("BEDS24_REFRESH_TOKEN")
        if refresh_token:
            self._refresh_access_token(refresh_token)
            return

        invite_code = self._read_env("BEDS24_INVITE_CODE")
        if invite_code:
            self._exchange_invite_code(invite_code)
            return

        raise Beds24Error(
            ".env に BEDS24_INVITE_CODE か BEDS24_REFRESH_TOKEN を設定してください。"
        )

    def _get(self, path, params=None):
        self._ensure_token()
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers={"token": self._token, "accept": "application/json"},
            params=params or {},
            timeout=30,
        )
        if resp.status_code == 401:
            # token may have just expired; force one retry with a fresh token
            self._token = None
            self._ensure_token()
            resp = requests.get(
                f"{BASE_URL}{path}",
                headers={"token": self._token, "accept": "application/json"},
                params=params or {},
                timeout=30,
            )
        if not resp.ok:
            raise Beds24Error(f"{path} の取得に失敗しました ({resp.status_code}): {resp.text}")
        return resp.json()

    def get_properties(self, **params):
        data = self._get("/properties", params=params)
        return data.get("data", data)

    def get_bookings(self, **params):
        data = self._get("/bookings", params=params)
        return data.get("data", data)

    def get_airbnb_reviews(self, room_id):
        data = self._get("/channels/airbnb/reviews", params={"roomId": room_id})
        return data.get("data", [])

    def get_booking_reviews(self, property_id, since="2000-01-01"):
        data = self._get(
            "/channels/booking/reviews", params={"propertyId": property_id, "from": since}
        )
        return data.get("data", [])
