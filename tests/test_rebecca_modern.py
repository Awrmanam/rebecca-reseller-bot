from datetime import UTC, datetime

import httpx
import pytest

from app.rebecca.capabilities import from_openapi
from app.rebecca.client import HTTPRebeccaClient
from app.rebecca.models import Admin, serialize_expire


def test_modern_admin_maps_users_usage_and_unix_expire():
    admin = Admin.from_rebecca(
        {
            "username": "seller",
            "role": "reseller",
            "status": "active",
            "users_usage": 1234,
            "data_limit": 9000,
            "expire": 1_800_000_000,
            "services": [1, 2],
        }
    )
    assert admin.used_traffic == 1234
    assert admin.expire == datetime.fromtimestamp(1_800_000_000, UTC)
    assert serialize_expire(admin.expire) == 1_800_000_000


def test_openapi_capabilities_include_parameterized_routes():
    schema = {
        "paths": {
            "/api/admins": {"get": {}},
            "/api/admin": {"post": {}},
            "/api/admin/{username}": {"put": {}},
            "/api/admin/{username}/disable": {"post": {}},
            "/api/user/{username}": {"get": {}, "put": {}, "delete": {}},
            "/api/user/{username}/reset": {"post": {}},
            "/api/user/{username}/revoke_sub": {"post": {}},
            "/api/users": {
                "get": {"parameters": [{"name": "admin_username", "in": "query"}]}
            },
            "/api/services": {"get": {}},
        }
    }
    caps = from_openapi(schema)
    assert caps.admin_get and caps.admin_create and caps.admin_update
    assert caps.admin_disable and caps.user_get and caps.user_update and caps.user_delete
    assert caps.user_reset and caps.user_revoke and caps.user_list_by_owner
    assert caps.services_list


@pytest.mark.asyncio
async def test_admin_lookup_uses_filtered_admins_route():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "admins": [
                    {
                        "username": "seller",
                        "role": "reseller",
                        "users_usage": 50,
                        "data_limit": 100,
                        "expire": 1_800_000_000,
                        "services": [],
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://panel.test", transport=transport)
    client = HTTPRebeccaClient("https://panel.test", "redacted", http)
    client.capabilities = client.capabilities.__class__(admin_get=True)
    admin = await client.get_admin("seller")
    assert admin and admin.used_traffic == 50
    assert requests[0].url.path == "/api/admins"
    assert requests[0].url.params["username"] == "seller"


@pytest.mark.asyncio
async def test_admin_write_serializes_expire_as_unix():
    captured = {}

    def handler(request: httpx.Request):
        if request.method == "PUT":
            import json
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"admins": [{"username": "r", "role": "reseller"}]})

    http = httpx.AsyncClient(base_url="https://panel.test", transport=httpx.MockTransport(handler))
    client = HTTPRebeccaClient("https://panel.test", "redacted", http)
    client.capabilities = client.capabilities.__class__(admin_update=True, admin_get=True)
    await client.update_admin("r", {"expire": datetime.fromtimestamp(1_800_000_000, UTC)})
    assert captured["expire"] == 1_800_000_000
