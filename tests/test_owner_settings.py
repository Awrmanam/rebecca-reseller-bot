from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.settings import (approval_messages, mask_card_number,
                              normalize_card_number, normalize_support_username,
                              owner_access, parse_service_ids)
from app.config import Settings
from app.database.models import Base, Order, OrderStatus, Product, Reseller
from app.rebecca.models import Admin
from app.database.settings import RuntimeSettingsService
from app.notifications.service import NotificationService
from app.scheduler.jobs import LifecycleRunner
from tests.fakes import FakeRebecca


def test_card_normalization_and_masking():
    assert normalize_card_number("6037-9912 3456-7890") == "6037991234567890"
    assert mask_card_number("6037991234567890") == "6037-****-****-7890"
    with pytest.raises(ValueError):
        normalize_card_number("6037-secret")


def test_settings_access_is_owner_only():
    assert owner_access(10, (10, 20))
    assert not owner_access(99, (10, 20))


def test_support_and_trial_service_normalization():
    assert normalize_support_username(" @Support_User ") == "@Support_User"
    assert parse_service_ids("3, 1,3, 2") == [1, 2, 3]
    with pytest.raises(ValueError):
        parse_service_ids("1,not-a-number")


def test_approval_messages_are_explicit_for_each_mode():
    dry_customer, dry_owner = approval_messages("R1", True)
    live_customer, live_owner = approval_messages("R1", False)
    assert "حالت آزمایشی" in dry_customer and "انتظار حالت زنده" in dry_owner
    assert "صف پردازش" in live_customer and live_owner is None


async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_operations_mode_defaults_to_env_and_live_requires_explicit_set():
    factory = await sessions()
    runtime = RuntimeSettingsService(Settings(dry_run=True))
    async with factory() as session, session.begin():
        assert await runtime.operations_mode(session) == "dry_run"
        await runtime.set_operations_mode(session, "live")
    async with factory() as session, session.begin():
        assert await runtime.operations_mode(session) == "live"
        await runtime.set_operations_mode(session, "dry_run")
    async with factory() as session:
        assert await runtime.operations_mode(session) == "dry_run"
    assert "allow_delete_actions" not in runtime.EDITABLE
    assert "destructive_actions" not in runtime.EDITABLE


@pytest.mark.asyncio
async def test_manual_reconciliation_in_dry_run_never_mutates_rebecca():
    factory = await sessions()
    async with factory() as session, session.begin():
        product = Product(name="Lite",slug="lite",service_type="LITE",service_ids=[1],duration_days=30,traffic_gb=10,price_toman=Decimal("100"),users_limit=2)
        reseller = Reseller(telegram_id=10,rebecca_admin_username="seller",status="ACTIVE")
        session.add_all([product,reseller]); await session.flush()
        session.add(Order(order_number="R-DRY",reseller_id=reseller.id,product_id=product.id,amount=Decimal("100"),currency="IRT",status=OrderStatus.PAID,payment_method="CARD"))
    fake = FakeRebecca()
    runner = LifecycleRunner(factory,fake,NotificationService(None,()),Settings(dry_run=True))
    await runner.reconcile_paid_orders()
    assert fake.mutations == []


@pytest.mark.asyncio
async def test_live_reconciliation_verifies_before_credentials_and_recovers_delivery():
    factory = await sessions()
    async with factory() as session, session.begin():
        product = Product(name="Pro",slug="pro",service_type="PROMAX",service_ids=[7],duration_days=30,traffic_gb=10,price_toman=Decimal("100"),users_limit=5)
        reseller = Reseller(telegram_id=20,status="PROVISIONING")
        session.add_all([product,reseller]); await session.flush()
        session.add(Order(order_number="R-LIVE",reseller_id=reseller.id,product_id=product.id,amount=Decimal("100"),currency="IRT",status=OrderStatus.PAID,payment_method="CARD"))

    class ProvisioningFake(FakeRebecca):
        async def create_reseller_admin(self, payload):
            self.mutations.append(("create", payload["username"]))
            self.admins[payload["username"]] = Admin(username=payload["username"],role="reseller",status="active",expire=payload["expire"],data_limit=payload["data_limit"],services=payload["services"],users_limit=payload["users_limit"])
            return self.admins[payload["username"]]

    class Delivery:
        def __init__(self): self.attempts=0; self.messages=[]
        async def send(self, chat_id, text):
            self.attempts += 1; self.messages.append(text); return self.attempts > 1
        async def owners(self, text): pass

    fake=ProvisioningFake(); delivery=Delivery()
    runner=LifecycleRunner(factory,fake,delivery,Settings(dry_run=True))
    async with factory() as session, session.begin():
        await runner.runtime.set_operations_mode(session,"live")
    await runner.reconcile_paid_orders()
    assert len([x for x in fake.mutations if x[0]=="create"]) == 1
    await runner.reconcile_paid_orders()
    assert len([x for x in fake.mutations if x[0]=="create"]) == 1
    assert any(x[0]=="update" for x in fake.mutations)
    async with factory() as session:
        order=await session.scalar(select(Order).where(Order.order_number=="R-LIVE"))
        reseller=await session.get(Reseller,order.reseller_id)
        assert order.status==OrderStatus.APPLIED and reseller.status=="ACTIVE"
    assert delivery.attempts == 2
