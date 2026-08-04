from pydantic import BaseModel


class UserRoleUpdate(BaseModel):
    role: str  # 'user', 'admin', 'super_admin'


class UserR18Update(BaseModel):
    enabled: bool


class UserContentVisibilityUpdate(BaseModel):
    r18_enabled: bool | None = None
    non_r18_enabled: bool | None = None
    can_manage_visibility: bool | None = None
