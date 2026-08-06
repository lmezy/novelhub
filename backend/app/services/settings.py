from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting


REGISTRATION_APPROVAL_KEY = "registration_approval_enabled"


async def get_registration_approval_enabled(db: AsyncSession) -> bool:
    row = await db.scalar(
        select(AppSetting).where(AppSetting.key == REGISTRATION_APPROVAL_KEY)
    )
    if row is None:
        return False
    return row.value.lower() == "true"


async def set_registration_approval_enabled(
    db: AsyncSession,
    enabled: bool,
) -> bool:
    row = await db.scalar(
        select(AppSetting).where(AppSetting.key == REGISTRATION_APPROVAL_KEY)
    )
    value = "true" if enabled else "false"
    if row is None:
        db.add(AppSetting(key=REGISTRATION_APPROVAL_KEY, value=value))
    else:
        row.value = value
    await db.commit()
    return enabled
