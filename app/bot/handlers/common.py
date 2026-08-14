from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from app.bot.keyboards.main import customer_menu, owner_menu

def router(owner_ids: tuple[int,...], dry_run: bool) -> Router:
    r=Router()
    @r.message(CommandStart())
    async def start(message: Message):
        owner=bool(message.from_user and message.from_user.id in owner_ids)
        await message.answer("به ربات مدیریت نمایندگی Rebecca خوش آمدید.",reply_markup=owner_menu(dry_run) if owner else customer_menu())
    @r.message()
    async def fallback(message: Message): await message.answer("این بخش از منوی اصلی قابل انتخاب است.")
    return r
