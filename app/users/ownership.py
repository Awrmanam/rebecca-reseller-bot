from app.rebecca.models import User
def verified_owner(user: User, admin_username: str) -> bool:
    return bool(user.admin_username) and user.admin_username == admin_username
