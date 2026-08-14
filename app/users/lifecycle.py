from dataclasses import dataclass
from datetime import datetime, timedelta
from app.rebecca.client import RebeccaClient
from app.rebecca.exceptions import RebeccaUnavailable, VerificationError
from app.reseller.quota import exhausted
from .ownership import verified_owner

@dataclass
class DeletePolicy:
    dry_run: bool=True; destructive_actions: bool=False; allow_delete_actions: bool=False

def deletion_time(detected: datetime, hours: int=72) -> datetime: return detected+timedelta(hours=hours)
async def safe_delete(client: RebeccaClient, username: str, owner: str, delete_after: datetime, now: datetime, policy: DeletePolicy, *, hold: bool=False, locked: bool=False) -> str:
    if hold: return "HELD"
    if now < delete_after: return "NOT_DUE"
    if policy.dry_run: return "WOULD_DELETE_USER"
    if not policy.destructive_actions or not policy.allow_delete_actions: return "DELETE_DISABLED"
    if locked: return "LOCKED"
    live=await client.get_user(username)  # final authoritative read; outage propagates safely
    if live is None: return "ALREADY_MISSING"
    if not verified_owner(live, owner): return "OWNERSHIP_MISMATCH"
    if not exhausted(live.expire, live.data_limit, live.used_traffic, now): return "RENEWED"
    await client.delete_user(username)
    if await client.get_user(username) is not None: raise VerificationError("delete not confirmed by not-found")
    return "DELETED"
