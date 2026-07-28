from pydantic import BaseModel


class SourceCreate(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str
    enabled: bool = True
    config: dict | None = None


class SourceOut(BaseModel):
    id: str
    name: str
    url: str | None = None
    plugin_name: str | None = None
    enabled: bool
    config: dict | None = None

    class Config:
        from_attributes = True
