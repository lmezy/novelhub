from pydantic import BaseModel


class UserRoleUpdate(BaseModel):
    role: str  # 'user', 'admin', 'super_admin'
