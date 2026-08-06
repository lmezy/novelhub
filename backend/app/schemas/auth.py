from typing import Literal

from pydantic import BaseModel

from app.schemas.user import UserOut


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RegisterResult(BaseModel):
    status: Literal["approved", "pending"]
    access_token: str | None = None
    token_type: str = "bearer"
    user: UserOut
