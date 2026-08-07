import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting


REGISTRATION_APPROVAL_KEY = "registration_approval_enabled"
AUTO_SYNC_ENABLED_KEY = "auto_sync_enabled"
AUTO_SYNC_TIME_KEY = "auto_sync_time"
AUTO_SYNC_LAST_RUN_KEY = "auto_sync_last_run"
DEFAULT_AUTO_SYNC_TIME = "03:00"


async def _get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    row = await db.scalar(
        select(AppSetting).where(AppSetting.key == key)
    )
    return row.value if row is not None else default


async def _set_setting(db: AsyncSession, key: str, value: str) -> None:
    row = await db.scalar(
        select(AppSetting).where(AppSetting.key == key)
    )
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()


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


async def get_auto_sync_settings(db: AsyncSession) -> dict:
    enabled_raw = await _get_setting(db, AUTO_SYNC_ENABLED_KEY, "false")
    time_raw = await _get_setting(
        db,
        AUTO_SYNC_TIME_KEY,
        DEFAULT_AUTO_SYNC_TIME,
    )
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_raw):
        time_raw = DEFAULT_AUTO_SYNC_TIME
    return {
        "enabled": enabled_raw.lower() == "true",
        "time": time_raw,
    }


async def set_auto_sync_settings(
    db: AsyncSession,
    enabled: bool,
    time: str,
) -> dict:
    time = (time or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
        raise ValueError("time must be in HH:MM format")
    await _set_setting(
        db,
        AUTO_SYNC_ENABLED_KEY,
        "true" if enabled else "false",
    )
    await _set_setting(db, AUTO_SYNC_TIME_KEY, time)
    await _set_setting(db, AUTO_SYNC_LAST_RUN_KEY, "")
    return {"enabled": enabled, "time": time}


async def get_auto_sync_last_run(db: AsyncSession) -> str:
    return await _get_setting(db, AUTO_SYNC_LAST_RUN_KEY, "")


async def set_auto_sync_last_run(db: AsyncSession, date_str: str) -> None:
    await _set_setting(db, AUTO_SYNC_LAST_RUN_KEY, date_str)
