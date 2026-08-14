from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards.main import customer_menu, owner_menu
from app.bot.middlewares.membership import is_member
from app.config import Settings
from app.database.models import (AuditLog, Order, OrderStatus, Payment, Product,
                                 RequiredChannel, Reseller, Setting, TrialRecord)
from app.payments.service import approve_card
from app.payments.plisio import PlisioClient
from app.rebecca.client import RebeccaClient
from app.reseller.service import credentials, provision
from app.reseller.trial import reserve_trial


class ReceiptState(StatesGroup):
    receipt = State()


def _inline(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


async def _channels(session) -> list[RequiredChannel]:
    return list((await session.scalars(select(RequiredChannel).where(RequiredChannel.enabled))).all())


async def _joined(bot: Bot, telegram_id: int, session) -> tuple[bool, list[RequiredChannel]]:
    channels = await _channels(session)
    return await is_member(bot, telegram_id, [item.chat_id for item in channels]), channels


def router(settings: Settings, sessions: async_sessionmaker, rebecca: RebeccaClient | None) -> Router:
    r = Router()

    @r.message(CommandStart())
    async def start(message: Message):
        owner = bool(message.from_user and message.from_user.id in settings.owner_ids)
        if not owner and message.from_user:
            async with sessions() as session, session.begin():
                existing = await session.scalar(select(Reseller).where(Reseller.telegram_id == message.from_user.id))
                if existing is None:
                    session.add(Reseller(telegram_id=message.from_user.id, telegram_username=message.from_user.username))
                else:
                    existing.telegram_username = message.from_user.username
        await message.answer("به ربات مدیریت نمایندگی Rebecca خوش آمدید.", reply_markup=owner_menu(settings.dry_run) if owner else customer_menu())

    @r.message(F.text == "👤 حساب من")
    async def account(message: Message):
        async with sessions() as session:
            reseller = await session.scalar(select(Reseller).where(Reseller.telegram_id == message.from_user.id))
        if not reseller:
            await message.answer("هنوز نمایندگی فعالی ندارید.")
            return
        cached = " (اطلاعات ذخیره‌شده)" if not rebecca else ""
        live = None
        if rebecca and reseller.rebecca_admin_username:
            try: live = await rebecca.get_admin(reseller.rebecca_admin_username)
            except Exception: cached = " (اطلاعات ذخیره‌شده؛ پنل در دسترس نیست)"
        remaining = ((live.data_limit-live.used_traffic) if live and live.data_limit else reseller.last_known_remaining) / 1024**3
        remaining_text = "نامحدود" if live and live.data_limit_unlimited else f"{remaining:.2f} GB"
        expire = live.expire if live else reseller.expires_at
        await message.answer(f"👤 حساب نمایندگی{cached}\nوضعیت: {reseller.status}\n📦 حجم باقی‌مانده: {remaining_text}\n📅 پایان: {expire or 'نامحدود'}")

    async def show_products(message: Message, bot: Bot, user_id: int):
        async with sessions() as session:
            joined, channels = await _joined(bot, user_id, session)
            if not joined:
                rows = [[("📢 عضویت در کانال", c.join_url)] for c in channels] + [[("✅ عضو شدم", "membership:retry")]]
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, url=d) if d.startswith("http") else InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows])
                await message.answer("برای استفاده از خدمات ابتدا عضو کانال شوید.", reply_markup=keyboard)
                return
            items = (await session.scalars(select(Product).where(Product.enabled, ~Product.deleted))).all()
        if not items:
            await message.answer("در حال حاضر محصول فعالی وجود ندارد.")
            return
        await message.answer("محصول را انتخاب کنید:", reply_markup=_inline([[(f"{p.name} — {p.price_toman:,.0f} تومان", f"buy:{p.id}")] for p in items]))

    @r.message(F.text == "🛒 خرید / تمدید")
    async def products(message: Message, bot: Bot):
        await show_products(message, bot, message.from_user.id)

    @r.callback_query(F.data == "membership:retry")
    async def membership_retry(call: CallbackQuery, bot: Bot):
        await show_products(call.message, bot, call.from_user.id)
        await call.answer()

    @r.callback_query(F.data.startswith("buy:"))
    async def choose_product(call: CallbackQuery):
        product_id = int(call.data.split(":")[1])
        await call.message.answer("روش پرداخت:", reply_markup=_inline([[("💳 کارت به کارت", f"paycard:{product_id}"), ("₿ پرداخت رمز ارز", f"paycrypto:{product_id}")]]))
        await call.answer()

    @r.callback_query(F.data.startswith("paycard:"))
    async def card_order(call: CallbackQuery, state: FSMContext):
        product_id = int(call.data.split(":")[1])
        async with sessions() as session, session.begin():
            product = await session.get(Product, product_id)
            reseller = await session.scalar(select(Reseller).where(Reseller.telegram_id == call.from_user.id))
            if not product or not reseller:
                await call.answer("محصول یا نمایندگی یافت نشد", show_alert=True); return
            number = f"R{datetime.now(UTC):%y%m%d}{secrets.token_hex(3).upper()}"
            order = Order(order_number=number, reseller_id=reseller.id, product_id=product.id, amount=product.price_toman, currency="IRT", status=OrderStatus.PENDING, payment_method="CARD", expires_at=datetime.now(UTC)+timedelta(hours=24))
            session.add(order); await session.flush()
            session.add(Payment(order_id=order.id, method="CARD", status="AWAITING_RECEIPT"))
            settings_rows = dict((await session.execute(select(Setting.key, Setting.value).where(Setting.key.in_(["card_number","card_holder","card_bank","card_instructions"])))).all())
        await state.set_state(ReceiptState.receipt); await state.update_data(order_id=order.id)
        await call.message.answer(f"سفارش #{number}\nمبلغ: {product.price_toman:,.0f} تومان\nکارت: {settings_rows.get('card_number','تنظیم نشده')}\nبه نام: {settings_rows.get('card_holder','-')}\n{settings_rows.get('card_instructions','لطفاً تصویر یا فایل رسید را ارسال کنید.')}" )
        await call.answer()

    @r.callback_query(F.data.startswith("paycrypto:"))
    async def crypto_order(call: CallbackQuery):
        if not settings.plisio_enabled or not settings.plisio_secret_key or not settings.public_base_url:
            await call.answer("پرداخت رمز ارز فعال نیست.", show_alert=True)
            return
        product_id = int(call.data.split(":")[1])
        async with sessions() as session, session.begin():
            product = await session.get(Product, product_id)
            reseller = await session.scalar(select(Reseller).where(Reseller.telegram_id == call.from_user.id))
            if not product or not reseller:
                await call.answer("محصول یا نمایندگی یافت نشد", show_alert=True)
                return
            rate = await session.scalar(select(Setting.value).where(Setting.key == "plisio_toman_per_source"))
            if not rate or Decimal(str(rate)) <= 0:
                await call.answer("نرخ تبدیل Plisio توسط مالک تنظیم نشده است.", show_alert=True)
                return
            source_amount = (Decimal(product.price_toman) / Decimal(str(rate))).quantize(Decimal("0.01"))
            number = f"P{datetime.now(UTC):%y%m%d}{secrets.token_hex(3).upper()}"
            order = Order(order_number=number, reseller_id=reseller.id, product_id=product.id, amount=source_amount, currency=settings.plisio_source_currency, status=OrderStatus.WAITING_PAYMENT, payment_method="PLISIO", expires_at=datetime.now(UTC)+timedelta(hours=24))
            session.add(order); await session.flush()
            client = PlisioClient(settings.plisio_secret_key)
            try:
                invoice = await client.create_invoice(order_number=number, source_currency=settings.plisio_source_currency, source_amount=source_amount, callback_url=f"{settings.public_base_url.rstrip('/')}/payments/plisio/callback?json=true", description=product.name)
            except Exception:
                order.status = OrderStatus.FAILED
                await call.answer("ساخت فاکتور ناموفق بود.", show_alert=True)
                return
            session.add(Payment(order_id=order.id, method="PLISIO", status="new", plisio_txn_id=invoice["txn_id"], invoice_url=invoice["invoice_url"]))
        await call.message.answer(f"فاکتور #{number}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="₿ پرداخت", url=invoice["invoice_url"])]]))
        await call.answer()

    @r.message(ReceiptState.receipt, F.photo | F.document)
    async def receipt(message: Message, state: FSMContext, bot: Bot):
        data = await state.get_data(); file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        async with sessions() as session, session.begin():
            order = await session.get(Order, data["order_id"]); payment = await session.scalar(select(Payment).where(Payment.order_id == order.id))
            if order.status != OrderStatus.PENDING: await message.answer("این سفارش دیگر رسید نمی‌پذیرد."); return
            order.status = OrderStatus.WAITING_RECEIPT; payment.telegram_file_id = file_id; payment.status = "WAITING_OWNER_APPROVAL"
            reseller = await session.get(Reseller, order.reseller_id); product = await session.get(Product, order.product_id)
        caption=f"💳 پرداخت جدید\nسفارش: #{order.order_number}\nTelegram ID: {reseller.telegram_id}\nمحصول: {product.name}\nمبلغ: {order.amount:,.0f} تومان"
        keyboard=_inline([[("✅ تأیید", f"cardok:{order.id}"), ("❌ رد", f"cardno:{order.id}")]])
        for owner in settings.owner_ids:
            if message.photo: await bot.send_photo(owner, file_id, caption=caption, reply_markup=keyboard)
            else: await bot.send_document(owner, file_id, caption=caption, reply_markup=keyboard)
        await state.clear(); await message.answer("رسید ثبت شد و پس از بررسی مالک نتیجه اعلام می‌شود.")

    @r.callback_query(F.data.startswith("cardok:"))
    async def card_approve(call: CallbackQuery, bot: Bot):
        if call.from_user.id not in settings.owner_ids: await call.answer("غیرمجاز", show_alert=True); return
        async with sessions() as session, session.begin():
            order_id = int(call.data.split(":")[1])
            claimed = await session.execute(update(Order).where(Order.id == order_id, Order.status == OrderStatus.WAITING_RECEIPT).values(status=OrderStatus.PAID, paid_at=datetime.now(UTC)))
            changed = claimed.rowcount == 1
            order = await session.get(Order, order_id)
            reseller = await session.get(Reseller, order.reseller_id)
        if changed: await bot.send_message(reseller.telegram_id, f"✅ پرداخت سفارش #{order.order_number} تأیید شد و برای اعمال در صف قرار گرفت.")
        await call.answer("تأیید شد" if changed else "قبلاً پردازش شده", show_alert=True)

    @r.callback_query(F.data.startswith("cardno:"))
    async def card_reject(call: CallbackQuery, bot: Bot):
        if call.from_user.id not in settings.owner_ids: await call.answer("غیرمجاز", show_alert=True); return
        async with sessions() as session, session.begin():
            order = await session.get(Order, int(call.data.split(":")[1]))
            if order.status != OrderStatus.WAITING_RECEIPT: await call.answer("قبلاً پردازش شده", show_alert=True); return
            order.status=OrderStatus.REJECTED; reseller=await session.get(Reseller,order.reseller_id)
        await bot.send_message(reseller.telegram_id,f"❌ پرداخت سفارش #{order.order_number} رد شد. برای پیگیری با پشتیبانی تماس بگیرید."); await call.answer("رد شد")

    @r.message(F.text == "👥 کاربران من")
    async def my_users(message: Message):
        async with sessions() as session:
            reseller=await session.scalar(select(Reseller).where(Reseller.telegram_id==message.from_user.id))
        if not reseller or not rebecca: await message.answer("اطلاعات کاربران در دسترس نیست."); return
        try: users=await rebecca.list_admin_users(reseller.rebecca_admin_username)
        except Exception: await message.answer("Rebecca در دسترس نیست؛ بعداً تلاش کنید."); return
        text="\n".join(f"👤 {u.username} | {u.status} | {'نامحدود' if not u.data_limit else f'{(u.data_limit-u.used_traffic)/1024**3:.2f} GB'}" for u in users[:20]) or "کاربری وجود ندارد."
        await message.answer(text)

    @r.message(F.text == "💳 پرداخت‌های من")
    async def my_payments(message: Message):
        async with sessions() as session:
            reseller=await session.scalar(select(Reseller).where(Reseller.telegram_id==message.from_user.id))
            orders=[] if not reseller else (await session.scalars(select(Order).where(Order.reseller_id==reseller.id).order_by(desc(Order.id)).limit(10))).all()
        await message.answer("\n".join(f"#{o.order_number} — {o.status} — {o.amount:,.0f}" for o in orders) or "پرداختی ثبت نشده است.")

    async def run_trial(message: Message, bot: Bot, user_id: int):
        if rebecca is None: await message.answer("پنل Rebecca تنظیم نشده است."); return
        async with sessions() as session:
            joined, channels = await _joined(bot,user_id,session)
            enabled = await session.scalar(select(Setting.value).where(Setting.key=="trial_enabled"))
            if not joined:
                buttons = [[InlineKeyboardButton(text="📢 عضویت در کانال", url=item.join_url)] for item in channels]
                buttons.append([InlineKeyboardButton(text="✅ عضو شدم", callback_data="membership:trial")])
                await message.answer("برای استفاده از خدمات ابتدا عضو کانال شوید.", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); return
            if enabled is False: await message.answer("تست رایگان غیرفعال است."); return
            record=await session.scalar(select(TrialRecord).where(TrialRecord.telegram_id == user_id))
            if record is None:
                username, _ = credentials()
                record=await reserve_trial(session,user_id,username,settings.trial_duration_hours)
                await session.commit()  # durable reservation before Rebecca mutation
            elif record.status not in {"PROVISIONING", "PROVISIONED_PENDING_DELIVERY"}:
                await message.answer("شما قبلاً از تست رایگان استفاده کرده‌اید."); return
            service_ids=await session.scalar(select(Setting.value).where(Setting.key=="trial_service_ids")) or []
            username=record.admin_username; _,password=credentials(); expire=record.expires_at
            try:
                await provision(rebecca,username=username,password=password,expire=expire,data_limit=settings.trial_traffic_gb*1024**3,services=service_ids,telegram_id=user_id)
                reseller = await session.scalar(select(Reseller).where(Reseller.telegram_id == user_id))
                if reseller:
                    reseller.rebecca_admin_username = username
                    reseller.status = "TRIAL"
                    reseller.trial = True
                    reseller.trial_used = True
                    reseller.expires_at = expire
                    reseller.purchased_traffic_bytes = settings.trial_traffic_gb * 1024**3
                record.status="PROVISIONED_PENDING_DELIVERY"; await session.commit()
            except Exception:
                record.status="FAILED"; await session.commit()
                for owner in settings.owner_ids: await bot.send_message(owner,f"🚨 ساخت تست برای {user_id} ناموفق/ناامن بود.")
                await message.answer("ساخت تست تأیید نشد؛ اطلاعات ورود ارسال نشد."); return
        try:
            await message.answer(f"🎁 تست شما فعال شد.\nنام کاربری: `{username}`\nرمز عبور: `{password}`\nاین اطلاعات فقط همین بار نمایش داده می‌شود.",parse_mode="Markdown")
        except Exception:
            return
        async with sessions() as session, session.begin():
            delivered = await session.scalar(select(TrialRecord).where(TrialRecord.telegram_id == user_id))
            if delivered:
                delivered.status = "ACTIVE"

    @r.message(F.text == "🎁 تست رایگان")
    async def trial(message: Message, bot: Bot):
        await run_trial(message, bot, message.from_user.id)

    @r.callback_query(F.data == "membership:trial")
    async def trial_membership_retry(call: CallbackQuery, bot: Bot):
        # Reuse the same tested flow; no trial reservation happens until all
        # channels are confirmed.
        await run_trial(call.message, bot, call.from_user.id)
        await call.answer()

    @r.message(F.text == "☎️ پشتیبانی")
    async def support(message: Message):
        async with sessions() as session: username=await session.scalar(select(Setting.value).where(Setting.key=="support_username"))
        await message.answer(f"پشتیبانی: {username or 'توسط مالک ربات'}")

    @r.message(F.text == "📦 محصولات")
    async def owner_products(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        async with sessions() as session: items=(await session.scalars(select(Product).where(~Product.deleted))).all()
        await message.answer("\n".join(f"{p.id}. {p.name} | {p.price_toman:,.0f} | {p.traffic_gb}GB | {p.duration_days}d | {'🟢' if p.enabled else '🔴'}" for p in items) or "محصولی نیست.\nبرای ساخت از /product_add استفاده کنید.")

    @r.message(Command("product_add"))
    async def product_add(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try:
            slug,name,price,traffic,days,services,users=message.text.split(maxsplit=1)[1].split("|")
            product=Product(slug=slug.strip(),name=name.strip(),price_toman=Decimal(price),traffic_gb=int(traffic),duration_days=int(days),service_ids=[int(x) for x in services.split(",") if x],users_limit=int(users) if users else None,service_type="CUSTOM")
            async with sessions() as session,session.begin(): session.add(product)
        except Exception: await message.answer("فرمت: /product_add slug|name|price|traffic_gb|days|service_ids|users_limit"); return
        await message.answer("✅ محصول ساخته شد.")

    @r.message(F.text == "🧾 لاگ عملیات")
    @r.message(F.text == "📋 تصمیم‌های اخیر")
    async def audits(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        async with sessions() as session: rows=(await session.scalars(select(AuditLog).order_by(desc(AuditLog.id)).limit(20))).all()
        await message.answer("\n".join(f"{x.action} | {x.target_identifier} | {x.result}" for x in rows) or "لاگی وجود ندارد.")

    @r.message(F.text.in_({"👥 نمایندگان","💳 پرداخت‌ها","📢 عضویت اجباری","🎁 تنظیم تست","⚙️ تنظیمات","📊 گزارش"}))
    async def owner_lists(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        async with sessions() as session:
            if message.text=="👥 نمایندگان": rows=(await session.scalars(select(Reseller).order_by(desc(Reseller.id)).limit(20))).all(); text="\n".join(f"{x.id}. {x.telegram_id} | {x.rebecca_admin_username} | {x.status}" for x in rows)
            elif message.text=="💳 پرداخت‌ها": rows=(await session.scalars(select(Order).order_by(desc(Order.id)).limit(20))).all(); text="\n".join(f"#{x.order_number} | {x.status} | {x.amount:,.0f}" for x in rows)
            elif message.text=="📢 عضویت اجباری": rows=(await session.scalars(select(RequiredChannel))).all(); text="\n".join(f"{x.id}. {x.title} {x.chat_id} {'🟢' if x.enabled else '🔴'}" for x in rows)
            else: text=f"🧪 DRY_RUN={settings.dry_run}\nDisable={settings.allow_disable_actions}\nDelete={settings.allow_delete_actions}\nبرای ویرایش تنظیمات از جدول settings و دستورات مدیریتی استفاده کنید."
        await message.answer(text or "موردی وجود ندارد.")


    @r.message(Command("product_edit"))
    async def product_edit(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try:
            product_id, field, value = message.text.split(maxsplit=3)[1:]
            allowed = {"name", "price_toman", "traffic_gb", "duration_days", "service_ids", "users_limit", "enabled"}
            if field not in allowed: raise ValueError
            async with sessions() as session, session.begin():
                product = await session.get(Product, int(product_id))
                if not product: raise ValueError
                if field == "service_ids": parsed = [int(x) for x in value.split(",") if x]
                elif field in {"traffic_gb", "duration_days", "users_limit"}: parsed = int(value)
                elif field == "price_toman": parsed = Decimal(value)
                elif field == "enabled": parsed = value.lower() in {"1", "true", "on"}
                else: parsed = value
                setattr(product, field, parsed)
        except Exception:
            await message.answer("فرمت: /product_edit id field value")
            return
        await message.answer("✅ محصول ویرایش شد.")

    @r.message(Command("product_delete"))
    async def product_delete(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: product_id = int(message.text.split()[1])
        except Exception: await message.answer("فرمت: /product_delete id"); return
        async with sessions() as session, session.begin():
            product = await session.get(Product, product_id)
            if product: product.deleted = True; product.enabled = False
        await message.answer("✅ محصول به‌صورت نرم حذف شد.")

    @r.message(Command("channel_add"))
    async def channel_add(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: chat_id, join_url, title = message.text.split(maxsplit=3)[1:]
        except Exception: await message.answer("فرمت: /channel_add chat_id join_url title"); return
        async with sessions() as session, session.begin(): session.add(RequiredChannel(chat_id=chat_id, join_url=join_url, title=title))
        await message.answer("✅ کانال افزوده شد.")

    @r.message(Command("setting"))
    async def setting_set(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: key, value = message.text.split(maxsplit=2)[1:]
        except Exception: await message.answer("فرمت: /setting key value"); return
        allowed={"trial_enabled","trial_service_ids","card_number","card_holder","card_bank","card_instructions","support_username","plisio_toman_per_source","user_delete_grace_hours","time_warning_thresholds","traffic_warning_thresholds"}
        if key not in allowed: await message.answer("این تنظیم از تلگرام قابل تغییر نیست."); return
        if key.endswith("_ids") or key.endswith("thresholds"): parsed=[int(x) for x in value.split(",") if x]
        elif key.endswith("enabled"): parsed=value.lower() in {"1","true","on"}
        else: parsed=value
        async with sessions() as session, session.begin():
            row=await session.get(Setting,key)
            if row: row.value=parsed
            else: session.add(Setting(key=key,value=parsed))
        await message.answer("✅ تنظیم ذخیره شد.")


    @r.message(Command("reseller_find"))
    async def reseller_find(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: term=message.text.split(maxsplit=1)[1].lstrip("@")
        except Exception: await message.answer("فرمت: /reseller_find telegram_id|username|rebecca_username"); return
        async with sessions() as session:
            clauses=[Reseller.telegram_username==term,Reseller.rebecca_admin_username==term]
            if term.isdigit(): clauses.append(Reseller.telegram_id==int(term))
            row=await session.scalar(select(Reseller).where(or_(*clauses)))
        if not row: await message.answer("نماینده یافت نشد."); return
        await message.answer(f"ID: {row.id}\nTelegram: {row.telegram_id} @{row.telegram_username or '-'}\nRebecca: {row.rebecca_admin_username or '-'}\nStatus: {row.status}\nHold: {row.automation_hold}")

    @r.message(Command("reseller_hold"))
    async def reseller_hold(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: reseller_id=int(message.text.split()[1])
        except Exception: await message.answer("فرمت: /reseller_hold id"); return
        async with sessions() as session,session.begin():
            row=await session.get(Reseller,reseller_id)
            if row: row.automation_hold=not row.automation_hold
        await message.answer("✅ وضعیت Hold تغییر کرد.")

    @r.message(Command("reseller_disable"))
    async def reseller_disable(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: reseller_id=int(message.text.split()[1])
        except Exception: await message.answer("فرمت: /reseller_disable id"); return
        async with sessions() as session,session.begin():
            row=await session.get(Reseller,reseller_id)
            if not row or not row.rebecca_admin_username: await message.answer("نماینده یافت نشد."); return
            row.status="SUSPENDED"; row.suspended_reason="manual owner action"
            if settings.dry_run or not settings.allow_disable_actions: session.add(AuditLog(actor=str(message.from_user.id),actor_type="OWNER",action="WOULD_DISABLE_RESELLER",target_type="reseller",target_identifier=row.rebecca_admin_username,result="DRY_RUN"))
            elif rebecca:
                await rebecca.disable_admin(row.rebecca_admin_username)
                await rebecca.disable_admin_users(row.rebecca_admin_username)
        await message.answer("✅ تعلیق دستی ثبت شد.")

    @r.message(Command("reseller_enable"))
    async def reseller_enable(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: reseller_id=int(message.text.split()[1])
        except Exception: await message.answer("فرمت: /reseller_enable id"); return
        async with sessions() as session,session.begin():
            row=await session.get(Reseller,reseller_id)
            if not row or row.status != "SUSPENDED": await message.answer("نماینده در تعلیق دستی نیست."); return
            if settings.dry_run or not settings.allow_disable_actions: await message.answer("در حالت آزمایشی تغییری در Rebecca انجام نشد."); return
            if not rebecca: await message.answer("Rebecca تنظیم نشده است."); return
            await rebecca.enable_admin(row.rebecca_admin_username)
            live=await rebecca.get_admin(row.rebecca_admin_username)
            if not live or live.status.lower() not in {"active","enabled"}: await message.answer("تأیید فعال‌سازی ناموفق بود."); return
            row.status="ACTIVE"; row.suspended_reason=None
        await message.answer("✅ تعلیق دستی رفع شد.")

    @r.message(Command("user_delete"))
    async def user_delete_prompt(message: Message):
        if message.from_user.id not in settings.owner_ids: return
        try: owner,username=message.text.split()[1:3]
        except Exception: await message.answer("فرمت: /user_delete rebecca_admin username"); return
        await message.answer(f"آیا از حذف {username} مطمئن هستید؟",reply_markup=_inline([[('بله، حذف شود',f'ownerdelete:{owner}:{username}'),('لغو','ownerdelete_cancel')]]))

    @r.callback_query(F.data.startswith("ownerdelete:"))
    async def user_delete_confirm(call: CallbackQuery):
        if call.from_user.id not in settings.owner_ids: await call.answer("غیرمجاز",show_alert=True); return
        _,owner,username=call.data.split(":",2)
        if settings.dry_run or not settings.destructive_actions or not settings.allow_delete_actions: await call.answer("کلید حذف خاموش است",show_alert=True); return
        if not rebecca: await call.answer("Rebecca تنظیم نشده",show_alert=True); return
        live=await rebecca.get_user(username)
        if not live or live.admin_username != owner: await call.answer("مالکیت زنده تأیید نشد",show_alert=True); return
        await rebecca.delete_user(username)
        if await rebecca.get_user(username) is not None: await call.answer("تأیید حذف ناموفق",show_alert=True); return
        await call.message.edit_text(f"✅ {username} حذف شد."); await call.answer()

    @r.callback_query(F.data == "ownerdelete_cancel")
    async def user_delete_cancel(call: CallbackQuery):
        await call.message.edit_text("لغو شد."); await call.answer()

    return r
