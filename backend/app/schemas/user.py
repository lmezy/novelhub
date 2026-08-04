from pydantic import BaseModel,EmailStr



class UserCreate(BaseModel):

    username:str

    email:EmailStr

    password:str



class UserLogin(BaseModel):

    username:str

    password:str



class UserOut(BaseModel):

    id:str

    username:str

    email:str

    role: str

    r18_enabled: bool = False
    non_r18_enabled: bool = True
    can_manage_visibility: bool = False

    class Config:
        from_attributes = True


class UserSelfVisibilityUpdate(BaseModel):
    r18_enabled: bool | None = None
    non_r18_enabled: bool | None = None


    class Config:

        from_attributes=True

