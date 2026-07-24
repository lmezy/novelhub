from pydantic import BaseModel

from app.schemas.user import UserOut


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
