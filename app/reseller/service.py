import secrets
from typing import Any
from app.rebecca.client import RebeccaClient
from app.rebecca.exceptions import VerificationError

RESTRICTED_PERMISSIONS={"users":{"create":True,"delete":True,"reset_usage":True,"revoke":True,"create_on_hold":True,"allow_unlimited_data":False,"allow_unlimited_expire":False,"allow_next_plan":False,"advanced_actions":False,"set_flow":False,"allow_custom_key":False},"admin_management":{"can_view":False,"can_edit":False,"can_manage_sudo":False,"manage_sessions":False,"manage_2fa":False},"sudo":{"all":False}}
def credentials() -> tuple[str,str]: return f"r{secrets.token_hex(6)}", secrets.token_urlsafe(20)
async def provision(client: RebeccaClient, *, username: str, password: str, expire: Any, data_limit: int, services: list[Any], telegram_id: int|None=None):
    payload={"username":username,"password":password,"role":"reseller","permissions":RESTRICTED_PERMISSIONS,"expire":expire,"data_limit":data_limit,"services":services,"telegram_id":telegram_id,"require_2fa":False}
    await client.create_reseller_admin(payload)
    live=await client.get_admin(username)
    if live is None or live.username != username or live.role != "reseller" or live.role in {"sudo","full_access"}: raise VerificationError("unsafe or unverifiable reseller role")
    if live.data_limit != data_limit or set(live.services) != set(services): raise VerificationError("limits/services verification failed")
    return live
