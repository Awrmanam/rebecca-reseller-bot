from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from app.database.models import OrderStatus,ResellerStatus
from app.payments.service import apply_order,approve_card
from app.rebecca.models import Admin,User
from app.reseller.quota import exhausted,renewal_data_limit,renewal_expiry
from app.reseller.service import provision
from app.rebecca.exceptions import VerificationError
from app.users.lifecycle import DeletePolicy,deletion_time,safe_delete
from tests.fakes import FakeRebecca
NOW=datetime(2026,1,1,tzinfo=timezone.utc)
def test_renewals():
 assert renewal_data_limit(100,50,25)==125
 assert renewal_data_limit(100,100,25)==125
 assert renewal_data_limit(100,120,25)==145
 assert renewal_expiry(NOW+timedelta(days=2),NOW,3)==NOW+timedelta(days=5)
 assert renewal_expiry(NOW-timedelta(days=2),NOW,3)==NOW+timedelta(days=3)
def test_exhaustion_either_limit():
 assert exhausted(NOW-timedelta(seconds=1),100,0,NOW)
 assert exhausted(NOW+timedelta(days=1),100,100,NOW)
 assert not exhausted(NOW+timedelta(days=1),100,99,NOW)
 assert not exhausted(None,0,999,NOW)
 assert not exhausted(None,None,999,NOW)
@pytest.mark.asyncio
@pytest.mark.parametrize("role",["sudo","full_access","standard"])
async def test_provision_rejects_unsafe_role(role):
 a=Admin(username="res",role=role,data_limit=10,services=[1]); f=FakeRebecca({"res":a})
 with pytest.raises(VerificationError): await provision(f,username="res",password="secret",expire=NOW,data_limit=10,services=[1])
 assert not any(item[0] == "create" for item in f.mutations)
 if role in {"sudo", "full_access"}: assert ("disable_admin", "res") in f.mutations
@pytest.mark.asyncio
async def test_delete_guards_and_live_ownership():
 due=NOW-timedelta(hours=1); u=User(username="u",admin_username="other",expire=NOW-timedelta(days=1),data_limit=10,used_traffic=10); f=FakeRebecca(users={"u":u})
 assert await safe_delete(f,"u","owner",due,NOW,DeletePolicy(False,True,True))=="OWNERSHIP_MISMATCH"
 assert not f.mutations
@pytest.mark.asyncio
async def test_dry_run_and_switch_prevent_delete():
 u=User(username="u",admin_username="owner",expire=NOW-timedelta(days=1),data_limit=10,used_traffic=10); f=FakeRebecca(users={"u":u}); due=NOW-timedelta(hours=1)
 assert await safe_delete(f,"u","owner",due,NOW,DeletePolicy())=="WOULD_DELETE_USER"
 assert await safe_delete(f,"u","owner",due,NOW,DeletePolicy(False,False,False))=="DELETE_DISABLED"
 assert not f.mutations
@pytest.mark.asyncio
async def test_safe_delete_and_renewal_cancel():
 due=NOW-timedelta(hours=1); active=User(username="u",admin_username="owner",expire=NOW+timedelta(days=1),data_limit=10,used_traffic=1); f=FakeRebecca(users={"u":active}); p=DeletePolicy(False,True,True)
 assert await safe_delete(f,"u","owner",due,NOW,p)=="RENEWED"
 active.expire=NOW-timedelta(1); active.used_traffic=10
 assert await safe_delete(f,"u","owner",due,NOW,p)=="DELETED"
 assert f.mutations==[("delete","u")]
def test_72_hour_schedule(): assert deletion_time(NOW)==NOW+timedelta(hours=72)
@pytest.mark.asyncio
async def test_card_manual_and_double_click():
 o=SimpleNamespace(status=OrderStatus.WAITING_RECEIPT,paid_at=None)
 assert await approve_card(o); assert not await approve_card(o); assert o.status==OrderStatus.PAID
@pytest.mark.asyncio
async def test_apply_exactly_once_and_suspended():
 a=Admin(username="r",role="reseller",expire=NOW+timedelta(days=1),data_limit=100,used_traffic=20); f=FakeRebecca({"r":a}); o=SimpleNamespace(id=1,status=OrderStatus.PAID,before_snapshot=None,after_snapshot=None,applied_at=None,apply_error=None); r=SimpleNamespace(status=ResellerStatus.ACTIVE,rebecca_admin_username="r"); p=SimpleNamespace(traffic_gb=1,duration_days=2)
 assert await apply_order(o,r,p,f,NOW); assert not await apply_order(o,r,p,f,NOW); assert len(f.mutations)==1
 o2=SimpleNamespace(id=2,status=OrderStatus.PAID); r.status=ResellerStatus.SUSPENDED
 assert not await apply_order(o2,r,p,f,NOW)
@pytest.mark.asyncio
async def test_paid_survives_update_failure():
 a=Admin(username="r",role="reseller",expire=NOW,data_limit=100,used_traffic=20); f=FakeRebecca({"r":a}); f.fail_update=True; o=SimpleNamespace(id=9,status=OrderStatus.PAID,before_snapshot=None,after_snapshot=None,applied_at=None,apply_error=None); r=SimpleNamespace(status=ResellerStatus.ACTIVE,rebecca_admin_username="r"); p=SimpleNamespace(traffic_gb=1,duration_days=2)
 with pytest.raises(RuntimeError): await apply_order(o,r,p,f,NOW)
 assert o.status==OrderStatus.APPLYING

@pytest.mark.asyncio
async def test_target_is_persisted_before_rebecca_mutation():
 events=[]
 class OrderedFake(FakeRebecca):
  async def update_admin(self,u,p):
   events.append("mutation")
   return await super().update_admin(u,p)
 a=Admin(username="r",role="reseller",expire=NOW+timedelta(days=1),data_limit=100,used_traffic=20)
 f=OrderedFake({"r":a}); o=SimpleNamespace(id=15,status=OrderStatus.PAID,before_snapshot=None,after_snapshot=None,applied_at=None,apply_error=None)
 r=SimpleNamespace(status=ResellerStatus.ACTIVE,rebecca_admin_username="r"); p=SimpleNamespace(traffic_gb=1,duration_days=2)
 async def persist():
  assert o.status==OrderStatus.APPLYING and o.after_snapshot["target"]
  events.append("persist")
 await apply_order(o,r,p,f,NOW,persist)
 assert events[:2]==["persist","mutation"]

@pytest.mark.asyncio
async def test_plan_switch_applies_services_and_users_limit():
 a=Admin(username="r",role="reseller",expire=NOW+timedelta(days=1),data_limit=100,used_traffic=20,services=[1],users_limit=2)
 f=FakeRebecca({"r":a}); o=SimpleNamespace(id=16,status=OrderStatus.PAID,before_snapshot=None,after_snapshot=None,applied_at=None,apply_error=None)
 r=SimpleNamespace(status=ResellerStatus.ACTIVE,rebecca_admin_username="r")
 p=SimpleNamespace(traffic_gb=1,duration_days=2,service_ids=[7,8],users_limit=20)
 await apply_order(o,r,p,f,NOW)
 assert a.services == [7,8] and a.users_limit == 20
