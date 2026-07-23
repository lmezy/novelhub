from fastapi import APIRouter


router=APIRouter()


@router.get("/api/test")

async def test():

    return {

        "message":

        "NovelHub API"

    }

