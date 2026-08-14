from datetime import datetime, timedelta

def renewal_expiry(current: datetime | None, now: datetime, days: int) -> datetime:
    return max(current or now, now) + timedelta(days=days)
def renewal_data_limit(limit: int | None, used: int, purchased: int) -> int:
    if limit is None or limit <= 0:
        return used + purchased
    return limit + purchased if limit > used else used + purchased
def exhausted(expire: datetime | None, limit: int | None, used: int, now: datetime) -> bool:
    time_exhausted = expire is not None and expire <= now
    # Rebecca zero/null is unlimited. Unknown/ambiguous limits therefore fail
    # closed rather than authorizing an automatic destructive action.
    traffic_exhausted = limit is not None and limit > 0 and limit - used <= 0
    return time_exhausted or traffic_exhausted
