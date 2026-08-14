from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.ui.status import status_text

PAGE_SIZE = 6
GB = 1024**3


def days_left(expire, now=None) -> str:
    if expire is None:
        return "نامحدود"
    now = now or datetime.now(UTC)
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=UTC)
    return f"{max(0, (expire - now).days)} روز"


def gb(value: int | float | None) -> str:
    return "نامحدود" if value is None else f"{max(0, value) / GB:,.1f} GB"


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


def account_panel(reseller, product, *, live=None, child_count=0, cached=False):
    limit = live.data_limit if live else reseller.last_known_data_limit
    used = live.used_traffic if live else reseller.last_known_usage
    remaining = None if live and live.data_limit_unlimited else max(0, (limit or 0) - used)
    expire = live.expire if live else reseller.expires_at
    users_limit = live.users_limit if live and live.users_limit is not None else (
        product.users_limit if product else None
    )
    note = "\nℹ️ اطلاعات ذخیره‌شده" if cached else ""
    text = (
        "👤 حساب نمایندگی\n\n"
        f"{status_text(reseller.status)}\n\n"
        f"📦 پلن: {product.name if product else '-'}\n"
        f"📊 باقی‌مانده: {gb(remaining)}\n"
        f"📅 اعتبار: {days_left(expire)}\n"
        f"👥 کاربران: {child_count} / {users_limit if users_limit is not None else 'نامحدود'}"
        f"{note}"
    )
    return text, keyboard([
        [("👥 کاربران من", "account:users")],
        [("🛒 تمدید", "buy:list"), ("💳 پرداخت‌ها", "account:payments")],
        [("🔄 بروزرسانی", "account:refresh")],
    ])


def users_panel(users, page=0):
    page = max(0, page)
    start = page * PAGE_SIZE
    visible = users[start:start + PAGE_SIZE]
    lines = [f"👥 کاربران من — {len(users)}", ""]
    buttons = []
    for offset, user in enumerate(visible):
        active = str(user.status).lower() in {"active", "enabled"}
        remaining = None if user.data_limit_unlimited else (user.data_limit or 0) - user.used_traffic
        lines.extend([f"{'🟢' if active else '🟡'} {user.username}", f"{gb(remaining)} • {days_left(user.expire)}", ""])
        buttons.append([(f"{'🟢' if active else '🟡'} {user.username}", f"account:user:{start + offset}:{page}")])
    nav = []
    if page: nav.append(("⬅️", f"account:users:{page-1}"))
    if start + PAGE_SIZE < len(users): nav.append(("➡️", f"account:users:{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([("🔄 بروزرسانی", f"account:users:{page}"), ("⬅️ حساب", "account:refresh")])
    return "\n".join(lines).rstrip(), keyboard(buttons)


def user_detail_panel(user, page=0, index=0):
    active = str(user.status).lower() in {"active", "enabled"}
    remaining = None if user.data_limit_unlimited else (user.data_limit or 0) - user.used_traffic
    text = (
        f"👤 {user.username}\n{'🟢 فعال' if active else '🟡 غیرفعال'}\n\n"
        f"📊 حجم کل: {gb(None if user.data_limit_unlimited else user.data_limit)}\n"
        f"📥 مصرف: {gb(user.used_traffic)}\n"
        f"📦 باقی‌مانده: {gb(remaining)}\n"
        f"📅 انقضا: {days_left(user.expire)}"
    )
    return text, keyboard([
        [("🔄 بروزرسانی", f"account:user:{index}:{page}")],
        [("⬅️ کاربران", f"account:users:{page}")],
    ])
