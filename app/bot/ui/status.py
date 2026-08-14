from __future__ import annotations


STATUS_PRESENTATION = {
    "ACTIVE": "🟢 فعال", "PROVISIONING": "🟡 در حال ساخت",
    "EXPIRED": "🔴 منقضی", "SUSPENDED": "⏸ تعلیق‌شده", "TRIAL": "🎁 تست",
    "PENDING": "🟡 منتظر رسید", "WAITING_RECEIPT": "🟡 منتظر بررسی رسید",
    "WAITING_PAYMENT": "🟠 منتظر پرداخت", "PAID": "🔵 پرداخت‌شده",
    "APPLYING": "⚙️ در حال اعمال", "APPLIED": "🟢 تکمیل‌شده", "FAILED": "🔴 خطا",
    "REJECTED": "❌ رد شده", "DISABLED": "⏸ غیرفعال", "ERROR": "🔴 خطا",
}


def status_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return STATUS_PRESENTATION.get(str(raw).upper(), str(raw))

