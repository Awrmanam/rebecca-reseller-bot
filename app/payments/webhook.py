from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database.models import Order, OrderStatus, Payment
from .plisio import exact_amount, verify

def router(factory: async_sessionmaker, secret: str) -> APIRouter:
    r=APIRouter()
    @r.post("/payments/plisio/callback")
    async def callback(request: Request):
        payload=await request.json()
        if not verify(payload,secret): raise HTTPException(400,"invalid signature")
        order_number=str(payload.get("order_number", ""))
        async with factory() as session, session.begin():
            order=(await session.execute(select(Order).where(Order.order_number==order_number))).scalar_one_or_none()
            if order is None: raise HTTPException(404,"unknown order")
            if order.status==OrderStatus.APPLIED: raise HTTPException(409,"already applied")
            payment=(await session.execute(select(Payment).where(Payment.order_id==order.id))).scalar_one_or_none()
            if payment and payment.plisio_txn_id and payment.plisio_txn_id != payload.get("txn_id"): raise HTTPException(400,"transaction mismatch")
            if exact_amount(payload.get("source_amount", "-1")) != Decimal(order.amount): raise HTTPException(400,"amount mismatch")
            if payload.get("status") != "completed": return {"ok":True,"paid":False}
            if order.status not in {OrderStatus.WAITING_PAYMENT,OrderStatus.PAID}: raise HTTPException(409,"invalid order state")
            order.status=OrderStatus.PAID; order.paid_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            if payment: payment.plisio_txn_id=payload.get("txn_id"); payment.status="completed"
        return {"ok":True,"paid":True}
    return r
