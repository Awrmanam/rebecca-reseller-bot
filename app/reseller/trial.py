from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TrialRecord
from app.rebecca.exceptions import VerificationError


async def reserve_trial(
    session: AsyncSession,
    telegram_id: int,
    username: str,
    hours: int = 24,
) -> TrialRecord | None:
    record = TrialRecord(
        telegram_id=telegram_id,
        admin_username=username,
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
        status="PROVISIONING",
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    return record


def failure_status(error: Exception) -> str:
    """Only unsafe/invalid live state consumes a trial terminally."""
    return "FAILED" if isinstance(error, VerificationError) else "PROVISIONING"
