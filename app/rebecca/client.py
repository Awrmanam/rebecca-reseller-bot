from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from .capabilities import Capabilities, from_openapi, resolve_operation_paths
from .exceptions import CapabilityMissing, NotFound, RebeccaError, RebeccaUnavailable
from .models import Admin, User, serialize_expire


class RebeccaClient(ABC):
    @abstractmethod
    async def health_check(self) -> bool: ...
    @abstractmethod
    async def detect_capabilities(self) -> Capabilities: ...
    async def get_current_admin(self) -> Admin | None: raise NotImplementedError
    @abstractmethod
    async def get_admin(self, username: str) -> Admin | None: ...
    @abstractmethod
    async def create_reseller_admin(self, payload: dict[str, Any]) -> Admin: ...
    @abstractmethod
    async def update_admin(self, username: str, payload: dict[str, Any]) -> Admin: ...
    @abstractmethod
    async def disable_admin(self, username: str) -> None: ...
    @abstractmethod
    async def enable_admin(self, username: str) -> None: ...
    @abstractmethod
    async def disable_admin_users(self, username: str) -> None: ...
    @abstractmethod
    async def activate_admin_users(self, username: str) -> None: ...
    async def get_admin_usage(self, username: str) -> dict[str, Any]: raise NotImplementedError
    @abstractmethod
    async def list_admin_users(self, username: str) -> list[User]: ...
    @abstractmethod
    async def get_user(self, username: str) -> User | None: ...
    async def create_user(self, payload: dict[str, Any]) -> User: raise NotImplementedError
    @abstractmethod
    async def update_user(self, username: str, payload: dict[str, Any]) -> User: ...
    async def disable_user(self, username: str) -> User:
        return await self.update_user(username, {"status": "disabled"})
    async def enable_user(self, username: str) -> User:
        return await self.update_user(username, {"status": "active"})
    @abstractmethod
    async def delete_user(self, username: str) -> None: ...
    async def reset_user_usage(self, username: str) -> None: raise NotImplementedError
    async def revoke_subscription(self, username: str) -> None: raise NotImplementedError
    async def list_services(self) -> list[dict[str, Any]]: raise NotImplementedError


