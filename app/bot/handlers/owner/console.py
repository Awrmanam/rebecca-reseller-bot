from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.audit.redaction import redact
from app.database.models import (
    AuditLog, Order, OrderStatus, Payment, Product, RequiredChannel, Reseller,
    ResellerStatus, ResellerUserCache, Setting,
)
from app.database.settings import RuntimeSettingsService
from app.payments.reconciliation import schedule_reconciliation

PAGE_SIZE = 8
PRODUCT_FIELDS = ("name", "slug", "price_toman", "traffic_gb", "duration_days", "service_ids", "users_limit")
PRODUCT_LABELS = {"name":"نام", "slug":"شناسه", "price_toman":"قیمت", "traffic_gb":"حجم", "duration_days":"مدت", "service_ids":"سرویس‌ها", "users_limit":"سقف کاربران"}
ORDER_FILTERS = {
    "WAITING": (OrderStatus.PENDING, OrderStatus.WAITING_RECEIPT),
    "PAYMENT": (OrderStatus.WAITING_PAYMENT,), "PAID": (OrderStatus.PAID,),
    "APPLYING": (OrderStatus.APPLYING,), "APPLIED": (OrderStatus.APPLIED,),
    "FAILED": (OrderStatus.FAILED,), "CLOSED": (OrderStatus.REJECTED, OrderStatus.EXPIRED),
}
DECISION_RESULTS = ("DRY_RUN", "ERROR", "SKIPPED", "BLOCKED", "RETRYABLE")

class ProductCreate(StatesGroup): value = State()
class ProductEdit(StatesGroup): value = State()
class ChannelCreate(StatesGroup): value = State()
class ChannelEdit(StatesGroup): value = State()
class ResellerSearch(StatesGroup): value = State()


def _keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows
    ])


def _page(value):
    try: return max(0, int(value))
    except (TypeError, ValueError): return 0


def _parse_product(field, value):
    value=value.strip()
    if field in {"name","slug"}:
        if not value or len(value)>100 or (field=="slug" and not value.replace("-","").replace("_","").isalnum()): raise ValueError("مقدار معتبر نیست.")
        return value
    if field=="price_toman":
        try: parsed=Decimal(value)
        except InvalidOperation as exc: raise ValueError("قیمت نامعتبر است.") from exc
        if parsed<=0: raise ValueError("قیمت باید مثبت باشد.")
        return parsed
    if field in {"traffic_gb","duration_days"}:
        parsed=int(value)
        if parsed<=0: raise ValueError("مقدار باید مثبت باشد.")
        return parsed
    if field=="users_limit":
        if value.lower() in {"none","-","نامحدود"}: return None
        parsed=int(value)
        if parsed<=0: raise ValueError("سقف باید مثبت یا نامحدود باشد.")
        return parsed
    if field=="service_ids":
        parsed=sorted({int(x.strip()) for x in value.split(",") if x.strip()})
        if not parsed or any(x<0 for x in parsed): raise ValueError("شناسه سرویس نامعتبر است.")
        return parsed
    raise ValueError("فیلد مجاز نیست.")


