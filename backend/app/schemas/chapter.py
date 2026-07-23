from pydantic import BaseModel



class ChapterOut(BaseModel):


    id:str


    title:str


    chapter_number:int


    content_path:str



    class Config:

        from_attributes=True
