import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting


REGISTRATION_APPROVAL_KEY = "registration_approval_enabled"
AUTO_SYNC_ENABLED_KEY = "auto_sync_enabled"
AUTO_SYNC_TIME_KEY = "auto_sync_time"
AUTO_SYNC_INTERVAL_KEY = "auto_sync_interval_hours"
AUTO_SYNC_LAST_RUN_KEY = "auto_sync_last_run"
DEFAULT_AUTO_SYNC_TIME = "03:00"
# 0 keeps the legacy behaviour ("every day at the configured time").
DEFAULT_AUTO_SYNC_INTERVAL_HOURS = 0
MAX_AUTO_SYNC_INTERVAL_HOURS = 168


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


def _parse_interval_hours(raw: str | int | None) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_AUTO_SYNC_INTERVAL_HOURS
    return max(0, min(value, MAX_AUTO_SYNC_INTERVAL_HOURS))


def parse_auto_sync_last_run(raw: str | None) -> datetime | None:
    """Parse the stored last-run marker.

    Older deployments stored a plain ``YYYY-MM-DD`` date; newer ones store the
    full local timestamp so interval scheduling can work.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
    return parsed.replace(tzinfo=None)


def auto_sync_is_due(
    auto_settings: dict,
    last_run: str | None,
    now: datetime,
) -> bool:
    """Whether the periodic sync should run at ``now``.

    ``interval_hours > 0`` means "run every N hours".  Otherwise the legacy
    "every day at HH:MM" mode is used, but with catch-up semantics: a run is
    still due later in the day if the scheduler missed the exact minute (a
    busy worker, a container restart, a paused queue).  The previous
    exact-minute comparison silently skipped the whole day in those cases,
    which is why the background sync could go a long time without running.
    """
    if not auto_settings.get("enabled"):
        return False
    last = parse_auto_sync_last_run(last_run)
    interval_hours = int(auto_settings.get("interval_hours") or 0)
    if interval_hours > 0:
        if last is None:
            return True
        return (now - last) >= timedelta(hours=interval_hours)

    if last is not None and last.date() == now.date():
        return False
    time_text = str(auto_settings.get("time") or DEFAULT_AUTO_SYNC_TIME)
    try:
        hour_text, minute_text = time_text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError):
        hour, minute = 3, 0
    return (now.hour, now.minute) >= (hour, minute)


async def get_auto_sync_settings(db: AsyncSession) -> dict:
    enabled_raw = await _get_setting(db, AUTO_SYNC_ENABLED_KEY, "false")
    time_raw = await _get_setting(
        db,
        AUTO_SYNC_TIME_KEY,
        DEFAULT_AUTO_SYNC_TIME,
    )
    interval_raw = await _get_setting(
        db,
        AUTO_SYNC_INTERVAL_KEY,
        str(DEFAULT_AUTO_SYNC_INTERVAL_HOURS),
    )
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_raw):
        time_raw = DEFAULT_AUTO_SYNC_TIME
    return {
        "enabled": enabled_raw.lower() == "true",
        "time": time_raw,
        "interval_hours": _parse_interval_hours(interval_raw),
    }


async def set_auto_sync_settings(
    db: AsyncSession,
    enabled: bool,
    time: str,
    interval_hours: int | None = None,
) -> dict:
    time = (time or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
        raise ValueError("time must be in HH:MM format")
    if interval_hours is None:
        # Callers that predate interval scheduling keep the stored value.
        existing = await _get_setting(
            db,
            AUTO_SYNC_INTERVAL_KEY,
            str(DEFAULT_AUTO_SYNC_INTERVAL_HOURS),
        )
        interval_hours = _parse_interval_hours(existing)
    if not isinstance(interval_hours, int) or interval_hours < 0:
        raise ValueError("interval_hours must be a non-negative integer")
    if interval_hours > MAX_AUTO_SYNC_INTERVAL_HOURS:
        raise ValueError(
            f"interval_hours must be <= {MAX_AUTO_SYNC_INTERVAL_HOURS}"
        )
    await _set_setting(
        db,
        AUTO_SYNC_ENABLED_KEY,
        "true" if enabled else "false",
    )
    await _set_setting(db, AUTO_SYNC_TIME_KEY, time)
    await _set_setting(db, AUTO_SYNC_INTERVAL_KEY, str(interval_hours))
    await _set_setting(db, AUTO_SYNC_LAST_RUN_KEY, "")
    # Disabling auto-sync must also stop the tasks it already queued, or the
    # background crawl keeps running and a user cannot tell it is "off".
    # Auto-sync tasks were created without a user_id, unlike manual tasks.
    if not enabled:
        try:
            from sqlalchemy import update
            from app.models import CrawlTask

            await db.execute(
                update(CrawlTask)
                .where(
                    CrawlTask.user_id.is_(None),
                    CrawlTask.mode == "discover_all",
                    CrawlTask.status.in_(["pending", "running"]),
                )
                .values(status="cancelled")
            )
            await db.commit()
        except Exception:
            pass
    return {
        "enabled": enabled,
        "time": time,
        "interval_hours": interval_hours,
    }


async def get_auto_sync_last_run(db: AsyncSession) -> str:
    return await _get_setting(db, AUTO_SYNC_LAST_RUN_KEY, "")


async def set_auto_sync_last_run(db: AsyncSession, date_str: str) -> None:
    await _set_setting(db, AUTO_SYNC_LAST_RUN_KEY, date_str)
