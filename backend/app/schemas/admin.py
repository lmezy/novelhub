import re

from pydantic import BaseModel, EmailStr, field_validator

from app.services.validation import password_error, username_error


class UserRoleUpdate(BaseModel):
    role: str  # 'user', 'admin', 'super_admin'


class UserR18Update(BaseModel):
    enabled: bool


class UserContentVisibilityUpdate(BaseModel):
    r18_enabled: bool | None = None
    non_r18_enabled: bool | None = None
    can_manage_visibility: bool | None = None


class AdminUserCreate(BaseModel):
    username: str
    email: EmailStr | None = None
    password: str
    role: str = "user"

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


class UserPasswordUpdate(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        error = password_error(value)
        if error:
            raise ValueError(error)
        return value


class RegistrationApprovalUpdate(BaseModel):
    enabled: bool


class AutoSyncSettingsUpdate(BaseModel):
    enabled: bool
    time: str = "03:00"
    # 0 = "every day at `time`"; >0 = "every N hours".
    interval_hours: int | None = None

    @field_validator("time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("time must be in HH:MM format")
        return value

    @field_validator("interval_hours")
    @classmethod
    def _validate_interval(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0 or value > 168:
            raise ValueError("interval_hours must be between 0 and 168")
        return value
