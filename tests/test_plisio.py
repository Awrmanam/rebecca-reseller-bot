import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.payments.plisio import PlisioClient, normalize_operation, signature, verify

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_invalid_missing_and_altered_signature():
    payload = json.loads((FIXTURES / "plisio_completed.json").read_text())
    assert verify(payload, "fixture-secret")
    assert not verify(payload, "wrong")
    assert not verify({"status": "completed"}, "fixture-secret")
    altered = dict(payload, source_amount="12.51")
    assert not verify(altered, "fixture-secret")


def test_node_semantics_do_not_recursively_sort_nested_objects():
    first = {"a": 1, "params": {"z": 2, "a": 1}}
    second = {"params": {"a": 1, "z": 2}, "a": 1}
    assert signature(first, "secret") != signature(second, "secret")


def test_transaction_details_nested_params_shape():
    operation = normalize_operation(
        json.loads((FIXTURES / "plisio_operation.json").read_text())
    )
    assert operation == {
        "id": "ps_test_123",
        "status": "completed",
        "order_number": "ORDER-42",
        "source_amount": Decimal("12.50"),
        "source_currency": "USD",
    }


@pytest.mark.asyncio
async def test_invoice_creation_preserves_decimal_and_ids():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"txn_id": "tx1", "invoice_url": "https://pay.test/i"},
            },
        )

    client = PlisioClient(
        "hidden",
        httpx.AsyncClient(
            base_url="https://api.test", transport=httpx.MockTransport(handler)
        ),
    )
    invoice = await client.create_invoice(
        order_number="O1",
        source_currency="USD",
        source_amount=Decimal("12.50"),
        callback_url="https://bot.test/cb",
        description="Plan",
    )
    assert invoice["txn_id"] == "tx1"
    assert seen["order_number"] == "O1" and seen["source_amount"] == "12.50"
    assert seen["source_currency"] == "USD" and seen["api_key"] == "hidden"
