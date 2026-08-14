from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.models import Setting


class OwnerSettingState(StatesGroup):
    waiting_value = State()


EDITABLE_LABELS = {
    "card_number": "💳 شماره کارت",
    "card_holder": "👤 نام صاحب حساب",
    "card_bank": "🏦 نام بانک",
    "card_instructions": "📝 متن راهنمای پرداخت",
    "support_username": "☎️ آیدی پشتیبانی",
    "trial_service_ids": "🧪 سرویس‌های تست",
}


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def _main_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [("💳 تنظیمات کارت", "settings:card")],
            [("☎️ پشتیبانی", "settings:edit:support_username")],
            [("🎁 تنظیم تست", "settings:trial")],
            [("❌ بستن", "settings:close")],
        ]
    )


def _card_menu() -> InlineKeyboardMarkup:
    return _kb(
        [
            [("💳 شماره کارت", "settings:edit:card_number")],
            [("👤 صاحب حساب", "settings:edit:card_holder")],
            [("🏦 بانک", "settings:edit:card_bank")],
            [("📝 متن پرداخت", "settings:edit:card_instructions")],
            [("⬅️ بازگشت", "settings:home")],
        ]
    )


def _trial_menu(enabled: bool) -> InlineKeyboardMarkup:
    toggle = "🔴 غیرفعال کردن تست" if enabled else "🟢 فعال کردن تست"
    return _kb(
        [
            [(toggle, "settings:trial:toggle")],
            [("🧪 انتخاب Service ID", "settings:edit:trial_service_ids")],
            [("⬅️ بازگشت", "settings:home")],
        ]
    )


async def _values(session, keys: list[str]) -> dict[str, object]:
    rows = await session.execute(select(Setting.key, Setting.value).where(Setting.key.in_(keys)))
    return dict(rows.all())


async def _set_value(session, key: str, value: object) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def _masked_card(value: object) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return text or "تنظیم نشده"
    return f"{text[:4]} **** **** {text[-4:]}"


def _parse_value(key: str, raw: str) -> object:
    value = raw.strip()
    if key == "trial_service_ids":
        items = [int(x.strip()) for x in value.split(",") if x.strip()]
        if not items:
            raise ValueError("at least one service id is required")
        return items
    if not value:
        raise ValueError("empty setting")
    return value


