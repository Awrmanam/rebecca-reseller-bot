from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

def customer_menu():
    rows=[["👤 حساب من","🛒 خرید / تمدید"],["👥 کاربران من","💳 پرداخت‌های من"],["🎁 تست رایگان","☎️ پشتیبانی"]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in row] for row in rows],resize_keyboard=True)
def owner_menu(mode: bool | str):
    dry_run = mode == "dry_run" if isinstance(mode, str) else mode
    rows=[["👥 نمایندگان","💳 پرداخت‌ها"],["📦 محصولات","🎁 تنظیم تست"],["📢 عضویت اجباری","⚙️ تنظیمات"],["📊 گزارش","🧾 لاگ عملیات"],[f"🧪 حالت آزمایشی: {'روشن' if dry_run else 'خاموش'}","📋 تصمیم‌های اخیر"]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x) for x in row] for row in rows],resize_keyboard=True)
