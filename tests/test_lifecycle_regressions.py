from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database.models import Base, Reseller, ResellerStatus, ResellerUserCache
from app.rebecca.models import Admin, User
from app.scheduler.jobs import LifecycleRunner
from tests.fakes import FakeRebecca


class Notices:
    def __init__(self):
        self.messages = []
    async def send(self, chat_id, text):
        self.messages.append((chat_id, text)); return True
    async def once(self, *args, **kwargs):
        return False
    async def owners(self, text):
        self.messages.append(("owners", text))


async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_repeated_expired_sync_does_not_disable_or_notify_again():
    sessions = await database()
    now = datetime.now(UTC)
    admin = Admin(username="r", role="reseller", status="disabled", expire=now-timedelta(days=1), data_limit=100, used_traffic=100)
    fake = FakeRebecca({"r": admin})
    notices = Notices()
    runner = LifecycleRunner(sessions, fake, notices, Settings(dry_run=False, allow_disable_actions=True))
    async with sessions() as session, session.begin():
        session.add(Reseller(telegram_id=1, rebecca_admin_username="r", status=ResellerStatus.EXPIRED))
    async with sessions() as session, session.begin():
        await runner._sync_resellers(session)
        await runner._sync_resellers(session)
    assert not [item for item in fake.mutations if item[0] == "disable_admin"]
    assert notices.messages == []


@pytest.mark.asyncio
async def test_renewed_own_expiry_user_is_enabled_and_warning_reset():
    sessions = await database()
    now = datetime.now(UTC)
    admin = Admin(username="r", role="reseller", status="active", expire=now+timedelta(days=5), data_limit=1000, used_traffic=1)
    user = User(username="u", admin_username="r", status="disabled", expire=now+timedelta(days=1), data_limit=100, used_traffic=1)
    fake = FakeRebecca({"r": admin}, {"u": user})
    notices = Notices()
    runner = LifecycleRunner(sessions, fake, notices, Settings(dry_run=False, allow_disable_actions=True))
    async with sessions() as session, session.begin():
        reseller=Reseller(telegram_id=1,rebecca_admin_username="r",status=ResellerStatus.ACTIVE); session.add(reseller); await session.flush()
        session.add(ResellerUserCache(reseller_id=reseller.id,username="u",rebecca_admin_username="r",status="disabled",local_status="EXPIRED",expired_detected_at=now-timedelta(hours=1),delete_after=now+timedelta(hours=71),warning_sent_at=now,disabled_by_own_expiry=True))
    async with sessions() as session, session.begin():
        await runner._sync_users(session)
        cached=await session.scalar(select(ResellerUserCache).where(ResellerUserCache.username=="u"))
        assert cached.delete_after is None and cached.warning_sent_at is None
        assert not cached.disabled_by_own_expiry and cached.local_status=="ACTIVE"
    assert any(item[0]=="update_user" for item in fake.mutations)
