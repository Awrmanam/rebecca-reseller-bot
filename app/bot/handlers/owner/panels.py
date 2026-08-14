from __future__ import annotations

from app.bot.handlers.customer.panels import days_left, gb
from app.bot.ui.money import format_rial
from app.bot.ui.status import status_text


def reseller_summary(counts: dict, total: int) -> str:
    return (
        f"👥 نمایندگان — {total}\n\n"
        f"🟢 فعال: {counts.get('ACTIVE', 0)}\n"
        f"🟡 در حال ساخت: {counts.get('PROVISIONING', 0)}\n"
        f"🔴 منقضی: {counts.get('EXPIRED', 0)}\n"
        f"⏸ تعلیق: {counts.get('SUSPENDED', 0)}\n"
        f"🎁 تست: {counts.get('TRIAL', 0)}"
    )


def reseller_label(reseller, product=None) -> str:
    identity = f"@{reseller.telegram_username}" if reseller.telegram_username else str(reseller.telegram_id)
    short_status = status_text(reseller.status).split(" ", 1)[0]
    plan = product.name if product else "-"
    return f"{short_status} {identity}\n{plan} • {gb(reseller.last_known_remaining)} • {days_left(reseller.expires_at)}"


def reseller_detail(reseller, product, child_count: int) -> str:
    identity = f"@{reseller.telegram_username}" if reseller.telegram_username else str(reseller.telegram_id)
    limit = product.users_limit if product else None
    return (
        f"👤 {identity}\n"
        f"🆔 Telegram: {reseller.telegram_id}\n"
        f"🔑 Rebecca: {reseller.rebecca_admin_username or '-'}\n\n"
        f"📦 پلن: {product.name if product else '-'}\n"
        f"وضعیت: {status_text(reseller.status)}\n"
        f"📊 باقی‌مانده: {gb(reseller.last_known_remaining)}\n"
        f"📅 اعتبار: {days_left(reseller.expires_at)}\n"
        f"👥 کاربران: {child_count} / {limit if limit is not None else 'نامحدود'}\n"
        f"🔄 Sync: {reseller.last_sync_at or '-'}\n"
        f"⏸ Automation Hold: {'بله' if reseller.automation_hold else 'خیر'}"
    )


def child_label(child) -> str:
    return f"{status_text(child.status)} {child.username}\n{gb((child.data_limit or 0)-child.used_traffic)} • {days_left(child.expire)}"


def child_detail(child, reseller) -> str:
    return (
        f"👤 {child.username}\n"
        f"👥 نماینده: @{reseller.telegram_username or reseller.telegram_id}\n"
        f"وضعیت Rebecca: {status_text(child.status)}\n"
        f"وضعیت محلی: {status_text(child.local_status)}\n\n"
        f"📊 حجم کل: {gb(child.data_limit)}\n"
        f"📥 مصرف: {gb(child.used_traffic)}\n"
        f"📦 باقی‌مانده: {gb((child.data_limit or 0)-child.used_traffic)}\n"
        f"📅 انقضا: {child.expire or '-'}\n\n"
        f"شناسایی انقضا: {child.expired_detected_at or '-'}\n"
        f"زمان حذف: {child.delete_after or '-'}\n"
        f"قفل حذف: {'بله' if child.deletion_hold else 'خیر'}\n"
        f"غیرفعال از والد: {'بله' if child.disabled_by_parent_reseller else 'خیر'}\n"
        f"غیرفعال از انقضا: {'بله' if child.disabled_by_own_expiry else 'خیر'}"
    )


def payment_summary(counts: dict) -> str:
    return (
        "💳 سفارش‌ها\n\n"
        f"🟡 منتظر رسید: {counts.get('PENDING', 0)}\n"
        f"🟠 منتظر تأیید: {counts.get('WAITING_RECEIPT', 0)}\n"
        f"🔵 پرداخت‌شده: {counts.get('PAID', 0)}\n"
        f"⚙️ در حال ساخت: {counts.get('APPLYING', 0)}\n"
        f"🟢 تکمیل: {counts.get('APPLIED', 0)}\n"
        f"🔴 خطا: {counts.get('FAILED', 0)}"
    )


def order_label(order, reseller) -> str:
    identity = f"@{reseller.telegram_username}" if reseller and reseller.telegram_username else str(reseller.telegram_id if reseller else "-")
    amount = format_rial(order.amount) if order.currency == "IRT" else f"{order.amount} {order.currency}"
    return f"#{order.order_number} • {identity}\n{amount} • {status_text(order.status)}"
