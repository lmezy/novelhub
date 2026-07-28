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


    class Config:

        from_attributes=True

