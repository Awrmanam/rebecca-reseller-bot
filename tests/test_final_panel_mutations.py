from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot.handlers.owner.console import router
from app.bot.handlers.common import router as common_router
from app.config import Settings
from app.database.models import Order, OrderStatus, Product, RequiredChannel, Reseller, Setting
from app.rebecca.models import Admin
from tests.fakes import FakeRebecca
from tests.test_owner_settings import sessions


class PanelMessage:
    def __init__(self): self.text="panel"; self.caption=None; self.edits=[]
    async def edit_text(self,text,**kwargs): self.text=text; self.edits.append((text,kwargs))


class Bot:
    def __init__(self): self.sent=[]; self.panel_edits=[]
    async def send_message(self,chat_id,text): self.sent.append((chat_id,text))
    async def edit_message_text(self,**kwargs): self.panel_edits.append(kwargs)


class Call:
    def __init__(self,data,owner=1,bot=None): self.data=data; self.from_user=SimpleNamespace(id=owner); self.message=PanelMessage(); self.bot=bot or Bot(); self.answers=[]
    async def answer(self,*args,**kwargs): self.answers.append((args,kwargs))


def handler(r,name,kind="callback_query"):
    return next(x.callback for x in getattr(r,kind).handlers if x.callback.__name__==name)


@pytest.mark.asyncio
async def test_order_rejection_notifies_exactly_once_and_refreshes_markup():
    factory=await sessions()
    async with factory() as s,s.begin():
        product=Product(name="Lite",slug="reject-lite",service_type="LITE",service_ids=[1],duration_days=30,traffic_gb=1,price_toman=Decimal(10),users_limit=1)
        reseller=Reseller(telegram_id=77); s.add_all([product,reseller]); await s.flush()
        order=Order(order_number="R-REJECT",reseller_id=reseller.id,product_id=product.id,amount=10,currency="IRT",status=OrderStatus.WAITING_RECEIPT,payment_method="CARD"); s.add(order); await s.flush(); oid=order.id
        s.add(Setting(key="support_username",value="@help"))
    r=router(Settings(owner_ids=(1,)),factory); reject=handler(r,"order_reject"); call=Call(f"order:reject:{oid}")
    await reject(call); await reject(call)
    assert len(call.bot.sent)==1 and call.bot.sent[0][0]==77
    assert "#R-REJECT" in call.bot.sent[0][1] and "@help" in call.bot.sent[0][1]
    assert "❌ رد شده" in call.message.text
    markup=call.message.edits[-1][1]["reply_markup"]
    callbacks={b.callback_data for row in markup.inline_keyboard for b in row}
    assert f"order:reject:{oid}" not in callbacks and "order:page:0:ALL" in callbacks
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_product_and_channel_toggles_refresh_same_panel():
    factory=await sessions()
    async with factory() as s,s.begin():
        p=Product(name="P",slug="toggle-p",service_type="LITE",service_ids=[1],duration_days=1,traffic_gb=1,price_toman=1,users_limit=1)
        ch=RequiredChannel(chat_id="@c",join_url="https://t.me/c",title="C"); s.add_all([p,ch]); await s.flush(); pid,cid=p.id,ch.id
    r=router(Settings(owner_ids=(1,)),factory)
    pc=Call(f"product:toggle:{pid}"); await handler(r,"product_toggle")(pc)
    cc=Call(f"channel:toggle:{cid}"); await handler(r,"channel_toggle")(cc)
    assert pc.message.edits and "غیرفعال" in pc.message.text
    assert cc.message.edits and "غیرفعال" in cc.message.text
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("action","initial","remote","expected"),[("disable","ACTIVE","active","تعلیق‌شده"),("enable","SUSPENDED","disabled","فعال")])
async def test_reseller_safe_state_change_refreshes_detail(action,initial,remote,expected):
    factory=await sessions(); fake=FakeRebecca({"seller":Admin(username="seller",role="reseller",status=remote)})
    async def disable(username): fake.admins[username].status="disabled"
    async def enable(username): fake.admins[username].status="active"
    fake.disable_admin=disable; fake.enable_admin=enable
    lifecycle=SimpleNamespace(rebecca=fake)
    async with factory() as s,s.begin():
        x=Reseller(telegram_id=2,rebecca_admin_username="seller",status=initial); s.add(x); await s.flush(); rid=x.id
        s.add(Setting(key="operations_mode",value="live"))
    r=router(Settings(owner_ids=(1,),allow_disable_actions=True),factory,lifecycle)
    call=Call(f"reseller:{action}:{rid}"); await handler(r,"reseller_state_change")(call)
    assert call.message.edits and expected in call.message.text
    await factory.kw["bind"].dispose()


@pytest.mark.asyncio
async def test_trial_toggle_refreshes_same_settings_panel():
    factory=await sessions(); r=common_router(Settings(owner_ids=(1,)),factory,None)
    call=Call("settings:trial_toggle"); await handler(r,"settings_trial_toggle")(call)
    assert call.message.edits and "خاموش" in call.message.text
    await factory.kw["bind"].dispose()


class State:
    def __init__(self,data): self.data=data; self.cleared=False
    async def get_data(self): return self.data
    async def clear(self): self.cleared=True


@pytest.mark.asyncio
@pytest.mark.parametrize("name,data,back",[
    ("product_create_value",{"panel_chat_id":1,"panel_message_id":9,"index":0,"values":{}},"product:page:0"),
    ("channel_create",{"panel_chat_id":1,"panel_message_id":9,"index":0,"values":{}},"channel:refresh"),
])
async def test_create_fsm_cancel_edits_origin_panel(name,data,back):
    factory=await sessions(); r=router(Settings(owner_ids=(1,)),factory); bot=Bot()
    message=SimpleNamespace(text="/cancel",from_user=SimpleNamespace(id=1),bot=bot)
    state=State(data); await handler(r,name,"message")(message,state)
    assert state.cleared and len(bot.panel_edits)==1
    assert bot.panel_edits[0]["reply_markup"].inline_keyboard[0][0].callback_data==back
    await factory.kw["bind"].dispose()