class HTTPRebeccaClient(RebeccaClient):
    """Rebecca's verified HTTP surface. No database or guessed-route fallback exists."""

    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None):
        self.http = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        self.capabilities = Capabilities()
        self.operation_paths: dict[str, str] = {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RebeccaUnavailable(str(exc)) from exc
        if response.status_code == 404:
            raise NotFound(path)
        if response.is_error:
            raise RebeccaError(f"Rebecca HTTP {response.status_code}")
        return response.json() if response.content else None

    def _require(self, name: str) -> None:
        if not getattr(self.capabilities, name):
            raise CapabilityMissing(name)

    async def health_check(self) -> bool:
        try:
            await self._request("GET", "/openapi.json")
            return True
        except RebeccaError:
            return False

    async def detect_capabilities(self) -> Capabilities:
        try:
            schema = await self._request("GET", "/openapi.json")
        except (RebeccaError, RebeccaUnavailable):
            self.capabilities = Capabilities()
            self.operation_paths = {}
        else:
            self.capabilities = from_openapi(schema)
            self.operation_paths = resolve_operation_paths(schema)
        return self.capabilities

    async def get_current_admin(self) -> Admin | None:
        data = await self._request("GET", "/api/admin")
        return Admin.from_rebecca(data) if data else None

    async def get_admin(self, username: str) -> Admin | None:
        self._require("admin_get")
        data = await self._request("GET", "/api/admins", params={"username": username})
        rows = data.get("admins", data.get("items", data)) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise RebeccaError("unexpected /api/admins response")
        exact = next((row for row in rows if row.get("username") == username), None)
        return Admin.from_rebecca(exact) if exact else None

    async def create_reseller_admin(self, payload: dict[str, Any]) -> Admin:
        self._require("admin_create")
        if payload.get("role") != "reseller":
            raise ValueError("reseller role is mandatory")
        data = await self._request("POST", "/api/admin", json=self._admin_payload(payload))
        return Admin.from_rebecca(data)

    @staticmethod
    def _admin_payload(payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(payload)
        if "expire" in result:
            result["expire"] = serialize_expire(result["expire"])
        return result

    async def update_admin(self, username: str, payload: dict[str, Any]) -> Admin:
        self._require("admin_update")
        await self._request("PUT", f"/api/admin/{username}", json=self._admin_payload(payload))
        live = await self.get_admin(username)
        if live is None:
            raise RebeccaError("admin disappeared after update")
        return live

    async def _admin_action(self, username: str, action: str, capability: str) -> None:
        self._require(capability)
        await self._request("POST", f"/api/admin/{username}/{action}")

    async def disable_admin(self, username: str) -> None:
        await self._admin_action(username, "disable", "admin_disable")
    async def enable_admin(self, username: str) -> None:
        await self._admin_action(username, "enable", "admin_enable")
    async def disable_admin_users(self, username: str) -> None:
        await self._admin_action(username, "users/disable", "admin_users_disable")
    async def activate_admin_users(self, username: str) -> None:
        await self._admin_action(username, "users/activate", "admin_users_activate")

    def _advertised_path(self, capability: str, **parameters: str) -> str:
        self._require(capability)
        template = self.operation_paths.get(capability)
        if template is None:
            raise CapabilityMissing(f"{capability}: advertised route unavailable")
        return template.format(**parameters)

    async def get_admin_usage(self, username: str) -> dict[str, Any]:
        path = self._advertised_path("admin_usage", username=username)
        data = await self._request("GET", path)
        if not isinstance(data, dict):
            raise RebeccaError("unexpected admin usage response")
        return data

    async def list_admin_users(self, username: str) -> list[User]:
        self._require("user_list_by_owner")
        data = await self._request("GET", "/api/users", params={"admin_username": username})
        rows = data.get("users", data.get("items", data)) if isinstance(data, dict) else data
        return [User.from_rebecca(item) for item in rows]

    async def get_user(self, username: str) -> User | None:
        self._require("user_get")
        try:
            data = await self._request("GET", f"/api/user/{username}")
        except NotFound:
            return None
        return User.from_rebecca(data)

    async def create_user(self, payload: dict[str, Any]) -> User:
        self._require("user_create")
        data = await self._request("POST", "/api/user", json=payload)
        return User.from_rebecca(data)

    async def update_user(self, username: str, payload: dict[str, Any]) -> User:
        self._require("user_update")
        if await self.get_user(username) is None:
            raise NotFound(username)
        await self._request("PUT", f"/api/user/{username}", json=payload)
        live = await self.get_user(username)
        if live is None:
            raise RebeccaError("user disappeared after update")
        return live

    async def delete_user(self, username: str) -> None:
        self._require("user_delete")
        if await self.get_user(username) is None:
            raise NotFound(username)
        await self._request("DELETE", f"/api/user/{username}")

    async def reset_user_usage(self, username: str) -> None:
        self._require("user_reset")
        if await self.get_user(username) is None:
            raise NotFound(username)
        await self._request("POST", f"/api/user/{username}/reset")

    async def revoke_subscription(self, username: str) -> None:
        self._require("user_revoke")
        if await self.get_user(username) is None:
            raise NotFound(username)
        await self._request("POST", f"/api/user/{username}/revoke_sub")

    async def list_services(self) -> list[dict[str, Any]]:
        path = self._advertised_path("services_list")
        data = await self._request("GET", path)
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            if isinstance(data.get("services"), list):
                rows = data["services"]
            elif isinstance(data.get("items"), list):
                rows = data["items"]
            else:
                raise RebeccaError("unexpected services response wrapper")
        else:
            raise RebeccaError("unexpected services response")
        if not all(isinstance(item, dict) for item in rows):
            raise RebeccaError("services response contains non-object entries")
        return rows
