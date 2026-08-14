from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import TrialRecord

async def reserve_trial(session: AsyncSession, telegram_id: int, hours: int=24) -> TrialRecord | None:
    record=TrialRecord(telegram_id=telegram_id, expires_at=datetime.now(timezone.utc)+timedelta(hours=hours)); session.add(record)
    try: await session.flush()
    except IntegrityError: await session.rollback(); return None
    return record
