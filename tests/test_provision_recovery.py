from datetime import UTC, datetime

import pytest

from app.rebecca.models import Admin
from app.reseller.service import provision
from app.rebecca.exceptions import VerificationError
from tests.fakes import FakeRebecca


@pytest.mark.asyncio
async def test_retry_reuses_existing_admin_and_resets_password():
    expire = datetime.fromtimestamp(1_800_000_000, UTC)
    existing = Admin(
        username="durably-reserved",
        role="reseller",
        status="active",
        expire=expire,
        data_limit=100,
        services=[3],
        users_limit=5,
    )
    fake = FakeRebecca({"durably-reserved": existing})
    await provision(
        fake,
        username="durably-reserved",
        password="new-recovery-password",
        expire=expire,
        data_limit=100,
        services=[3],
        users_limit=5,
    )
    assert not any(item[0] == "create" for item in fake.mutations)
    updates = [item for item in fake.mutations if item[0] == "update"]
    assert len(updates) == 1
    assert updates[0][2]["password"] == "new-recovery-password"


@pytest.mark.asyncio
async def test_post_create_verification_includes_status_and_users_limit():
    expire = datetime.fromtimestamp(1_800_000_000, UTC)
    unsafe = Admin(
        username="reserved",
        role="reseller",
        status="disabled",
        expire=expire,
        data_limit=100,
        services=[3],
        users_limit=99,
    )
    fake = FakeRebecca({"reserved": unsafe})
    with pytest.raises(VerificationError):
        await provision(
            fake,
            username="reserved",
            password="recovery",
            expire=expire,
            data_limit=100,
            services=[3],
            users_limit=5,
        )
