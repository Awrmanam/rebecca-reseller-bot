from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx


def _sort_recursive(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_recursive(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_recursive(item) for item in value]
    return value


def callback_message(payload: dict[str, Any]) -> str:
    """Plisio JSON callback algorithm: remove hash, recursively sort, compact JSON."""
    unsigned = {key: value for key, value in payload.items() if key != "verify_hash"}
    return json.dumps(
        _sort_recursive(unsigned),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def signature(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode(), callback_message(payload).encode(), hashlib.sha1
    ).hexdigest()


def verify(payload: dict[str, Any], secret: str) -> bool:
    supplied = payload.get("verify_hash")
    return (
        isinstance(supplied, str)
        and bool(supplied)
        and bool(secret)
        and hmac.compare_digest(supplied, signature(payload, secret))
    )


def exact_amount(value: Any) -> Decimal:
    return Decimal(str(value))


class PlisioClient:
    API_URL = "https://api.plisio.net/api/v1"

    def __init__(self, secret: str, client: httpx.AsyncClient | None = None):
        self._secret = secret
        self.http = client or httpx.AsyncClient(base_url=self.API_URL, timeout=20)

    async def create_invoice(
        self,
        *,
        order_number: str,
        source_currency: str,
        source_amount: Decimal,
        callback_url: str,
        description: str,
    ) -> dict[str, Any]:
        response = await self.http.get(
            "/invoices/new",
            params={
                "api_key": self._secret,
                "order_number": order_number,
                "source_currency": source_currency,
                "source_amount": format(source_amount, "f"),
                "callback_url": callback_url,
                "email": "",
                "order_name": description,
                "json": "true",
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success" or not isinstance(body.get("data"), dict):
            raise RuntimeError("Plisio invoice creation failed")
        data = body["data"]
        if not data.get("txn_id") or not data.get("invoice_url"):
            raise RuntimeError("Plisio response missing invoice identifiers")
        return data

    async def transaction(self, txn_id: str) -> dict[str, Any]:
        response = await self.http.get(
            f"/operations/{txn_id}", params={"api_key": self._secret}
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success" or not isinstance(body.get("data"), dict):
            raise RuntimeError("Plisio transaction lookup failed")
        return body["data"]
