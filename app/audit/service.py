from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import AuditLog
async def audit(session: AsyncSession, action: str, target_type: str, target: str, result: str, *, actor: str="system", actor_type: str="SYSTEM", order_id: int|None=None, before: dict|None=None, after: dict|None=None, error: str|None=None) -> None:
    session.add(AuditLog(actor=actor, actor_type=actor_type, action=action, target_type=target_type, target_identifier=target, order_id=order_id, before_snapshot=before, after_snapshot=after, result=result, error=error))
