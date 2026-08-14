from abc import ABC, abstractmethod
from typing import Any
import httpx
from .capabilities import Capabilities
from .exceptions import CapabilityMissing, NotFound, RebeccaError, RebeccaUnavailable
from .models import Admin, User

class RebeccaClient(ABC):
    @abstractmethod
    async def health_check(self) -> bool: ...
    @abstractmethod
    async def detect_capabilities(self) -> Capabilities: ...
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
    @abstractmethod
    async def list_admin_users(self, username: str) -> list[User]: ...
    @abstractmethod
    async def get_user(self, username: str) -> User | None: ...
    @abstractmethod
    async def update_user(self, username: str, payload: dict[str, Any]) -> User: ...
    @abstractmethod
    async def delete_user(self, username: str) -> None: ...

class HTTPRebeccaClient(RebeccaClient):
    """Only verified paths live here; unsupported operations fail closed."""
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None):
        self.http = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=15)
        self.capabilities = Capabilities()
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try: response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc: raise RebeccaUnavailable(str(exc)) from exc
        if response.status_code == 404: raise NotFound(path)
        if response.is_error: raise RebeccaError(f"Rebecca HTTP {response.status_code}")
        return response.json() if response.content else None
    async def health_check(self) -> bool:
        try: await self._request("GET", "/api/admin")
        except RebeccaError: return False
        return True
    async def detect_capabilities(self) -> Capabilities:
        # OPTIONS is observational. A capability is true only when explicitly advertised.
        mapping = {"/api/admin": ("POST", "admin_create"), "/api/user": ("POST", "user_create")}
        found: dict[str, bool] = {}
        for path, (verb, name) in mapping.items():
            try:
                response = await self.http.options(path)
                found[name] = verb in response.headers.get("allow", "").upper().split(", ")
            except httpx.HTTPError: found[name] = False
        # Parameterized modern endpoints cannot be safely probed without a real identifier.
        self.capabilities = Capabilities(**found)
        return self.capabilities
    def _require(self, name: str) -> None:
        if not getattr(self.capabilities, name): raise CapabilityMissing(name)
    async def get_admin(self, username: str) -> Admin | None:
        try: data = await self._request("GET", f"/api/admin/{username}")
        except NotFound: return None
        return Admin.model_validate({**data, "raw": data})
    async def create_reseller_admin(self, payload: dict[str, Any]) -> Admin:
        self._require("admin_create")
        if payload.get("role") != "reseller": raise ValueError("reseller role is mandatory")
        data = await self._request("POST", "/api/admin", json=payload)
        return Admin.model_validate({**data, "raw": data})
    async def update_admin(self, username: str, payload: dict[str, Any]) -> Admin:
        self._require("admin_update"); data = await self._request("PUT", f"/api/admin/{username}", json=payload); return Admin.model_validate({**data, "raw": data})
    async def _admin_action(self, username: str, action: str, capability: str) -> None:
        self._require(capability); await self._request("POST", f"/api/admin/{username}/{action}")
    async def disable_admin(self, username: str) -> None: await self._admin_action(username, "disable", "admin_disable")
    async def enable_admin(self, username: str) -> None: await self._admin_action(username, "enable", "admin_enable")
    async def disable_admin_users(self, username: str) -> None: await self._admin_action(username, "users/disable", "admin_users_disable")
    async def activate_admin_users(self, username: str) -> None: await self._admin_action(username, "users/activate", "admin_users_activate")
    async def list_admin_users(self, username: str) -> list[User]:
        self._require("user_list_by_owner"); data = await self._request("GET", "/api/users", params={"admin_username": username}); return [User.model_validate({**x, "raw": x}) for x in data]
    async def get_user(self, username: str) -> User | None:
        self._require("user_get")
        try: data = await self._request("GET", f"/api/user/{username}")
        except NotFound: return None
        return User.model_validate({**data, "raw": data})
    async def update_user(self, username: str, payload: dict[str, Any]) -> User:
        self._require("user_update"); await self.get_user(username); data = await self._request("PUT", f"/api/user/{username}", json=payload); result = await self.get_user(username); return result or User.model_validate(data)
    async def delete_user(self, username: str) -> None:
        self._require("user_delete"); await self.get_user(username); await self._request("DELETE", f"/api/user/{username}")
