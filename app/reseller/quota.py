from datetime import datetime, timedelta

def renewal_expiry(current: datetime | None, now: datetime, days: int) -> datetime:
    return max(current or now, now) + timedelta(days=days)
def renewal_data_limit(limit: int, used: int, purchased: int) -> int:
    return limit + purchased if limit > used else used + purchased
def exhausted(expire: datetime | None, limit: int, used: int, now: datetime) -> bool:
    return (expire is not None and expire <= now) or limit - used <= 0
