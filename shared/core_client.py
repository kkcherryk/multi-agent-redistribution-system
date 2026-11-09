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

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
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
        return resp

    def _request(self, method: str, path: str, **kwargs):
        resp = self._send(method, path, **kwargs)
        return None if resp.status_code == 204 else resp.json()

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

    # ---- Phase 2: onboarding, review, availability
    def get_onboarding(self, account_id: str) -> dict:
        return self._request("GET", f"/internal/accounts/{account_id}/onboarding")

    def put_profile(self, account_id: str, profile: dict) -> dict:
        return self._request("PUT", f"/internal/accounts/{account_id}/profile", json=profile)

    def upload_document(self, account_id: str, kind: str, filename: str, data: bytes, content_type: str) -> dict:
        return self._request("POST", f"/internal/accounts/{account_id}/documents", data={"kind": kind},
                             files={"file": (filename, data, content_type or "application/octet-stream")})

    def delete_document(self, account_id: str, doc_id: str) -> None:
        self._request("DELETE", f"/internal/accounts/{account_id}/documents/{doc_id}")

    def document_content(self, doc_id: str, account_id: str | None = None) -> tuple[bytes, str]:
        params = {"account_id": account_id} if account_id else None
        resp = self._send("GET", f"/internal/documents/{doc_id}/content", params=params)
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    def submit_application(self, account_id: str) -> dict:
        return self._request("POST", f"/internal/accounts/{account_id}/submit")

    def set_availability(self, account_id: str, available: bool) -> dict:
        return self._request("PUT", f"/internal/accounts/{account_id}/availability", json={"available": available})

    def review_queue(self, status: str = "under_review") -> list[dict]:
        return self._request("GET", "/internal/review-queue", params={"status": status})

    def review(self, account_id: str, decision: str, note: str | None, reviewer_id: str) -> dict:
        return self._request("POST", f"/internal/accounts/{account_id}/review",
                             json={"decision": decision, "note": note, "reviewer_id": reviewer_id})

    def ngo_directory(self) -> list[dict]:
        return self._request("GET", "/internal/ngo-directory")

    # ---- Phase 3: food listings and matching
    def create_listing(self, restaurant_id: str, data_json: str, filename: str, photo: bytes, content_type: str) -> dict:
        return self._request("POST", "/internal/listings", data={"restaurant_id": restaurant_id, "data": data_json},
                             files={"photo": (filename, photo, content_type or "application/octet-stream")})

    def restaurant_listings(self, restaurant_id: str) -> list[dict]:
        return self._request("GET", f"/internal/restaurants/{restaurant_id}/listings")

    def cancel_listing(self, listing_id: str, restaurant_id: str) -> dict:
        return self._request("POST", f"/internal/listings/{listing_id}/cancel", params={"restaurant_id": restaurant_id})

    def agent_feed(self, agent_id: str) -> dict:
        return self._request("GET", f"/internal/agents/{agent_id}/feed")

    def accept_listing(self, listing_id: str, agent_id: str) -> dict:
        return self._request("POST", f"/internal/listings/{listing_id}/accept", params={"agent_id": agent_id})

    def agent_pickups(self, agent_id: str) -> list[dict]:
        return self._request("GET", f"/internal/agents/{agent_id}/pickups")

    def listing_photo(self, listing_id: str, viewer_id: str | None = None) -> tuple[bytes, str]:
        params = {"viewer_id": viewer_id} if viewer_id else None
        resp = self._send("GET", f"/internal/listings/{listing_id}/photo", params=params)
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    def all_listings(self, status: str | None = None) -> list[dict]:
        return self._request("GET", "/internal/listings", params={"status": status} if status else None)
