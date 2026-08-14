from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TrialRecord
from app.database.models import AuditLog
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


async def record_dry_run_request(session: AsyncSession, telegram_id: int) -> None:
    session.add(
        AuditLog(
            actor=str(telegram_id),
            actor_type="TELEGRAM_USER",
            action="WOULD_PROVISION_TRIAL",
            target_type="trial",
            target_identifier=str(telegram_id),
            result="DRY_RUN",
        )
    )
    await session.flush()