def router(settings: Settings, sessions: async_sessionmaker) -> Router:
    r = Router()

    def is_owner(message_or_call: Message | CallbackQuery) -> bool:
        user = message_or_call.from_user
        return bool(user and user.id in settings.owner_ids)

    @r.message(F.text == "⚙️ تنظیمات")
    async def settings_home_message(message: Message, state: FSMContext):
        if not is_owner(message):
            return
        await state.clear()
        await message.answer("⚙️ تنظیمات ربات\nبخش موردنظر را انتخاب کنید:", reply_markup=_main_menu())

    @r.callback_query(F.data == "settings:home")
    async def settings_home(call: CallbackQuery, state: FSMContext):
        if not is_owner(call):
            return
        await state.clear()
        await call.message.edit_text("⚙️ تنظیمات ربات\nبخش موردنظر را انتخاب کنید:", reply_markup=_main_menu())
        await call.answer()

    @r.callback_query(F.data == "settings:close")
    async def settings_close(call: CallbackQuery, state: FSMContext):
        if not is_owner(call):
            return
        await state.clear()
        await call.message.edit_text("✅ تنظیمات بسته شد.")
        await call.answer()

    @r.callback_query(F.data == "settings:card")
    async def settings_card(call: CallbackQuery):
        if not is_owner(call):
            return
        async with sessions() as session:
            current = await _values(
                session,
                ["card_number", "card_holder", "card_bank", "card_instructions"],
            )
        text = (
            "💳 تنظیمات کارت‌به‌کارت\n\n"
            f"شماره کارت: {_masked_card(current.get('card_number'))}\n"
            f"صاحب حساب: {current.get('card_holder') or 'تنظیم نشده'}\n"
            f"بانک: {current.get('card_bank') or 'تنظیم نشده'}\n"
            f"راهنما: {current.get('card_instructions') or 'تنظیم نشده'}"
        )
        await call.message.edit_text(text, reply_markup=_card_menu())
        await call.answer()

    @r.callback_query(F.data == "settings:trial")
    async def settings_trial(call: CallbackQuery):
        if not is_owner(call):
            return
        async with sessions() as session:
            current = await _values(session, ["trial_enabled", "trial_service_ids"])
        enabled = bool(current.get("trial_enabled", False))
        ids = current.get("trial_service_ids") or []
        await call.message.edit_text(
            "🎁 تنظیمات تست رایگان\n\n"
            f"وضعیت: {'🟢 فعال' if enabled else '🔴 غیرفعال'}\n"
            f"Service ID: {', '.join(map(str, ids)) if ids else 'تنظیم نشده'}",
            reply_markup=_trial_menu(enabled),
        )
        await call.answer()

    @r.callback_query(F.data == "settings:trial:toggle")
    async def settings_trial_toggle(call: CallbackQuery):
        if not is_owner(call):
            return
        async with sessions() as session, session.begin():
            current = await session.get(Setting, "trial_enabled")
            enabled = not bool(current.value) if current else True
            await _set_value(session, "trial_enabled", enabled)
            ids_row = await session.get(Setting, "trial_service_ids")
            ids = ids_row.value if ids_row else []
        await call.message.edit_text(
            "🎁 تنظیمات تست رایگان\n\n"
            f"وضعیت: {'🟢 فعال' if enabled else '🔴 غیرفعال'}\n"
            f"Service ID: {', '.join(map(str, ids)) if ids else 'تنظیم نشده'}",
            reply_markup=_trial_menu(enabled),
        )
        await call.answer("ذخیره شد")

    @r.callback_query(F.data.startswith("settings:edit:"))
    async def settings_edit(call: CallbackQuery, state: FSMContext):
        if not is_owner(call):
            return
        key = call.data.removeprefix("settings:edit:")
        if key not in EDITABLE_LABELS:
            await call.answer("تنظیم ناشناخته", show_alert=True)
            return
        await state.set_state(OwnerSettingState.waiting_value)
        await state.update_data(setting_key=key)
        hint = "Service IDها را با ویرگول وارد کنید؛ مثال: 2 یا 1,2" if key == "trial_service_ids" else "مقدار جدید را در پیام بعدی ارسال کنید."
        await call.message.answer(f"{EDITABLE_LABELS[key]}\n{hint}\nبرای لغو /cancel را بفرستید.")
        await call.answer()

    @r.message(OwnerSettingState.waiting_value, F.text == "/cancel")
    async def settings_cancel(message: Message, state: FSMContext):
        if not is_owner(message):
            return
        await state.clear()
        await message.answer("لغو شد.", reply_markup=_main_menu())

    @r.message(OwnerSettingState.waiting_value)
    async def settings_save(message: Message, state: FSMContext):
        if not is_owner(message):
            return
        data = await state.get_data()
        key = data.get("setting_key")
        if key not in EDITABLE_LABELS or not message.text:
            await state.clear()
            await message.answer("تنظیم نامعتبر بود؛ دوباره از ⚙️ تنظیمات شروع کنید.")
            return
        try:
            value = _parse_value(key, message.text)
        except (ValueError, TypeError):
            await message.answer("مقدار نامعتبر است. دوباره وارد کنید یا /cancel را بفرستید.")
            return
        async with sessions() as session, session.begin():
            await _set_value(session, key, value)
        await state.clear()
        await message.answer(f"✅ {EDITABLE_LABELS[key]} ذخیره شد.", reply_markup=_main_menu())

    return r
