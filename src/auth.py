"""2-legged (Client Credentials) OAuth against Autodesk Platform Services.

Verified 2026-07-22 against https://aps.autodesk.com/en/docs/oauth/v2/tutorials/get-2-legged-token
and https://aps.autodesk.com/en/docs/oauth/v2/developers_guide/scopes/
"""

import base64
import time

import requests

TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"

SCOPES = "data:read account:read"


class AuthError(RuntimeError):
    pass


class TokenProvider:
    """Fetches and caches a 2-legged token, refreshing shortly before it expires."""

    def __init__(self, client_id, client_secret, scopes=SCOPES):
        if not client_id or not client_secret:
            raise AuthError("APS_CLIENT_ID / APS_CLIENT_SECRET are not set -- fill them in in .env")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self._token = None
        self._expires_at = 0

    def get_token(self):
        if self._token is None or time.time() >= self._expires_at:
            self._refresh()
        return self._token

    def _refresh(self):
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(
            TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Authorization": f"Basic {basic}",
            },
            data={"grant_type": "client_credentials", "scope": self.scopes},
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"Token request failed ({resp.status_code}): {resp.text[:500]}")
        data = resp.json()
        self._token = data["access_token"]
        # refresh 60s early to avoid edge-of-expiry 401s mid-crawl
        self._expires_at = time.time() + data.get("expires_in", 1800) - 60
