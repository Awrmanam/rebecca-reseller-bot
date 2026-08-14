from app.payments.plisio import signature,verify

def test_valid_invalid_and_missing_signature():
 p={"order_number":"A1","status":"completed","source_amount":"12.00","txn_id":"x"}; p["verify_hash"]=signature(p,"secret")
 assert verify(p,"secret"); assert not verify(p,"wrong"); assert not verify({"status":"completed"},"secret")
def test_signature_canonical_nested():
 a={"b":{"z":2,"a":1},"a":[2,1]}; b={"a":[2,1],"b":{"a":1,"z":2}}
 assert signature(a,"s")==signature(b,"s")

import json
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from app.payments.plisio import PlisioClient

def test_official_json_style_callback_fixture():
 payload=json.loads((Path(__file__).parent/"fixtures/plisio_completed.json").read_text())
 assert verify(payload,"fixture-secret")

@pytest.mark.asyncio
async def test_invoice_creation_preserves_decimal_and_ids():
 seen={}
 def handler(request):
  seen.update(dict(request.url.params))
  return httpx.Response(200,json={"status":"success","data":{"txn_id":"tx1","invoice_url":"https://pay.test/i"}})
 client=PlisioClient("hidden",httpx.AsyncClient(base_url="https://api.test",transport=httpx.MockTransport(handler)))
 invoice=await client.create_invoice(order_number="O1",source_currency="USD",source_amount=Decimal("12.50"),callback_url="https://bot.test/cb",description="Plan")
 assert invoice["txn_id"]=="tx1"
 assert seen["order_number"]=="O1" and seen["source_amount"]=="12.50"
 assert seen["source_currency"]=="USD" and seen["api_key"]=="hidden"
