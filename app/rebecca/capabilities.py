from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Capabilities:
    admin_create: bool = False
    admin_get: bool = False
    admin_update: bool = False
    admin_disable: bool = False
    admin_enable: bool = False
    admin_users_disable: bool = False
    admin_users_activate: bool = False
    admin_usage: bool = False
    user_list_by_owner: bool = False
    user_get: bool = False
    user_create: bool = False
    user_update: bool = False
    user_delete: bool = False
    user_reset: bool = False
    user_revoke: bool = False
    services_list: bool = False

    def snapshot(self) -> dict[str, bool]:
        return asdict(self)


# Ordered candidates are compatibility declarations, not request fallbacks.
# A route is selected only if that exact method/path appears in OpenAPI.
OPERATION_CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "admin_create": (("/api/admin", "post"),),
    "admin_get": (("/api/admins", "get"),),
    "admin_update": (("/api/admin/{username}", "put"),),
    "admin_disable": (("/api/admin/{username}/disable", "post"),),
    "admin_enable": (("/api/admin/{username}/enable", "post"),),
    "admin_users_disable": (("/api/admin/{username}/users/disable", "post"),),
    "admin_users_activate": (("/api/admin/{username}/users/activate", "post"),),
    "admin_usage": (
        ("/api/admin/usage/{username}", "get"),
        ("/api/admin/{username}/usage", "get"),
    ),
    "user_get": (("/api/user/{username}", "get"),),
    "user_create": (("/api/user", "post"),),
    "user_update": (("/api/user/{username}", "put"),),
    "user_delete": (("/api/user/{username}", "delete"),),
    "user_reset": (("/api/user/{username}/reset", "post"),),
    "user_revoke": (("/api/user/{username}/revoke_sub", "post"),),
    "services_list": (
        ("/api/v2/services", "get"),
        ("/api/services", "get"),
    ),
}


def resolve_operation_paths(schema: dict[str, Any]) -> dict[str, str]:
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return {}
    resolved: dict[str, str] = {}
    for name, candidates in OPERATION_CANDIDATES.items():
        for path, method in candidates:
            operation = paths.get(path)
            if isinstance(operation, dict) and method in operation:
                resolved[name] = path
                break
    return resolved


def from_openapi(schema: dict[str, Any]) -> Capabilities:
    paths = schema.get("paths", {})
    resolved = resolve_operation_paths(schema)
    detected = {name: name in resolved for name in OPERATION_CANDIDATES}
    # Owner filtering is verified only when /api/users has an owner parameter.
    users_get = paths.get("/api/users", {}).get("get", {})
    parameters = users_get.get("parameters", [])
    detected["user_list_by_owner"] = any(
        item.get("name") in {"admin_username", "admin_id", "owner"}
        for item in parameters
    )
    return Capabilities(**detected)
