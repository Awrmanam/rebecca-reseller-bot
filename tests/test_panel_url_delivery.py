from decimal import Decimal

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select

from app.bot.credentials import credential_keyboard
from app.config import Settings
from app.database.models import Order, OrderStatus, Product, Reseller, Setting
from app.rebecca.models import Admin
from app.scheduler.jobs import LifecycleRunner
from tests.fakes import FakeRebecca
from tests.test_owner_settings import sessions


class ProvisioningRebecca(FakeRebecca):
    async def create_reseller_admin(self, payload):
        self.mutations.append(("create", payload["username"]))
        admin = Admin(
            username=payload["username"], role="reseller", status="active",
            expire=payload["expire"], data_limit=payload["data_limit"],
            services=payload["services"], users_limit=payload["users_limit"],
        )
        self.admins[admin.username] = admin
        return admin


class CredentialCapture:
    def __init__(self): self.deliveries = []
    async def send_credentials(self, chat_id, text, username, password, panel_url):
        self.deliveries.append((chat_id, text, panel_url, credential_keyboard(username, password, panel_url)))
        return True
    async def send(self, chat_id, text): return True
    async def owners(self, text): pass


async def add_order(factory, product_id, telegram_id, number):
    async with factory() as session, session.begin():
        reseller = Reseller(telegram_id=telegram_id, status="PROVISIONING")
        session.add(reseller); await session.flush()
        session.add(Order(
            order_number=number, reseller_id=reseller.id, product_id=product_id,
            amount=Decimal("1490000"), currency="IRT", status=OrderStatus.PAID,
            payment_method="CARD",
        ))


def url_from_keyboard(markup: InlineKeyboardMarkup):
    return next(button.url for row in markup.inline_keyboard for button in row if button.url)


@pytest.mark.asyncio
async def test_runtime_panel_url_reaches_lifecycle_text_and_button_without_restart():
    factory = await sessions()
    old_url = "https://example.com/panel"
    new_url = "https://new.example.com/panel"
    async with factory() as session, session.begin():
        product = Product(name="Lite", slug="lite-panel", service_type="LITE", service_ids=[1], duration_days=30, traffic_gb=1000, price_toman=Decimal("1490000"), users_limit=100)
        session.add(product); await session.flush(); product_id = product.id
        session.add(Setting(key="customer_panel_url", value=old_url))
    await add_order(factory, product_id, 701, "R-PANEL-1")
    fake = ProvisioningRebecca(); capture = CredentialCapture()
    runner = LifecycleRunner(factory, fake, capture, Settings(dry_run=True))
    async with factory() as session, session.begin(): await runner.runtime.set_operations_mode(session, "live")

    await runner.reconcile_paid_orders()
    assert any(item[0] == "create" for item in fake.mutations)
    assert old_url in capture.deliveries[0][1]
    assert capture.deliveries[0][2] == old_url
    assert url_from_keyboard(capture.deliveries[0][3]) == old_url

    async with factory() as session, session.begin():
        row = await session.get(Setting, "customer_panel_url"); row.value = f"  {new_url}  "
    await add_order(factory, product_id, 702, "R-PANEL-2")
    await runner.reconcile_paid_orders()
    assert new_url in capture.deliveries[1][1]
    assert capture.deliveries[1][2] == new_url
    assert url_from_keyboard(capture.deliveries[1][3]) == new_url
    async with factory() as session:
        assert len((await session.scalars(select(Order).where(Order.status == OrderStatus.APPLIED))).all()) == 2
    await factory.kw["bind"].dispose()
