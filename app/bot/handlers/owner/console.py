from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.audit.redaction import redact
from app.database.models import AuditLog, Order, OrderStatus, Product, RequiredChannel, Reseller, Setting
from app.database.settings import RuntimeSettingsService

PAGE_SIZE = 8


def _keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


def _page(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def router(settings, sessions, lifecycle=None) -> Router:
    r = Router(name="owner-functional-console")
    runtime = RuntimeSettingsService(settings)

    async def authorized(event) -> bool:
        user = event.from_user
        if user and user.id in settings.owner_ids:
            return True
        if isinstance(event, CallbackQuery):
            await event.answer("غیرمجاز", show_alert=True)
        return False

    async def reseller_page(target, page=0, status=None):
        async with sessions() as session:
            query = select(Reseller)
            if status:
                query = query.where(Reseller.status == status)
            rows = (await session.scalars(query.order_by(Reseller.id.desc()).offset(page*PAGE_SIZE).limit(PAGE_SIZE+1))).all()
        buttons = [[(f"{x.telegram_id} | {x.status}", f"reseller:view:{x.id}")] for x in rows[:PAGE_SIZE]]
        nav=[]
        if page: nav.append(("⬅️", f"reseller:page:{page-1}:{status or 'ALL'}"))
        if len(rows)>PAGE_SIZE: nav.append(("➡️", f"reseller:page:{page+1}:{status or 'ALL'}"))
        if nav: buttons.append(nav)
        buttons.append([("ACTIVE","reseller:page:0:ACTIVE"),("PROVISIONING","reseller:page:0:PROVISIONING")])
        await target.answer("👥 مدیریت نمایندگان", reply_markup=_keyboard(buttons))

    @r.message(F.text == "👥 نمایندگان")
    async def resellers(message: Message):
        if await authorized(message): await reseller_page(message)

    @r.callback_query(F.data.startswith("reseller:page:"))
    async def resellers_callback(call: CallbackQuery):
        if not await authorized(call): return
        _,_,page,status=call.data.split(":",3)
        await reseller_page(call.message,_page(page),None if status=="ALL" else status); await call.answer()

    @r.callback_query(F.data.startswith("reseller:view:"))
    async def reseller_detail(call: CallbackQuery):
        if not await authorized(call): return
        async with sessions() as session:
            row=await session.get(Reseller,int(call.data.rsplit(":",1)[1]))
        if not row: await call.answer("یافت نشد",show_alert=True); return
        await call.message.answer(
            f"Telegram: {row.telegram_id} @{row.telegram_username or '-'}\nRebecca: {row.rebecca_admin_username or '-'}\n"
            f"وضعیت: {row.status}\nانقضا: {row.expires_at or '-'}\nحجم: {row.last_known_remaining/1024**3:.2f} GB\n"
            f"Hold: {row.automation_hold}\nآخرین همگام‌سازی: {row.last_sync_at or '-'}",
            reply_markup=_keyboard([[("Hold/Unhold",f"reseller:hold:{row.id}"),("سفارش‌ها",f"reseller:orders:{row.id}")]])
        ); await call.answer()

    @r.callback_query(F.data.startswith("reseller:hold:"))
    async def reseller_hold(call: CallbackQuery):
        if not await authorized(call): return
        async with sessions() as session,session.begin():
            row=await session.get(Reseller,int(call.data.rsplit(":",1)[1])); row.automation_hold=not row.automation_hold
        await call.answer("ذخیره شد",show_alert=True)

    async def order_page(target,page=0,status=None):
        async with sessions() as session:
            query=select(Order)
            if status: query=query.where(Order.status==status)
            rows=(await session.scalars(query.order_by(Order.id.desc()).offset(page*PAGE_SIZE).limit(PAGE_SIZE+1))).all()
        buttons=[[(f"#{x.order_number} | {x.status}",f"order:view:{x.id}")] for x in rows[:PAGE_SIZE]]
        buttons.append([("PAID","order:page:0:PAID"),("FAILED","order:page:0:FAILED"),("همه","order:page:0:ALL")])
        buttons.append([("▶️ پردازش PAID","orders:reconcile")])
        await target.answer("💳 کنسول پرداخت‌ها",reply_markup=_keyboard(buttons))

    @r.message(F.text == "💳 پرداخت‌ها")
    async def payments(message: Message):
        if await authorized(message): await order_page(message)

    @r.callback_query(F.data.startswith("order:page:"))
    async def order_callback(call: CallbackQuery):
        if not await authorized(call): return
        _,_,page,status=call.data.split(":",3); await order_page(call.message,_page(page),None if status=="ALL" else status); await call.answer()

    @r.callback_query(F.data.startswith("order:view:"))
    async def order_detail(call: CallbackQuery):
        if not await authorized(call): return
        async with sessions() as session:
            order=await session.get(Order,int(call.data.rsplit(":",1)[1])); product=await session.get(Product,order.product_id)
        await call.message.answer(f"#{order.order_number}\nمحصول: {product.name}\nمبلغ: {order.amount} {order.currency}\nروش: {order.payment_method}\nوضعیت: {order.status}\nخطا: {redact(order.apply_error) or '-'}")
        await call.answer()

    @r.message(F.text == "📊 گزارش")
    async def report(message: Message):
        if not await authorized(message): return
        now=datetime.now(UTC); today=now.replace(hour=0,minute=0,second=0,microsecond=0)
        async with sessions() as session:
            reseller_counts=dict((await session.execute(select(Reseller.status,func.count()).group_by(Reseller.status))).all())
            order_counts=dict((await session.execute(select(Order.status,func.count()).group_by(Order.status))).all())
            async def sales(since): return await session.scalar(select(func.coalesce(func.sum(Order.amount),0)).where(Order.status==OrderStatus.APPLIED,Order.currency=="IRT",Order.paid_at>=since))
            day,week,month=await sales(today),await sales(now-timedelta(days=7)),await sales(today.replace(day=1))
        await message.answer("📊 داشبورد\n"+" | ".join(f"{k}: {v}" for k,v in reseller_counts.items())+"\nسفارش‌ها: "+" | ".join(f"{k}: {v}" for k,v in order_counts.items())+f"\nفروش کارت (تومان): امروز {day:,.0f} | ۷ روز {week:,.0f} | ماه {month:,.0f}")

    @r.message(F.text.in_({"🧾 لاگ عملیات","📋 تصمیم‌های اخیر"}))
    async def logs(message: Message):
        if not await authorized(message): return
        decisions=message.text.startswith("📋")
        async with sessions() as session:
            query=select(AuditLog)
            if decisions: query=query.where((AuditLog.action.like("WOULD_%")) | (AuditLog.result.in_(["DRY_RUN","ERROR","SKIPPED","BLOCKED"])))
            rows=(await session.scalars(query.order_by(AuditLog.id.desc()).limit(20))).all()
        await message.answer("\n".join(f"{x.timestamp} | {x.action} | {redact(x.target_identifier)} | {x.result} | {redact(x.error) or '-'}" for x in rows) or "موردی وجود ندارد.")

    @r.message(F.text == "📦 محصولات")
    async def products(message: Message):
        if not await authorized(message): return
        async with sessions() as session: rows=(await session.scalars(select(Product).where(~Product.deleted))).all()
        buttons=[[(f"{x.name} | {'🟢' if x.enabled else '🔴'}",f"product:toggle:{x.id}"),("حذف",f"product:delete_warn:{x.id}")] for x in rows]
        await message.answer("📦 مدیریت محصولات\nویرایش کامل از دکمه هر محصول انجام می‌شود.",reply_markup=_keyboard(buttons or [[("تازه‌سازی","product:refresh")]]))

    @r.callback_query(F.data.startswith("product:toggle:"))
    async def product_toggle(call: CallbackQuery):
        if not await authorized(call): return
        async with sessions() as session,session.begin(): row=await session.get(Product,int(call.data.rsplit(":",1)[1])); row.enabled=not row.enabled
        await call.answer("ذخیره شد",show_alert=True)

    @r.callback_query(F.data.startswith("product:delete_warn:"))
    async def product_delete_warn(call: CallbackQuery):
        if not await authorized(call): return
        product_id=call.data.rsplit(":",1)[1]
        await call.message.answer("حذف نرم محصول تأیید شود؟",reply_markup=_keyboard([[("تأیید",f"product:delete:{product_id}"),("لغو","product:refresh")]])); await call.answer()

    @r.callback_query(F.data.startswith("product:delete:"))
    async def product_delete(call: CallbackQuery):
        if not await authorized(call): return
        async with sessions() as session,session.begin(): row=await session.get(Product,int(call.data.rsplit(":",1)[1])); row.deleted=True; row.enabled=False
        await call.answer("نرم‌حذف شد",show_alert=True)

    @r.message(F.text == "📢 عضویت اجباری")
    async def channels(message: Message):
        if not await authorized(message): return
        async with sessions() as session: rows=(await session.scalars(select(RequiredChannel))).all()
        await message.answer("📢 مدیریت عضویت اجباری",reply_markup=_keyboard([[(f"{x.title} {'🟢' if x.enabled else '🔴'}",f"channel:toggle:{x.id}")] for x in rows] or [[("تازه‌سازی","channel:refresh")]]))

    @r.callback_query(F.data.startswith("channel:toggle:"))
    async def channel_toggle(call: CallbackQuery):
        if not await authorized(call): return
        async with sessions() as session,session.begin(): row=await session.get(RequiredChannel,int(call.data.rsplit(":",1)[1])); row.enabled=not row.enabled
        await call.answer("ذخیره شد",show_alert=True)

    @r.message(F.text == "🎁 تنظیم تست")
    async def trial_settings(message: Message):
        if not await authorized(message): return
        async with sessions() as session:
            enabled=await session.scalar(select(Setting.value).where(Setting.key=="trial_enabled"))
            service_ids=await session.scalar(select(Setting.value).where(Setting.key=="trial_service_ids")) or []
            traffic=await runtime.trial_traffic_gb(session)
            duration=await runtime.trial_duration_hours(session)
        await message.answer(
            f"🎁 تنظیم تست\nوضعیت: {'روشن' if enabled is not False else 'خاموش'}\n"
            f"سرویس‌ها: {service_ids}\nحجم: {traffic} GB\nمدت: {duration} ساعت",
            reply_markup=_keyboard([[("تنظیمات کامل تست","settings:trial")]]),
        )

    return r
