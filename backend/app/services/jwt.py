from datetime import datetime,timedelta,timezone

from jose import jwt


from app.core.config import settings



def create_token(
    user_id:str
):

    expire=datetime.now(
        timezone.utc
    ) + timedelta(
        minutes=settings.JWT_EXPIRE_MINUTES
    )


    payload={

        "sub":user_id,

        "exp":expire

    }


    return jwt.encode(

        payload,

        settings.JWT_SECRET,

        algorithm=settings.JWT_ALGORITHM

    )



def decode_token(
    token:str
):

    return jwt.decode(

        token,

        settings.JWT_SECRET,

        algorithms=[
            settings.JWT_ALGORITHM
        ]

    )
