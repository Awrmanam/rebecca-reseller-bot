from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

class Admin(BaseModel):
    username: str
    role: str
    status: str = "active"
    expire: datetime | None = None
    data_limit: int = 0
    used_traffic: int = 0
    services: list[int | str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

class User(BaseModel):
    username: str
    admin_username: str | None = None
    admin_id: int | None = None
    status: str = "active"
    expire: datetime | None = None
    data_limit: int = 0
    used_traffic: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)
