from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, Order, OrderStatus, Payment, Product, Reseller
from app.payments.plisio import signature
from app.payments.webhook import router


@pytest.mark.asyncio
async def test_completed_callback_is_idempotent_after_paid():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        product = Product(name="Lite", slug="lite", service_type="LITE", service_ids=[1], duration_days=30, traffic_gb=10, price_toman=100, users_limit=2)
        reseller = Reseller(telegram_id=1)
        session.add_all([product, reseller]); await session.flush()
        order = Order(order_number="ORDER-1", reseller_id=reseller.id, product_id=product.id, amount=Decimal("12.50"), currency="USD", status=OrderStatus.WAITING_PAYMENT, payment_method="PLISIO")
        session.add(order); await session.flush()
        session.add(Payment(order_id=order.id, method="PLISIO", status="new", plisio_txn_id="tx-1"))
    app = FastAPI(); app.include_router(router(sessions, "secret", "USD"))
    payload = {"txn_id":"tx-1","status":"completed","order_number":"ORDER-1","source_currency":"USD","source_amount":"12.50"}
    payload["verify_hash"] = signature(payload, "secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/payments/plisio/callback", json=payload)
        second = await client.post("/payments/plisio/callback", json=payload)
    assert first.status_code == 200 and first.json() == {"ok": True, "paid": True}
    assert second.status_code == 200
    assert second.json() == {"ok": True, "paid": True, "idempotent": True}
