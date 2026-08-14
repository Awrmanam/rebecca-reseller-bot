from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.database.models import OrderStatus, ResellerStatus
from app.payments.service import AmbiguousEntitlementState, apply_order
from app.rebecca.models import Admin
from tests.fakes import FakeRebecca

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def objects():
    admin = Admin(username="r", role="reseller", status="active", expire=NOW+timedelta(days=1), data_limit=100, used_traffic=20, services=[1], users_limit=2)
    order = SimpleNamespace(id=100, status=OrderStatus.PAID, before_snapshot=None, after_snapshot=None, applied_at=None, apply_error=None)
    reseller = SimpleNamespace(status=ResellerStatus.ACTIVE, rebecca_admin_username="r")
    product = SimpleNamespace(traffic_gb=1, duration_days=2, service_ids=[7, 8], users_limit=20)
    return admin, order, reseller, product


@pytest.mark.asyncio
async def test_timeout_after_full_update_marks_applied_without_second_update():
    admin, order, reseller, product = objects()
    class UpdatedThenTimeout(FakeRebecca):
        async def update_admin(self, username, payload):
            result = await super().update_admin(username, payload)
            raise TimeoutError("response lost")
    fake = UpdatedThenTimeout({"r": admin})
    with pytest.raises(TimeoutError):
        await apply_order(order, reseller, product, fake, NOW)
    assert order.status == OrderStatus.APPLYING
    assert len([x for x in fake.mutations if x[0] == "update"]) == 1
    assert await apply_order(order, reseller, product, fake, NOW)
    assert order.status == OrderStatus.APPLIED
    assert len([x for x in fake.mutations if x[0] == "update"]) == 1


@pytest.mark.asyncio
async def test_no_update_retries_same_immutable_target():
    admin, order, reseller, product = objects()
    fake = FakeRebecca({"r": admin}); fake.fail_update = True
    with pytest.raises(RuntimeError):
        await apply_order(order, reseller, product, fake, NOW)
    target = dict(order.after_snapshot["target"])
    before = dict(order.before_snapshot)
    fake.fail_update = False
    await apply_order(order, reseller, product, fake, NOW + timedelta(days=30))
    assert order.status == OrderStatus.APPLIED
    assert target == order.after_snapshot["target"]
    assert before == order.before_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", ["data", "expire", "services"])
async def test_partial_update_is_ambiguous_and_never_reapplied(partial):
    admin, order, reseller, product = objects()
    fake = FakeRebecca({"r": admin}); fake.fail_update = True
    with pytest.raises(RuntimeError):
        await apply_order(order, reseller, product, fake, NOW)
    target = order.after_snapshot["target"]
    if partial == "data": admin.data_limit = target["data_limit"]
    elif partial == "expire": admin.expire = datetime.fromisoformat(target["expire"])
    else: admin.services = target["services"]
    fake.fail_update = False
    mutations_before = len(fake.mutations)
    with pytest.raises(AmbiguousEntitlementState):
        await apply_order(order, reseller, product, fake, NOW + timedelta(days=10))
    assert len(fake.mutations) == mutations_before
    assert order.status == OrderStatus.APPLYING
    assert order.apply_error == "live Rebecca entitlement is partial or ambiguous"
