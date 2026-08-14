from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.bot.handlers.customer.panels import PAGE_SIZE, account_panel, user_detail_panel, users_panel
from app.bot.handlers.owner.panels import child_detail, order_label, payment_summary, reseller_detail, reseller_label, reseller_summary
from app.bot.ui.navigation import mark_receipt_processed


def callbacks(markup):
    return {button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data}


def test_account_dashboard_and_navigation_are_compact_and_persian():
    reseller=SimpleNamespace(status="ACTIVE",last_known_data_limit=1000*1024**3,last_known_usage=257.4*1024**3,last_known_remaining=742.6*1024**3,expires_at=datetime.now(UTC)+timedelta(days=20))
    product=SimpleNamespace(name="Lite",users_limit=100)
    text,markup=account_panel(reseller,product,child_count=14,cached=True)
    assert "👤 حساب نمایندگی" in text and "🟢 فعال" in text and "Lite" in text
    assert "742.6 GB" in text and "14 / 100" in text
    assert {"account:users","buy:list","account:payments","account:refresh"} <= callbacks(markup)


def make_user(index):
    return SimpleNamespace(username=f"user{index}",status="active",data_limit=50*1024**3,used_traffic=37.6*1024**3,data_limit_unlimited=False,expire=datetime.now(UTC)+timedelta(days=9))


def test_customer_users_pagination_and_detail_callbacks():
    users=[make_user(i) for i in range(PAGE_SIZE+2)]
    text,markup=users_panel(users,0)
    assert "کاربران من — 8" in text and "account:users:1" in callbacks(markup)
    assert sum(cb.startswith("account:user:") for cb in callbacks(markup)) == PAGE_SIZE
    detail,detail_markup=user_detail_panel(users[0],1)
    assert "حجم کل" in detail and "مصرف" in detail and "باقی‌مانده" in detail
    assert "account:users:1" in callbacks(detail_markup)


def test_owner_reseller_dashboard_detail_and_labels():
    reseller=SimpleNamespace(id=1,telegram_username="arman",telegram_id=10,rebecca_admin_username="seller",status="ACTIVE",product_id=2,last_known_remaining=742*1024**3,expires_at=datetime.now(UTC)+timedelta(days=20),last_sync_at=None,automation_hold=False)
    product=SimpleNamespace(name="Lite",users_limit=100)
    assert "نمایندگان — 3" in reseller_summary({"ACTIVE":2,"TRIAL":1},3)
    assert "@arman" in reseller_label(reseller,product) and "Lite" in reseller_label(reseller,product)
    detail=reseller_detail(reseller,product,14)
    assert "14 / 100" in detail and "Automation Hold: خیر" in detail and "🟢 فعال" in detail


def test_owner_child_detail_keeps_all_lifecycle_safety_fields():
    child=SimpleNamespace(username="u",status="expired",local_status="EXPIRED",data_limit=10,used_traffic=4,expire=None,expired_detected_at="then",delete_after="later",deletion_hold=True,disabled_by_parent_reseller=True,disabled_by_own_expiry=False)
    reseller=SimpleNamespace(telegram_username="arman",telegram_id=1)
    text=child_detail(child,reseller)
    for label in ("وضعیت محلی","شناسایی انقضا","زمان حذف","قفل حذف","غیرفعال از والد","غیرفعال از انقضا"): assert label in text


def test_payment_dashboard_and_order_row_use_rial_and_persian_status():
    assert "منتظر تأیید: 2" in payment_summary({"WAITING_RECEIPT":2})
    order=SimpleNamespace(order_number="R1",amount=1490000,currency="IRT",status="WAITING_RECEIPT")
    reseller=SimpleNamespace(telegram_username="arman",telegram_id=1)
    row=order_label(order,reseller)
    assert "14,900,000 ریال" in row and "منتظر بررسی رسید" in row and "@arman" in row


@pytest.mark.asyncio
async def test_receipt_caption_is_finalized_and_buttons_removed():
    class Receipt:
        caption="رسید"
        def __init__(self): self.calls=[]
        async def edit_caption(self, **kwargs): self.calls.append(kwargs)
    receipt=Receipt(); await mark_receipt_processed(receipt,"✅ تأیید شد")
    assert receipt.calls == [{"caption":"رسید\n\n✅ تأیید شد","reply_markup":None}]


def test_important_navigation_routes_are_registered():
    from app.bot.handlers.common import router as customer_router
    from app.bot.handlers.owner.console import router as owner_router
    from app.config import Settings
    customer={item.callback.__name__ for item in customer_router(Settings(),None,None).callback_query.handlers}
    owner={item.callback.__name__ for item in owner_router(Settings(),None,None).callback_query.handlers}
    assert {"product_list_callback","choose_product","card_order","account_refresh","my_users_callback","my_user_detail","my_payments_callback","settings_root","settings_cancel"} <= customer
    assert {"reseller_pages","reseller_view","reseller_children","reseller_child_detail","order_pages","order_view","reseller_manage"} <= owner