def router(settings, sessions, lifecycle=None) -> Router:
    r=Router(name="owner-functional-console"); runtime=RuntimeSettingsService(settings)
    rebecca=getattr(lifecycle,"rebecca",None)

    async def authorized(event):
        if event.from_user and event.from_user.id in settings.owner_ids: return True
        if isinstance(event,CallbackQuery): await event.answer("غیرمجاز",show_alert=True)
        return False

    async def product_page(target,page=0):
        async with sessions() as s:
            rows=(await s.scalars(select(Product).where(~Product.deleted).order_by(Product.id.desc()).offset(page*PAGE_SIZE).limit(PAGE_SIZE+1))).all()
        buttons=[[(f"{x.name} | {'🟢' if x.enabled else '🔴'}",f"product:view:{x.id}")] for x in rows[:PAGE_SIZE]]
        nav=[]
        if page: nav.append(("⬅️",f"product:page:{page-1}"))
        if len(rows)>PAGE_SIZE: nav.append(("➡️",f"product:page:{page+1}"))
        if nav: buttons.append(nav)
        buttons.append([("➕ محصول جدید","product:add"),("🔄 تازه‌سازی",f"product:page:{page}")])
        await target.answer("📦 مدیریت کامل محصولات",reply_markup=_keyboard(buttons))

    @r.message(F.text=="📦 محصولات")
    async def products(m):
        if await authorized(m): await product_page(m)

    @r.callback_query(F.data.startswith("product:page:"))
    async def product_pages(c):
        if not await authorized(c): return
        await product_page(c.message,_page(c.data.rsplit(":",1)[1])); await c.answer()

    @r.callback_query(F.data.startswith("product:view:"))
    async def product_view(c):
        if not await authorized(c): return
        pid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s: p=await s.get(Product,pid)
        if not p: await c.answer("یافت نشد",show_alert=True); return
        rows=[[(PRODUCT_LABELS[f],f"product:edit:{pid}:{f}") for f in PRODUCT_FIELDS[i:i+2]] for i in range(0,len(PRODUCT_FIELDS),2)]
        rows += [[("فعال/غیرفعال",f"product:toggle:{pid}"),("حذف نرم",f"product:delete_warn:{pid}")],[ ("⬅️ فهرست","product:page:0") ]]
        services=""
        if rebecca and getattr(getattr(rebecca,"capabilities",None),"services_list",False):
            try: services="\nسرویس‌های Rebecca: "+", ".join(f"{x.get('id')}={x.get('name','-')}" for x in (await rebecca.list_services())[:30])
            except Exception: services="\nخواندن سرویس‌های Rebecca ناموفق بود."
        await c.message.answer(f"{p.name}\nslug: {p.slug}\nقیمت: {p.price_toman}\nحجم: {p.traffic_gb}GB\nمدت: {p.duration_days} روز\nسرویس‌ها: {p.service_ids}\nسقف: {p.users_limit or 'نامحدود'}{services}",reply_markup=_keyboard(rows)); await c.answer()

    @r.callback_query(F.data=="product:add")
    async def product_add(c,state:FSMContext):
        if not await authorized(c): return
        await state.set_state(ProductCreate.value); await state.set_data({"index":0,"values":{}})
        await c.message.answer(f"{PRODUCT_LABELS[PRODUCT_FIELDS[0]]} را بفرستید. /cancel برای لغو"); await c.answer()

    @r.message(ProductCreate.value)
    async def product_create_value(m,state:FSMContext):
        if not await authorized(m): await state.clear(); return
        if m.text=="/cancel": await state.clear(); await m.answer("لغو شد."); return
        data=await state.get_data(); index=data["index"]; field=PRODUCT_FIELDS[index]
        try: value=_parse_product(field,m.text)
        except (ValueError,TypeError) as exc: await m.answer(str(exc)); return
        values=data["values"]; values[field]=value; index+=1
        if index<len(PRODUCT_FIELDS):
            await state.update_data(index=index,values=values); await m.answer(f"{PRODUCT_LABELS[PRODUCT_FIELDS[index]]} را بفرستید."); return
        try:
            async with sessions() as s,s.begin(): s.add(Product(**values,service_type="CUSTOM"))
        except IntegrityError: await m.answer("slug تکراری است؛ دوباره شروع کنید."); await state.clear(); return
        await state.clear(); await m.answer("✅ محصول ساخته شد.")

    @r.callback_query(F.data.startswith("product:edit:"))
    async def product_edit(c,state:FSMContext):
        if not await authorized(c): return
        _,_,pid,field=c.data.split(":",3)
        if field not in PRODUCT_FIELDS: await c.answer("غیرمجاز",show_alert=True); return
        await state.set_state(ProductEdit.value); await state.set_data({"id":int(pid),"field":field})
        await c.message.answer(f"مقدار جدید {PRODUCT_LABELS[field]} را بفرستید. /cancel برای لغو"); await c.answer()

    @r.message(ProductEdit.value)
    async def product_edit_value(m,state:FSMContext):
        if not await authorized(m): await state.clear(); return
        if m.text=="/cancel": await state.clear(); await m.answer("لغو شد."); return
        data=await state.get_data()
        try: value=_parse_product(data["field"],m.text)
        except (ValueError,TypeError) as exc: await m.answer(str(exc)); return
        try:
            async with sessions() as s,s.begin():
                p=await s.get(Product,data["id"]); setattr(p,data["field"],value)
        except IntegrityError: await m.answer("slug تکراری است."); return
        await state.clear(); await m.answer("✅ ویرایش شد.")

    @r.callback_query(F.data.startswith("product:toggle:"))
    async def product_toggle(c):
        if not await authorized(c): return
        async with sessions() as s,s.begin(): p=await s.get(Product,int(c.data.rsplit(":",1)[1])); p.enabled=not p.enabled
        await c.answer("ذخیره شد",show_alert=True)

    @r.callback_query(F.data.startswith("product:delete_warn:"))
    async def product_delete_warn(c):
        if not await authorized(c): return
        pid=c.data.rsplit(":",1)[1]; await c.message.answer("حذف نرم تأیید شود؟",reply_markup=_keyboard([[("تأیید",f"product:delete:{pid}"),("لغو",f"product:view:{pid}")]])); await c.answer()

    @r.callback_query(F.data.startswith("product:delete:"))
    async def product_delete(c):
        if not await authorized(c): return
        async with sessions() as s,s.begin(): p=await s.get(Product,int(c.data.rsplit(":",1)[1])); p.deleted=True; p.enabled=False
        await c.answer("نرم‌حذف شد",show_alert=True)

    async def channel_page(target):
        async with sessions() as s: rows=(await s.scalars(select(RequiredChannel).order_by(RequiredChannel.id.desc()))).all()
        buttons=[[(f"{x.title} {'🟢' if x.enabled else '🔴'}",f"channel:view:{x.id}")] for x in rows]
        buttons.append([("➕ افزودن کانال","channel:add"),("🔄 تازه‌سازی","channel:refresh")])
        await target.answer("📢 مدیریت عضویت اجباری",reply_markup=_keyboard(buttons))

    @r.message(F.text=="📢 عضویت اجباری")
    async def channels(m):
        if await authorized(m): await channel_page(m)
    @r.callback_query(F.data=="channel:refresh")
    async def channel_refresh(c):
        if await authorized(c): await channel_page(c.message); await c.answer()
    @r.callback_query(F.data.startswith("channel:view:"))
    async def channel_view(c):
        if not await authorized(c): return
        cid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s: x=await s.get(RequiredChannel,cid)
        await c.message.answer(f"{x.title}\nchat_id: {x.chat_id}\njoin_url: {x.join_url}\nوضعیت: {x.enabled}",reply_markup=_keyboard([[('ویرایش chat_id',f'channel:edit:{cid}:chat_id'),('ویرایش عنوان',f'channel:edit:{cid}:title')],[('ویرایش لینک',f'channel:edit:{cid}:join_url'),('فعال/غیرفعال',f'channel:toggle:{cid}')],[('حذف/غیرفعال',f'channel:remove_warn:{cid}'),('🔄',f'channel:view:{cid}')]])); await c.answer()
    @r.callback_query(F.data=="channel:add")
    async def channel_add(c,state:FSMContext):
        if not await authorized(c): return
        await state.set_state(ChannelCreate.value); await state.set_data({"index":0,"values":{}}); await c.message.answer("chat_id را بفرستید. /cancel برای لغو"); await c.answer()
    @r.message(ChannelCreate.value)
    async def channel_create(m,state:FSMContext):
        if not await authorized(m): await state.clear(); return
        if m.text=="/cancel": await state.clear(); await m.answer("لغو شد."); return
        fields=("chat_id","title","join_url"); data=await state.get_data(); field=fields[data["index"]]; value=m.text.strip()
        if not value or (field=="join_url" and not value.startswith(("https://","http://"))): await m.answer("مقدار معتبر نیست."); return
        values=data["values"]; values[field]=value; index=data["index"]+1
        if index<3: await state.update_data(index=index,values=values); await m.answer(f"{fields[index]} را بفرستید."); return
        try:
            async with sessions() as s,s.begin(): s.add(RequiredChannel(**values))
        except IntegrityError: await m.answer("chat_id تکراری است."); await state.clear(); return
        await state.clear(); await m.answer("✅ کانال افزوده شد.")
    @r.callback_query(F.data.startswith("channel:edit:"))
    async def channel_edit(c,state:FSMContext):
        if not await authorized(c): return
        _,_,cid,field=c.data.split(":",3)
        if field not in {"chat_id","title","join_url"}: await c.answer("غیرمجاز",show_alert=True); return
        await state.set_state(ChannelEdit.value); await state.set_data({"id":int(cid),"field":field}); await c.message.answer("مقدار جدید را بفرستید. /cancel برای لغو"); await c.answer()
    @r.message(ChannelEdit.value)
    async def channel_edit_value(m,state:FSMContext):
        if not await authorized(m): await state.clear(); return
        if m.text=="/cancel": await state.clear(); await m.answer("لغو شد."); return
        data=await state.get_data(); value=m.text.strip()
        if not value or (data["field"]=="join_url" and not value.startswith(("https://","http://"))): await m.answer("مقدار معتبر نیست."); return
        try:
            async with sessions() as s,s.begin(): x=await s.get(RequiredChannel,data["id"]); setattr(x,data["field"],value)
        except IntegrityError: await m.answer("chat_id تکراری است."); return
        await state.clear(); await m.answer("✅ ویرایش شد.")
    @r.callback_query(F.data.startswith("channel:toggle:"))
    async def channel_toggle(c):
        if not await authorized(c): return
        async with sessions() as s,s.begin(): x=await s.get(RequiredChannel,int(c.data.rsplit(":",1)[1])); x.enabled=not x.enabled
        await c.answer("ذخیره شد",show_alert=True)
    @r.callback_query(F.data.startswith("channel:remove_warn:"))
    async def channel_remove_warn(c):
        if not await authorized(c): return
        cid=c.data.rsplit(":",1)[1]; await c.message.answer("کانال به‌صورت امن غیرفعال شود؟",reply_markup=_keyboard([[("تأیید",f"channel:remove:{cid}"),("لغو",f"channel:view:{cid}")]])); await c.answer()
    @r.callback_query(F.data.startswith("channel:remove:"))
    async def channel_remove(c):
        if not await authorized(c): return
        async with sessions() as s,s.begin(): x=await s.get(RequiredChannel,int(c.data.rsplit(":",1)[1])); x.enabled=False
        await c.answer("غیرفعال شد",show_alert=True)

    async def reseller_page(target,page=0,status=None):
        async with sessions() as s:
            q=select(Reseller)
            if status: q=q.where(Reseller.status==status)
            rows=(await s.scalars(q.order_by(Reseller.id.desc()).offset(page*PAGE_SIZE).limit(PAGE_SIZE+1))).all()
        buttons=[[(f"{x.telegram_id} | {x.status}",f"reseller:view:{x.id}")] for x in rows[:PAGE_SIZE]]
        nav=[]
        if page: nav.append(("⬅️",f"reseller:page:{page-1}:{status or 'ALL'}"))
        if len(rows)>PAGE_SIZE: nav.append(("➡️",f"reseller:page:{page+1}:{status or 'ALL'}"))
        if nav: buttons.append(nav)
        for statuses in (("ACTIVE","PROVISIONING","EXPIRED"),("SUSPENDED","TRIAL","ALL")): buttons.append([(x,f"reseller:page:0:{x}") for x in statuses])
        buttons.append([("🔎 جستجو","reseller:search"),("🔄 تازه‌سازی",f"reseller:page:{page}:{status or 'ALL'}")])
        await target.answer("👥 مدیریت نمایندگان",reply_markup=_keyboard(buttons))
    @r.message(F.text=="👥 نمایندگان")
    async def resellers(m):
        if await authorized(m): await reseller_page(m)
    @r.callback_query(F.data.startswith("reseller:page:"))
    async def reseller_pages(c):
        if not await authorized(c): return
        _,_,page,status=c.data.split(":",3); await reseller_page(c.message,_page(page),None if status=="ALL" else status); await c.answer()
    @r.callback_query(F.data=="reseller:search")
    async def reseller_search(c,state:FSMContext):
        if not await authorized(c): return
        await state.set_state(ResellerSearch.value); await c.message.answer("Telegram ID، @username یا نام Rebecca را بفرستید."); await c.answer()
    @r.message(ResellerSearch.value)
    async def reseller_search_value(m,state:FSMContext):
        if not await authorized(m): await state.clear(); return
        term=m.text.strip().lstrip("@"); clauses=[Reseller.telegram_username==term,Reseller.rebecca_admin_username==term]
        if term.isdigit(): clauses.append(Reseller.telegram_id==int(term))
        async with sessions() as s: rows=(await s.scalars(select(Reseller).where(or_(*clauses)).limit(PAGE_SIZE))).all()
        await state.clear(); await m.answer("نتایج",reply_markup=_keyboard([[(f"{x.telegram_id} | {x.status}",f"reseller:view:{x.id}")] for x in rows] or [[("یافت نشد","reseller:page:0:ALL")]]))
    @r.callback_query(F.data.startswith("reseller:view:"))
    async def reseller_view(c):
        if not await authorized(c): return
        rid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s: x=await s.get(Reseller,rid)
        if not x: await c.answer("یافت نشد",show_alert=True); return
        await c.message.answer(f"Telegram: {x.telegram_id} @{x.telegram_username or '-'}\nRebecca: {x.rebecca_admin_username or '-'}\nوضعیت: {x.status}\nانقضا: {x.expires_at or '-'}\nحجم/مصرف: {x.last_known_data_limit/1024**3:.2f}/{x.last_known_usage/1024**3:.2f} GB\nHold: {x.automation_hold}\nآخرین sync: {x.last_sync_at or '-'}",reply_markup=_keyboard([[('🔄 Rebecca',f'reseller:refresh:{rid}'),('Hold/Unhold',f'reseller:hold:{rid}')],[('سفارش‌ها',f'reseller:orders:{rid}'),('کاربران',f'reseller:children:{rid}')],[('تعلیق امن',f'reseller:disable_warn:{rid}'),('فعال‌سازی امن',f'reseller:enable_warn:{rid}')]])); await c.answer()
    @r.callback_query(F.data.startswith("reseller:refresh:"))
    async def reseller_refresh(c):
        if not await authorized(c): return
        rid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s,s.begin():
            x=await s.get(Reseller,rid)
            if not rebecca or not x.rebecca_admin_username: await c.answer("Rebecca در دسترس نیست",show_alert=True); return
            live=await rebecca.get_admin(x.rebecca_admin_username)
            if live:
                x.last_known_data_limit=live.data_limit or 0; x.last_known_usage=live.used_traffic; x.last_known_remaining=(live.data_limit or 0)-live.used_traffic; x.expires_at=live.expire; x.last_sync_at=datetime.now(UTC)
        await c.answer("همگام شد",show_alert=True)
    @r.callback_query(F.data.startswith("reseller:hold:"))
    async def reseller_hold(c):
        if not await authorized(c): return
        async with sessions() as s,s.begin(): x=await s.get(Reseller,int(c.data.rsplit(":",1)[1])); x.automation_hold=not x.automation_hold
        await c.answer("ذخیره شد",show_alert=True)
    @r.callback_query(F.data.startswith("reseller:orders:"))
    async def reseller_orders(c):
        if not await authorized(c): return
        rid=int(c.data.rsplit(":",1)[1]); await order_page(c.message,0,None,rid); await c.answer()
    @r.callback_query(F.data.startswith("reseller:children:"))
    async def reseller_children(c):
        if not await authorized(c): return
        rid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s: rows=(await s.scalars(select(ResellerUserCache).where(ResellerUserCache.reseller_id==rid).order_by(ResellerUserCache.id.desc()).limit(30))).all()
        await c.message.answer("\n".join(f"{x.username} | {x.status} | {(x.data_limit-x.used_traffic)/1024**3:.2f}GB | {x.expire or '-'}" for x in rows) or "کاربری ثبت نشده است."); await c.answer()
    @r.callback_query(F.data.regexp(r"reseller:(disable|enable)_warn:"))
    async def reseller_state_warn(c):
        if not await authorized(c): return
        action="disable" if ":disable_" in c.data else "enable"; rid=c.data.rsplit(":",1)[1]
        await c.message.answer("این عملیات نیازمند تأیید و عبور از کلیدهای ایمنی محیط است.",reply_markup=_keyboard([[("تأیید",f"reseller:{action}:{rid}"),("لغو",f"reseller:view:{rid}")]])); await c.answer()
    @r.callback_query(F.data.regexp(r"reseller:(disable|enable):"))
    async def reseller_state_change(c):
        if not await authorized(c): return
        action=c.data.split(":",2)[1]; rid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s,s.begin():
            x=await s.get(Reseller,rid)
            if await runtime.is_dry_run(s) or not settings.allow_disable_actions: await c.answer("کلید ایمنی یا LIVE فعال نیست",show_alert=True); return
            if not rebecca or not x.rebecca_admin_username: await c.answer("Rebecca در دسترس نیست",show_alert=True); return
            if action=="disable": await rebecca.disable_admin(x.rebecca_admin_username); expected={"disabled","inactive"}
            else: await rebecca.enable_admin(x.rebecca_admin_username); expected={"active","enabled"}
            live=await rebecca.get_admin(x.rebecca_admin_username)
            if not live or live.status.lower() not in expected: await c.answer("تأیید Rebecca ناموفق بود",show_alert=True); return
            x.status=ResellerStatus.SUSPENDED if action=="disable" else ResellerStatus.ACTIVE; x.suspended_reason="manual owner action" if action=="disable" else None
        await c.answer("انجام و تأیید شد",show_alert=True)

    async def order_page(target,page=0,group=None,reseller_id=None):
        async with sessions() as s:
            q=select(Order)
            if group: q=q.where(Order.status.in_(ORDER_FILTERS[group]))
            if reseller_id: q=q.where(Order.reseller_id==reseller_id)
            rows=(await s.scalars(q.order_by(Order.id.desc()).offset(page*PAGE_SIZE).limit(PAGE_SIZE+1))).all()
        buttons=[[(f"#{x.order_number} | {x.status}",f"order:view:{x.id}")] for x in rows[:PAGE_SIZE]]
        nav=[]
        suffix=group or "ALL"
        if page: nav.append(("⬅️",f"order:page:{page-1}:{suffix}"))
        if len(rows)>PAGE_SIZE: nav.append(("➡️",f"order:page:{page+1}:{suffix}"))
        if nav: buttons.append(nav)
        for groups in (("WAITING","PAYMENT","PAID"),("APPLYING","APPLIED","FAILED"),("CLOSED","ALL")): buttons.append([(x,f"order:page:0:{x}") for x in groups])
        buttons.append([("▶️ پردازش محدود PAID","orders:reconcile"),("🔄",f"order:page:{page}:{suffix}")]); await target.answer("💳 کنسول پرداخت‌ها",reply_markup=_keyboard(buttons))
    @r.message(F.text=="💳 پرداخت‌ها")
    async def payments(m):
        if await authorized(m): await order_page(m)
    @r.callback_query(F.data.startswith("order:page:"))
    async def order_pages(c):
        if not await authorized(c): return
        _,_,page,group=c.data.split(":",3); await order_page(c.message,_page(page),None if group=="ALL" else group); await c.answer()
    @r.callback_query(F.data.startswith("order:view:"))
    async def order_view(c):
        if not await authorized(c): return
        oid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s:
            o=await s.get(Order,oid); p=await s.get(Product,o.product_id); reseller=await s.get(Reseller,o.reseller_id)
        actions=[]
        if o.payment_method=="CARD" and o.status==OrderStatus.WAITING_RECEIPT: actions.append([("✅ تأیید",f"order:approve:{oid}"),("❌ رد",f"order:reject:{oid}")])
        if o.status in {OrderStatus.PAID,OrderStatus.APPLYING,OrderStatus.FAILED}: actions.append([("▶️ پردازش/تلاش مجدد",f"order:retry:{oid}")])
        actions.append([("🔄",f"order:view:{oid}"),("⬅️", "order:page:0:ALL")])
        await c.message.answer(f"#{o.order_number}\nTelegram: {reseller.telegram_id} @{reseller.telegram_username or '-'}\nمحصول: {p.name}\nمبلغ: {o.amount} {o.currency}\nروش: {o.payment_method}\nوضعیت: {o.status}\nساخت: {o.created_at}\nپرداخت: {o.paid_at or '-'}\nخطا: {redact(o.apply_error) or '-'}",reply_markup=_keyboard(actions)); await c.answer()
    @r.callback_query(F.data.startswith("order:approve:"))
    async def order_approve(c):
        if not await authorized(c): return
        oid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s,s.begin():
            result=await s.execute(update(Order).where(Order.id==oid,Order.status==OrderStatus.WAITING_RECEIPT).values(status=OrderStatus.PAID,paid_at=datetime.now(UTC))); changed=result.rowcount==1
        if changed and lifecycle: schedule_reconciliation(lifecycle)
        await c.answer("تأیید شد" if changed else "قبلاً پردازش شده",show_alert=True)
    @r.callback_query(F.data.startswith("order:reject:"))
    async def order_reject(c):
        if not await authorized(c): return
        oid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s,s.begin(): result=await s.execute(update(Order).where(Order.id==oid,Order.status==OrderStatus.WAITING_RECEIPT).values(status=OrderStatus.REJECTED)); changed=result.rowcount==1
        await c.answer("رد شد" if changed else "قبلاً پردازش شده",show_alert=True)
    @r.callback_query(F.data.startswith("order:retry:"))
    async def order_retry(c):
        if not await authorized(c): return
        oid=int(c.data.rsplit(":",1)[1])
        async with sessions() as s,s.begin():
            o=await s.get(Order,oid)
            if o.status==OrderStatus.FAILED and o.paid_at: o.status=OrderStatus.PAID; o.apply_error=None
            eligible=o.status in {OrderStatus.PAID,OrderStatus.APPLYING}
        if eligible and lifecycle: schedule_reconciliation(lifecycle)
        await c.answer("تلاش مجدد زمان‌بندی شد" if eligible else "سفارش قابل پردازش نیست",show_alert=True)

    async def audit_page(target,kind,page=0,result="ALL"):
        async with sessions() as s:
            q=select(AuditLog)
            if kind=="decision": q=q.where(or_(AuditLog.action.like("WOULD_%"),AuditLog.result.in_(DECISION_RESULTS),AuditLog.action.like("%RETRY%"),AuditLog.action.like("%FAILED%")))
            if result!="ALL": q=q.where(AuditLog.result==result)
            rows=(await s.scalars(q.order_by(AuditLog.id.desc()).offset(page*PAGE_SIZE).limit(PAGE_SIZE+1))).all()
        buttons=[[(f"{x.action} | {x.result}",f"audit:view:{kind}:{x.id}")] for x in rows[:PAGE_SIZE]]; nav=[]
        if page: nav.append(("⬅️",f"audit:page:{kind}:{page-1}:{result}"))
        if len(rows)>PAGE_SIZE: nav.append(("➡️",f"audit:page:{kind}:{page+1}:{result}"))
        if nav: buttons.append(nav)
        buttons.append([(x,f"audit:page:{kind}:0:{x}") for x in ("OK","ERROR","DRY_RUN","ALL")]); buttons.append([("🔄 تازه‌سازی",f"audit:page:{kind}:{page}:{result}")]); await target.answer("📋 تصمیم‌های اخیر" if kind=="decision" else "🧾 لاگ عملیات",reply_markup=_keyboard(buttons))
    @r.message(F.text=="🧾 لاگ عملیات")
    async def logs(m):
        if await authorized(m): await audit_page(m,"log")
    @r.message(F.text=="📋 تصمیم‌های اخیر")
    async def decisions(m):
        if await authorized(m): await audit_page(m,"decision")
    @r.callback_query(F.data.startswith("audit:page:"))
    async def audit_pages(c):
        if not await authorized(c): return
        _,_,kind,page,result=c.data.split(":",4); await audit_page(c.message,kind,_page(page),result); await c.answer()
    @r.callback_query(F.data.startswith("audit:view:"))
    async def audit_view(c):
        if not await authorized(c): return
        _,_,kind,aid=c.data.split(":",3)
        async with sessions() as s: x=await s.get(AuditLog,int(aid))
        await c.message.answer(f"زمان: {x.timestamp}\nعملیات: {x.action}\nهدف: {redact(x.target_identifier)}\nنتیجه: {x.result}\nخطا: {redact(x.error) or '-'}",reply_markup=_keyboard([[('⬅️',f'audit:page:{kind}:0:ALL')]])); await c.answer()

    async def show_report(target):
        now=datetime.now(UTC); today=now.replace(hour=0,minute=0,second=0,microsecond=0)
        async with sessions() as s:
            rc=dict((await s.execute(select(Reseller.status,func.count()).group_by(Reseller.status))).all()); oc=dict((await s.execute(select(Order.status,func.count()).group_by(Order.status))).all())
            async def sales(since): return await s.scalar(select(func.coalesce(func.sum(Order.amount),0)).where(Order.status==OrderStatus.APPLIED,Order.currency=="IRT",Order.paid_at>=since))
            day,week,month=await sales(today),await sales(now-timedelta(days=7)),await sales(today.replace(day=1)); children=await s.scalar(select(func.count(ResellerUserCache.id)))
        await target.answer("📊 داشبورد\n"+" | ".join(f"{k}: {v}" for k,v in rc.items())+"\nسفارش‌ها: "+" | ".join(f"{k}: {v}" for k,v in oc.items())+f"\nفروش کارت (تومان): امروز {day:,.0f} | ۷ روز {week:,.0f} | ماه {month:,.0f}\nکاربران شناخته‌شده: {children}",reply_markup=_keyboard([[('🔄 تازه‌سازی','report:refresh')]]))
    @r.message(F.text=="📊 گزارش")
    async def report(m):
        if await authorized(m): await show_report(m)
    @r.callback_query(F.data=="report:refresh")
    async def report_refresh(c):
        if not await authorized(c): return
        await show_report(c.message); await c.answer()

    @r.message(F.text=="🎁 تنظیم تست")
    async def trial_settings(m):
        if not await authorized(m): return
        async with sessions() as s:
            enabled=await s.scalar(select(Setting.value).where(Setting.key=="trial_enabled")); ids=await s.scalar(select(Setting.value).where(Setting.key=="trial_service_ids")) or []; traffic=await runtime.trial_traffic_gb(s); duration=await runtime.trial_duration_hours(s)
        await m.answer(f"🎁 تنظیم تست\nوضعیت: {'روشن' if enabled is not False else 'خاموش'}\nسرویس‌ها: {ids}\nحجم: {traffic} GB\nمدت: {duration} ساعت",reply_markup=_keyboard([[('تنظیم کامل','settings:trial')]]))
    return r
