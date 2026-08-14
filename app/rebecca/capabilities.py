from dataclasses import asdict, dataclass

@dataclass(frozen=True)
class Capabilities:
    admin_create: bool = False
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
    def snapshot(self) -> dict[str, bool]:
        return asdict(self)
