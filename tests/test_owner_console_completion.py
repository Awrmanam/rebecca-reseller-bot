import asyncio
from decimal import Decimal

import pytest
from datetime import UTC, datetime

from app.bot.handlers.owner.console import (
    DECISION_RESULTS, ORDER_FILTERS, PAGE_SIZE, PRODUCT_FIELDS, _page, _parse_product,
)
from app.database.models import OrderStatus
from app.database.models import Order, Product, Reseller
from app.notifications.service import NotificationService
from app.rebecca.models import Admin
from app.scheduler.jobs import LifecycleRunner
from tests.fakes import FakeRebecca
from tests.test_owner_settings import sessions
from app.payments.reconciliation import ImmediateReconciliation, schedule_reconciliation
from app.bot.handlers.owner.console import router as owner_router
from app.config import Settings


def test_product_ui_supports_every_editable_field_and_validates_values():
    assert set(PRODUCT_FIELDS) == {
        "name", "slug", "price_toman", "traffic_gb", "duration_days",
        "service_ids", "users_limit",
    }
    assert _parse_product("price_toman", "125000") == Decimal("125000")
    assert _parse_product("service_ids", "3,1,3") == [1, 3]
    assert _parse_product("users_limit", "نامحدود") is None
    for field, value in (("price_toman", "0"), ("traffic_gb", "-1"), ("slug", "bad slug")):
        with pytest.raises(ValueError):
            _parse_product(field, value)


def test_payment_console_filters_cover_every_required_state():
    covered = {status for values in ORDER_FILTERS.values() for status in values}
    assert {
        OrderStatus.PENDING, OrderStatus.WAITING_RECEIPT, OrderStatus.WAITING_PAYMENT,
        OrderStatus.PAID, OrderStatus.APPLYING, OrderStatus.APPLIED,
        OrderStatus.FAILED, OrderStatus.REJECTED, OrderStatus.EXPIRED,
    } <= covered


def test_pagination_clamps_invalid_and_negative_pages():
    assert PAGE_SIZE > 0
    assert _page("3") == 3
    assert _page("-10") == 0
    assert _page("not-a-page") == 0


def test_decision_filters_include_dry_run_errors_and_safety_blocks():
    assert {"DRY_RUN", "ERROR", "SKIPPED", "BLOCKED"} <= set(DECISION_RESULTS)


def test_owner_router_registers_complete_functional_handler_sets():
    configured = owner_router(Settings(owner_ids=(1,)), object())
    callback_names = {handler.callback.__name__ for handler in configured.callback_query.handlers}
    message_names = {handler.callback.__name__ for handler in configured.message.handlers}
    assert {
        "product_add", "product_edit", "product_toggle", "product_delete",
        "channel_add", "channel_edit", "channel_toggle", "channel_remove",
        "reseller_search", "reseller_refresh", "reseller_hold",
        "reseller_state_change", "order_approve", "order_reject", "order_retry",
        "audit_pages", "audit_view",
    } <= callback_names
    assert {
        "product_create_value", "product_edit_value", "channel_create",
        "channel_edit_value", "reseller_search_value",
    } <= message_names


@pytest.mark.asyncio
async def test_immediate_trigger_is_background_and_non_blocking():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowRunner:
        async def reconcile_paid_orders(self):
            started.set()
            await release.wait()

    immediate = ImmediateReconciliation()
    immediate.runner = SlowRunner()
    task = immediate.trigger()
    assert isinstance(task, asyncio.Task)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not task.done()
    release.set()
    await task


@pytest.mark.asyncio
async def test_background_trigger_surfaces_sanitized_failure(caplog):
    class FailedRunner:
        async def reconcile_paid_orders(self):
            raise RuntimeError("Authorization: Bearer secret-token")

    task = schedule_reconciliation(FailedRunner())
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert "secret-token" not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest.mark.asyncio
async def test_renewal_never_resets_or_sends_password():
    factory = await sessions()
    expire = datetime.fromtimestamp(1_900_000_000, UTC)
    async with factory() as session, session.begin():
        product = Product(name="Renew", slug="renew", service_type="CUSTOM", service_ids=[1], duration_days=30, traffic_gb=5, price_toman=Decimal("10"), users_limit=2)
        reseller = Reseller(telegram_id=44, telegram_username="customer", rebecca_admin_username="customer_1234", status="ACTIVE")
        session.add_all([product, reseller]); await session.flush()
        session.add(Order(order_number="RENEW-1", reseller_id=reseller.id, product_id=product.id, amount=Decimal("10"), currency="IRT", status=OrderStatus.PAID, payment_method="CARD", paid_at=datetime.now(UTC)))
    fake = FakeRebecca({"customer_1234": Admin(username="customer_1234", role="reseller", status="active", expire=expire, data_limit=100, used_traffic=10, services=[1], users_limit=2)})
    runner = LifecycleRunner(factory, fake, NotificationService(None, ()), Settings(dry_run=False))
    await runner.reconcile_paid_orders()
    updates = [mutation for mutation in fake.mutations if mutation[0] == "update"]
    assert len(updates) == 1
    assert "password" not in updates[0][2]
