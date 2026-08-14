from __future__ import annotations

from html import escape

import aiogram.types as telegram_types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def credential_message(
    *, product_name: str, traffic_gb: int, duration_days: int,
    expiry: object, users_limit: int | None, username: str, password: str,
    panel_url: str | None,
) -> str:
    """Format the one-time credential message; callers must never persist it."""
    panel = escape(panel_url) if panel_url else "تنظیم نشده"
    return (
        "✅ نمایندگی شما فعال شد\n\n"
        f"📦 پلن: {escape(product_name)}\n"
        f"📊 حجم: {traffic_gb} GB\n"
        f"📅 اعتبار: {duration_days} روز / {escape(str(expiry))}\n"
        f"👥 سقف کاربران: {users_limit if users_limit is not None else 'نامحدود'}\n\n"
        f"👤 نام کاربری:\n<code>{escape(username)}</code>\n\n"
        f"🔑 رمز عبور:\n<code>{escape(password)}</code>\n\n"
        f"🔗 ورود به پنل:\n{panel}"
    )


def credential_keyboard(
    username: str, password: str, panel_url: str | None
) -> InlineKeyboardMarkup:
    """Use Bot API copy buttons; credentials never enter callback_data."""
    copy_button = getattr(telegram_types, "CopyTextButton", None)
    rows = []
    if copy_button is not None:
        rows.extend([
            [InlineKeyboardButton(text="📋 کپی نام کاربری", copy_text=copy_button(text=username))],
            [InlineKeyboardButton(text="📋 کپی رمز عبور", copy_text=copy_button(text=password))],
        ])
    if panel_url:
        rows.append([InlineKeyboardButton(text="🔗 ورود به پنل", url=panel_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
