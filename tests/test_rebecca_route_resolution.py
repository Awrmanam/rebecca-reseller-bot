import httpx
import pytest

from app.rebecca.capabilities import from_openapi, resolve_operation_paths
from app.rebecca.client import HTTPRebeccaClient
from app.rebecca.exceptions import NotFound, RebeccaError


def schema(*paths):
    return {"paths": {path: {"get": {}} for path in paths}}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/admin/usage/{username}", True),
        ("/api/admin/{username}/usage", True),
        (None, False),
    ],
)
def test_admin_usage_capability_routes(path, expected):
    document = schema(*([path] if path else []))
    assert from_openapi(document).admin_usage is expected
    resolved = resolve_operation_paths(document)
    assert resolved.get("admin_usage") == path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v2/services", True),
        ("/api/services", True),
        (None, False),
    ],
)
def test_services_capability_routes(path, expected):
    document = schema(*([path] if path else []))
    assert from_openapi(document).services_list is expected
    resolved = resolve_operation_paths(document)
    assert resolved.get("services_list") == path


def test_modern_route_wins_only_when_both_are_advertised():
    document = schema(
        "/api/admin/usage/{username}",
        "/api/admin/{username}/usage",
        "/api/v2/services",
        "/api/services",
    )
    resolved = resolve_operation_paths(document)
    assert resolved["admin_usage"] == "/api/admin/usage/{username}"
    assert resolved["services_list"] == "/api/v2/services"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template", "expected_path"),
    [
        ("/api/admin/usage/{username}", "/api/admin/usage/seller"),
        ("/api/admin/{username}/usage", "/api/admin/seller/usage"),
    ],
)
async def test_admin_usage_calls_only_advertised_path(template, expected_path):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"users_usage": 12})

    http = httpx.AsyncClient(
        base_url="https://panel.test", transport=httpx.MockTransport(handler)
    )
    client = HTTPRebeccaClient("https://panel.test", "token", http)
    client.capabilities = client.capabilities.__class__(admin_usage=True)
    client.operation_paths = {"admin_usage": template}
    assert await client.get_admin_usage("seller") == {"users_usage": 12}
    assert requests == [expected_path]


@pytest.mark.asyncio
async def test_failed_advertised_route_has_no_speculative_fallback():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(404)

    http = httpx.AsyncClient(
        base_url="https://panel.test", transport=httpx.MockTransport(handler)
    )
    client = HTTPRebeccaClient("https://panel.test", "token", http)
    client.capabilities = client.capabilities.__class__(services_list=True)
    client.operation_paths = {"services_list": "/api/v2/services"}
    with pytest.raises(NotFound):
        await client.list_services()
    assert requests == ["/api/v2/services"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [{"id": 1, "name": "Lite"}],
        {"services": [{"id": 1, "name": "Lite"}]},
        {"items": [{"id": 1, "name": "Lite"}]},
    ],
)
async def test_services_response_normalization(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(
        base_url="https://panel.test", transport=httpx.MockTransport(handler)
    )
    client = HTTPRebeccaClient("https://panel.test", "token", http)
    client.capabilities = client.capabilities.__class__(services_list=True)
    client.operation_paths = {"services_list": "/api/v2/services"}
    assert await client.list_services() == [{"id": 1, "name": "Lite"}]


@pytest.mark.asyncio
async def test_services_rejects_incompatible_wrapper():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": 1}]})

    http = httpx.AsyncClient(
        base_url="https://panel.test", transport=httpx.MockTransport(handler)
    )
    client = HTTPRebeccaClient("https://panel.test", "token", http)
    client.capabilities = client.capabilities.__class__(services_list=True)
    client.operation_paths = {"services_list": "/api/v2/services"}
    with pytest.raises(RebeccaError):
        await client.list_services()
