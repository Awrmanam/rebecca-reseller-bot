from types import SimpleNamespace
import pytest
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.bot.middlewares.membership import is_member
from app.database.models import Base
from app.reseller.trial import reserve_trial
from app.reseller.trial import failure_status
from app.rebecca.exceptions import RebeccaUnavailable
class Bot:
 def __init__(self,status): self.status=status
 async def get_chat_member(self,c,u): return SimpleNamespace(status=self.status)
@pytest.mark.asyncio
async def test_membership_gate():
 assert await is_member(Bot("member"),1,["@a","@b"])
 assert not await is_member(Bot("left"),1,["@a"])
@pytest.mark.asyncio
async def test_trial_only_once_database_enforced():
 e=create_async_engine("sqlite+aiosqlite:///:memory:")
 async with e.begin() as c: await c.run_sync(Base.metadata.create_all)
 sf=async_sessionmaker(e,expire_on_commit=False)
 async with sf() as s:
  record = await reserve_trial(s,123,"reserved-name"); assert record.admin_username == "reserved-name"; await s.commit()
 async with sf() as s: assert await reserve_trial(s,123,"different-name") is None


def test_transport_failure_keeps_trial_recoverable():
 assert failure_status(RebeccaUnavailable("temporary")) == "PROVISIONING"
