from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from app.rebecca.client import RebeccaClient
from app.rebecca.exceptions import VerificationError
from app.rebecca.models import Admin, parse_expire

RESTRICTED_PERMISSIONS = {
    "users": {
        "create": True,
        "delete": True,
        "reset_usage": True,
        "revoke": True,
        "create_on_hold": True,
        "allow_unlimited_data": False,
        "allow_unlimited_expire": False,
        "allow_next_plan": False,
        "advanced_actions": False,
        "set_flow": False,
        "allow_custom_key": False,
    },
    "admin_management": {
        "can_view": False,
        "can_edit": False,
        "can_manage_sudo": False,
        "manage_sessions": False,
        "manage_2fa": False,
    },
    "sudo": {"all": False},
}


def credentials() -> tuple[str, str]:
    return f"r{secrets.token_hex(6)}", secrets.token_urlsafe(20)


def _same_second(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return int(left.timestamp()) == int(right.timestamp())


def verify_entitlement(
    live: Admin | None,
    *,
    username: str,
    expire: datetime,
    data_limit: int,
    services: list[Any],
    users_limit: int | None,
    require_active: bool = True,
) -> Admin:
    if live is None or live.username != username:
        raise VerificationError("admin identity verification failed")
    if live.role != "reseller" or live.role in {"sudo", "full_access"}:
        raise VerificationError("unsafe reseller role")
    if require_active and live.status.lower() not in {"active", "enabled"}:
        raise VerificationError("reseller is not active")
    if not _same_second(live.expire, parse_expire(expire)):
        raise VerificationError("expire verification failed")
    if live.data_limit != data_limit:
        raise VerificationError("data limit verification failed")
    if set(live.services) != set(services):
        raise VerificationError("services verification failed")
    if live.users_limit != users_limit:
        raise VerificationError("users limit verification failed")
    return live


async def provision(
    client: RebeccaClient,
    *,
    username: str,
    password: str,
    expire: datetime,
    data_limit: int,
    services: list[Any],
    telegram_id: int | None = None,
    users_limit: int | None = None,
) -> Admin:
    """Create or reconcile one durably reserved Rebecca username.

    Callers must persist ``username`` before invoking this function. If the
    prior process created the account and crashed, the same username is found,
    its password is safely reset through the verified update operation, and no
    second admin is created.
    """
    payload = {
        "username": username,
        "password": password,
        "role": "reseller",
        "permissions": RESTRICTED_PERMISSIONS,
        "expire": expire,
        "data_limit": data_limit,
        "services": services,
        "users_limit": users_limit,
        "telegram_id": telegram_id,
        "require_2fa": False,
    }
    live = await client.get_admin(username)
    if live is None:
        await client.create_reseller_admin(payload)
    else:
        if live.role in {"sudo", "full_access"}:
            try:
                await client.disable_admin(username)
            except Exception:
                pass
            raise VerificationError("unsafe reseller role")
        # A password generated before a crash was never stored. Recovery uses
        # the verified admin update operation to issue a new one, rather than
        # creating another admin or storing plaintext credentials.
        recovery_payload = {key: value for key, value in payload.items() if key != "username"}
        # CapabilityMissing remains retryable. It must not be converted into a
        # terminal VerificationError that consumes a trial.
        await client.update_admin(username, recovery_payload)
    live = await client.get_admin(username)
    try:
        return verify_entitlement(
            live,
            username=username,
            expire=expire,
            data_limit=data_limit,
            services=services,
            users_limit=users_limit,
        )
    except VerificationError:
        if live is not None and live.role in {"sudo", "full_access"}:
            try:
                await client.disable_admin(username)
            except Exception:
                pass
        raise
