from app.rebecca.capabilities import Capabilities
from app.rebecca.client import RebeccaClient
class FakeRebecca(RebeccaClient):
 def __init__(self,admins=None,users=None): self.admins=admins or {}; self.users=users or {}; self.mutations=[]; self.fail_update=False
 async def health_check(self): return True
 async def detect_capabilities(self): return Capabilities(**{k:True for k in Capabilities().__dict__})
 async def get_admin(self,u): return self.admins.get(u)
 async def create_reseller_admin(self,p): self.mutations.append(("create",p)); return self.admins[p["username"]]
 async def update_admin(self,u,p):
  if self.fail_update: raise RuntimeError("down")
  self.mutations.append(("update",u,p)); a=self.admins[u]; a.data_limit=p.get("data_limit",a.data_limit)
  if "expire" in p:
   from datetime import datetime
   a.expire=datetime.fromisoformat(p["expire"])
  return a
 async def disable_admin(self,u): self.mutations.append(("disable_admin",u))
 async def enable_admin(self,u): self.mutations.append(("enable_admin",u))
 async def disable_admin_users(self,u): self.mutations.append(("disable_users",u))
 async def activate_admin_users(self,u): self.mutations.append(("activate_users",u))
 async def list_admin_users(self,u): return [x for x in self.users.values() if x.admin_username==u]
 async def get_user(self,u): return self.users.get(u)
 async def update_user(self,u,p): self.mutations.append(("update_user",u,p)); return self.users[u]
 async def delete_user(self,u): self.mutations.append(("delete",u)); self.users.pop(u,None)
