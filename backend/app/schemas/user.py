from pydantic import BaseModel, EmailStr, field_validator

from app.services.validation import password_error, username_error



class UserCreate(BaseModel):

    username:str

    email: EmailStr | None = None

    password:str

    invite_code: str | None = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        error = username_error(value)
        if error:
            raise ValueError(error)
        return value

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        error = password_error(value)
        if error:
            raise ValueError(error)
        return value



class UserLogin(BaseModel):

    username:str

    password:str



class UserOut(BaseModel):

    id:str

    username:str
    nickname: str | None = None

    email: str | None = None

    role: str

    r18_enabled: bool = False
    non_r18_enabled: bool = True
    can_manage_visibility: bool = False
    approved: bool = True
    invite_code: str = ""
    invited_by_id: str | None = None
    settings: dict = {}

    class Config:
        from_attributes = True


class AdminUserOut(UserOut):
    invite_tag: str | None = None


class UserSelfVisibilityUpdate(BaseModel):
    r18_enabled: bool | None = None
    non_r18_enabled: bool | None = None


    class Config:

        from_attributes=True


class UserSettingsUpdate(BaseModel):
    font: str | None = None
    font_size: int | None = None
    language: str | None = None
    theme: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateEmailRequest(BaseModel):
    email: EmailStr


class UpdateNicknameRequest(BaseModel):
    nickname: str

