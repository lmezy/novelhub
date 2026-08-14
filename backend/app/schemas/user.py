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
    show_covers: bool | None = None
    show_content_images: bool | None = None
    tap_actions: dict | None = None

    @field_validator("tap_actions")
    @classmethod
    def _validate_tap_actions(cls, value: dict | None) -> dict | None:
        if value is None:
            return value
        region_keys = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
        valid_actions = (
            "none",
            "menu",
            "prev_page",
            "next_page",
            "prev_chapter",
            "next_chapter",
        )
        defaults = {
            "tl": "prev_page",
            "tc": "prev_page",
            "tr": "next_page",
            "ml": "prev_page",
            "mc": "menu",
            "mr": "next_page",
            "bl": "prev_page",
            "bc": "next_page",
            "br": "next_page",
        }
        normalized: dict[str, str] = {}
        for key in region_keys:
            raw = value.get(key)
            action = str(raw).strip() if raw is not None else defaults[key]
            if action not in valid_actions:
                raise ValueError(
                    f"tap_actions.{key} must be one of: {', '.join(valid_actions)}"
                )
            normalized[key] = action
        if "menu" not in normalized.values():
            normalized["mc"] = "menu"
        return normalized


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateEmailRequest(BaseModel):
    email: EmailStr


class UpdateNicknameRequest(BaseModel):
    nickname: str

