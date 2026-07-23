from pydantic import BaseModel



class BookCreate(BaseModel):


    title:str


    author_id:str|None=None


    source_id:str|None=None



class BookOut(BaseModel):


    id:str


    title:str


    description:str|None=None



    class Config:

        from_attributes=True
