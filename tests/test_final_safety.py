from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import AuditLog, Base, OrderStatus, ResellerStatus, TrialRecord
from app.payments.service import AmbiguousEntitlementState, apply_order, entitlement_snapshot
from app.rebecca.exceptions import CapabilityMissing
from app.rebecca.models import Admin, normalize_status
from app.reseller.service import provision
from app.reseller.trial import failure_status, record_dry_run_request
from tests.fakes import FakeRebecca

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_dry_run_trial_records_decision_without_consuming_or_mutating():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fake = FakeRebecca()
    async with sessions() as session, session.begin():
        await record_dry_run_request(session, 123)
    async with sessions() as session:
        trial = await session.scalar(select(TrialRecord).where(TrialRecord.telegram_id == 123))
        event = await session.scalar(select(AuditLog).where(AuditLog.action == "WOULD_PROVISION_TRIAL"))
    assert trial is None
    assert event and event.target_identifier == "123"
    assert fake.mutations == []


def test_status_normalization_equivalents_and_unknown():
    assert normalize_status("active") == normalize_status("enabled") == "active"
    assert normalize_status("disabled") == normalize_status("inactive") == "disabled"
    assert normalize_status("maintenance") == "maintenance"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_status", ["active", "enabled"])
async def test_active_equivalent_live_status_matches_immutable_target(live_status):
    target_admin = Admin(username="r", role="reseller", status=live_status, expire=NOW+timedelta(days=5), data_limit=500, used_traffic=10, services=[2], users_limit=4)
    order = SimpleNamespace(id=201,status=OrderStatus.APPLYING,before_snapshot=entitlement_snapshot(Admin(username="r",role="reseller",status="active",expire=NOW,data_limit=100,services=[1],users_limit=1)),after_snapshot={"target": {"data_limit":500,"expire":(NOW+timedelta(days=5)).isoformat(),"services":[2],"users_limit":4,"role":"reseller","status":"active"}},applied_at=None,apply_error=None)
    reseller=SimpleNamespace(status=ResellerStatus.ACTIVE,rebecca_admin_username="r")
    assert await apply_order(order,reseller,SimpleNamespace(),FakeRebecca({"r":target_admin}),NOW)
    assert order.status == OrderStatus.APPLIED


@pytest.mark.asyncio
async def test_inactive_matches_disabled_before_but_unknown_fails_closed():
    before_admin=Admin(username="r",role="reseller",status="disabled",expire=NOW,data_limit=100,services=[1],users_limit=1)
    live=before_admin.model_copy(update={"status":"inactive"})
    target={"data_limit":200,"expire":(NOW+timedelta(days=1)).isoformat(),"services":[2],"users_limit":2,"role":"reseller","status":"active"}
    order=SimpleNamespace(id=202,status=OrderStatus.APPLYING,before_snapshot=entitlement_snapshot(before_admin),after_snapshot={"target":target},applied_at=None,apply_error=None)
    reseller=SimpleNamespace(status=ResellerStatus.EXPIRED,rebecca_admin_username="r")
    class EnablingFake(FakeRebecca):
        async def enable_admin(self, username):
            await super().enable_admin(username); self.admins[username].status="enabled"
    fake=EnablingFake({"r":live})
    await apply_order(order,reseller,SimpleNamespace(),fake,NOW)
    assert order.status == OrderStatus.APPLIED

    unknown=live.model_copy(update={"status":"maintenance"})
    order2=SimpleNamespace(id=203,status=OrderStatus.APPLYING,before_snapshot=entitlement_snapshot(before_admin),after_snapshot={"target":target},applied_at=None,apply_error=None)
    with pytest.raises(AmbiguousEntitlementState):
        await apply_order(order2,SimpleNamespace(status=ResellerStatus.ACTIVE,rebecca_admin_username="r"),SimpleNamespace(),FakeRebecca({"r":unknown}),NOW)


@pytest.mark.asyncio
async def test_capability_missing_trial_stays_recoverable_with_same_username():
    expire=NOW+timedelta(days=1)
    existing=Admin(username="reserved",role="reseller",status="active",expire=expire,data_limit=10,services=[1])
    class CapabilityFake(FakeRebecca):
        def __init__(self): super().__init__({"reserved":existing}); self.missing=True
        async def update_admin(self,username,payload):
            if self.missing:
                self.missing=False
                raise CapabilityMissing("admin_update")
            return await super().update_admin(username,payload)
    fake=CapabilityFake()
    with pytest.raises(CapabilityMissing) as error:
        await provision(fake,username="reserved",password="one",expire=expire,data_limit=10,services=[1])
    assert failure_status(error.value) == "PROVISIONING"
    result=await provision(fake,username="reserved",password="two",expire=expire,data_limit=10,services=[1])
    assert result.username == "reserved"
    assert not any(item[0] == "create" for item in fake.mutations)


@pytest.mark.parametrize("role", ["sudo", "full_access"])
def test_unsafe_roles_are_terminal_trial_failures(role):
    from app.rebecca.exceptions import VerificationError
    assert failure_status(VerificationError(role)) == "FAILED"
