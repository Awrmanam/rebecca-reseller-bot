from __future__ import annotations


def owner_access(user_id: int, owner_ids: tuple[int, ...]) -> bool:
    return user_id in owner_ids


def normalize_card_number(value: str) -> str:
    normalized = value.replace(" ", "").replace("-", "")
    if not normalized.isdigit() or len(normalized) != 16:
        raise ValueError("شماره کارت باید ۱۶ رقم باشد.")
    return normalized


def mask_card_number(value: str | None) -> str:
    if not value:
        return "تنظیم نشده"
    digits = value.replace(" ", "").replace("-", "")
    if len(digits) < 8:
        return "****"
    return f"{digits[:4]}-****-****-{digits[-4:]}"


def normalize_support_username(value: str) -> str:
    normalized = value.strip().lstrip("@")
    if not normalized or not normalized.replace("_", "").isalnum():
        raise ValueError("نام کاربری پشتیبانی معتبر نیست.")
    return f"@{normalized}"


def parse_service_ids(value: str) -> list[int]:
    try:
        result = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    except ValueError as exc:
        raise ValueError("شناسه سرویس‌ها باید عدد صحیح و با ویرگول جدا شود.") from exc
    if not result or any(item < 0 for item in result):
        raise ValueError("حداقل یک شناسه سرویس معتبر وارد کنید.")
    return result


def approval_messages(order_number: str, dry_run: bool) -> tuple[str, str | None]:
    if dry_run:
        return (
            f"✅ پرداخت سفارش #{order_number} تأیید شد.\n🧪 ربات در حالت آزمایشی است؛ "
            "ساخت نمایندگی تا فعال‌شدن حالت زنده متوقف می‌ماند.",
            f"🧪 سفارش پرداخت‌شده #{order_number} در انتظار حالت زنده است.",
        )
    return (
        f"✅ پرداخت سفارش #{order_number} تأیید شد و ساخت/تمدید در صف پردازش قرار گرفت.",
        None,
    )


async def send_approval_notifications(
    bot, *, customer_id: int, owner_ids: tuple[int, ...], order_number: str, dry_run: bool
) -> None:
    """Send the single canonical post-approval customer/owner notification."""
    customer_text, owner_text = approval_messages(order_number, dry_run)
    await bot.send_message(customer_id, customer_text)
    if owner_text:
        for owner_id in owner_ids:
            await bot.send_message(owner_id, owner_text)
