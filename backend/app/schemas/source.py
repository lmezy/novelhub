from pydantic import BaseModel, HttpUrl


class SourceCreate(BaseModel):
    id: str
    name: str
    url: HttpUrl | None = None
    plugin_name: str
    enabled: bool = True


class SourceOut(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str | None = None
    enabled: bool

    class Config:
        from_attributes = True
