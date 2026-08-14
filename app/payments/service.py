import asyncio
from datetime import datetime, timezone
from app.database.models import OrderStatus, ResellerStatus
from app.rebecca.client import RebeccaClient
from app.rebecca.exceptions import VerificationError
from app.reseller.quota import renewal_data_limit, renewal_expiry
_locks: dict[int,asyncio.Lock]={}

async def approve_card(order) -> bool:
    if order.status in {OrderStatus.PAID,OrderStatus.APPLYING,OrderStatus.APPLIED}: return False
    if order.status != OrderStatus.WAITING_RECEIPT: return False
    order.status=OrderStatus.PAID; order.paid_at=datetime.now(timezone.utc); return True
async def apply_order(order, reseller, product, client: RebeccaClient, now: datetime) -> bool:
    async with _locks.setdefault(order.id,asyncio.Lock()):
        if order.status == OrderStatus.APPLIED: return False
        if reseller.status == ResellerStatus.SUSPENDED: return False
        live=await client.get_admin(reseller.rebecca_admin_username)
        if live is None: raise VerificationError("reseller missing")
        target_limit=renewal_data_limit(live.data_limit,live.used_traffic,product.traffic_gb*1024**3); target_expire=renewal_expiry(live.expire,now,product.duration_days)
        before=live.model_dump(mode="json"); order.before_snapshot=before; order.status=OrderStatus.APPLYING
        # Crash recovery: if the exact recorded target is already live, do not add again.
        previous_target=(order.after_snapshot or {}).get("target")
        if previous_target and live.data_limit == previous_target["data_limit"] and live.expire == datetime.fromisoformat(previous_target["expire"]):
            order.status=OrderStatus.APPLIED; order.applied_at=now; return True
        order.after_snapshot={"target":{"data_limit":target_limit,"expire":target_expire.isoformat()}}
        try: await client.update_admin(live.username,{"data_limit":target_limit,"expire":target_expire.isoformat()})
        except Exception as exc: order.status=OrderStatus.PAID; order.apply_error=str(exc); raise
        verified=await client.get_admin(live.username)
        if verified is None or verified.data_limit != target_limit or verified.expire != target_expire:
            order.status=OrderStatus.PAID; raise VerificationError("post-update entitlement mismatch")
        order.status=OrderStatus.APPLIED; order.applied_at=now; order.after_snapshot=verified.model_dump(mode="json"); return True
