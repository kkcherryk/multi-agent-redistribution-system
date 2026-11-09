"""HTTP client a portal backend uses to talk to the core data service."""
from __future__ import annotations

import httpx


class CoreError(Exception):
    def __init__(self, status_code: int, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CoreUnavailable(Exception):
    pass


class CoreClient:
    def __init__(self, base_url: str, service_key: str, http: httpx.Client | None = None, timeout: float = 5.0):
        # ``http`` lets tests inject a client bound directly to the core ASGI app.
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._headers = {"X-Service-Key": service_key}

    def _request(self, method: str, path: str, **kwargs):
        try:
            resp = self._http.request(method, path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CoreUnavailable(str(exc)) from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CoreError(resp.status_code, detail)
        return resp.json()

    def health(self) -> dict:
        return self._request("GET", "/health")

    def register(self, **fields) -> dict:
        return self._request("POST", "/internal/accounts", json=fields)

    def verify_credentials(self, email: str, password: str) -> dict:
        return self._request("POST", "/internal/auth/verify", json={"email": email, "password": password})

    def get_account(self, account_id: str) -> dict:
        return self._request("GET", f"/internal/accounts/{account_id}")

    def list_accounts(self, role: str | None = None, status: str | None = None) -> list[dict]:
        params = {k: v for k, v in {"role": role, "status": status}.items() if v}
        return self._request("GET", "/internal/accounts", params=params)

    def list_events(self, limit: int = 50) -> list[dict]:
        return self._request("GET", "/internal/events", params={"limit": limit})
