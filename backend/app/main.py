from fastapi import FastAPI

from core.config import settings


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0"
)



@app.get("/")
async def root():

    return {

        "project":
            settings.PROJECT_NAME,

        "status":
            "running"

    }



@app.get("/health")

async def health():

    return {

        "status":"ok"

    }

