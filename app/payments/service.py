from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.database.models import OrderStatus, ResellerStatus
from app.rebecca.client import RebeccaClient
from app.rebecca.exceptions import VerificationError
from app.rebecca.models import Admin
from app.reseller.quota import renewal_data_limit, renewal_expiry

_locks: dict[int, asyncio.Lock] = {}


class AmbiguousEntitlementState(VerificationError):
    """Live state is neither the immutable before snapshot nor its target."""


async def approve_card(order) -> bool:
    if order.status in {OrderStatus.PAID, OrderStatus.APPLYING, OrderStatus.APPLIED}:
        return False
    if order.status != OrderStatus.WAITING_RECEIPT:
        return False
    order.status = OrderStatus.PAID
    order.paid_at = datetime.now(timezone.utc)
    return True


def entitlement_snapshot(admin: Admin) -> dict[str, Any]:
    return {
        "data_limit": admin.data_limit,
        "expire": admin.expire.isoformat() if admin.expire else None,
        "services": sorted(str(item) for item in admin.services),
        "users_limit": admin.users_limit,
        "role": admin.role,
        "status": admin.status.lower(),
    }


def _target_snapshot(admin: Admin, product, now: datetime) -> dict[str, Any]:
    return {
        "data_limit": renewal_data_limit(
            admin.data_limit, admin.used_traffic, product.traffic_gb * 1024**3
        ),
        "expire": renewal_expiry(admin.expire, now, product.duration_days).isoformat(),
        "services": list(getattr(product, "service_ids", [])),
        "users_limit": getattr(product, "users_limit", None),
        "role": "reseller",
        "status": "active",
    }


def _matches(live: Admin, snapshot: dict[str, Any]) -> bool:
    actual = entitlement_snapshot(live)
    expected = dict(snapshot)
    expected["services"] = sorted(str(item) for item in snapshot.get("services", []))
    expected["status"] = str(snapshot.get("status", "")).lower()
    return actual == expected


async def apply_order(
    order,
    reseller,
    product,
    client: RebeccaClient,
    now: datetime,
    persist_target: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    async with _locks.setdefault(order.id, asyncio.Lock()):
        if order.status == OrderStatus.APPLIED:
            return False
        if reseller.status == ResellerStatus.SUSPENDED:
            return False

        live = await client.get_admin(reseller.rebecca_admin_username)
        if live is None:
            raise VerificationError("reseller missing")

        persisted_target = (order.after_snapshot or {}).get("target")
        if persisted_target is None:
            # Calculate exactly once. Both snapshots become durable before the
            # first external mutation.
            before = entitlement_snapshot(live)
            target = _target_snapshot(live, product, now)
            order.before_snapshot = before
            order.after_snapshot = {"target": target}
            order.status = OrderStatus.APPLYING
            order.apply_error = None
            if persist_target is not None:
                await persist_target()
        else:
            # Never recalculate or overwrite a target during recovery.
            before = order.before_snapshot
            target = persisted_target
            if not isinstance(before, dict):
                order.apply_error = "missing immutable before snapshot"
                raise AmbiguousEntitlementState(order.apply_error)
            if _matches(live, target):
                order.status = OrderStatus.APPLIED
                order.applied_at = now
                order.apply_error = None
                if reseller.status in {ResellerStatus.EXPIRED, ResellerStatus.DISABLED}:
                    reseller.status = ResellerStatus.ACTIVE
                return True
            if not _matches(live, before):
                order.apply_error = "live Rebecca entitlement is partial or ambiguous"
                raise AmbiguousEntitlementState(order.apply_error)

        target_expire = datetime.fromisoformat(target["expire"])
        try:
            await client.update_admin(
                live.username,
                {
                    "data_limit": target["data_limit"],
                    "expire": target_expire,
                    "services": target["services"],
                    "users_limit": target["users_limit"],
                },
            )
            if reseller.status in {ResellerStatus.EXPIRED, ResellerStatus.DISABLED}:
                await client.enable_admin(live.username)
        except Exception as exc:
            # APPLYING and the immutable snapshots remain durable. A retry can
            # distinguish no-op, completed, and ambiguous partial outcomes.
            order.apply_error = str(exc)
            raise

        verified = await client.get_admin(live.username)
        if verified is None or not _matches(verified, target):
            order.apply_error = "post-update entitlement mismatch"
            raise VerificationError(order.apply_error)

        if reseller.status in {ResellerStatus.EXPIRED, ResellerStatus.DISABLED}:
            reseller.status = ResellerStatus.ACTIVE
        order.status = OrderStatus.APPLIED
        order.applied_at = now
        order.apply_error = None
        return True
