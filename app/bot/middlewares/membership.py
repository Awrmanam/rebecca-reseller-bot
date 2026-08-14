from typing import Protocol
class MembershipBot(Protocol):
    async def get_chat_member(self, chat_id: str, user_id: int): ...
async def is_member(bot: MembershipBot, user_id: int, channels: list[str]) -> bool:
    for channel in channels:
        try: member=await bot.get_chat_member(channel, user_id)
        except Exception: return False
        if getattr(member, "status", "left") in {"left","kicked"}: return False
    return True
