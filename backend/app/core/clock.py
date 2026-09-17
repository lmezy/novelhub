"""Wall-clock helpers shared by every Python-written timestamp.

The schema's default for ``created_at`` is the database's ``now()``, i.e. the
*database server's* local wall clock (docker-compose pins ``TZ`` for it), and
the frontend renders those columns with ``new Date(value).toLocaleString()``,
which reads a naive ISO string as *browser* local time.  Anything written from
Python therefore also has to be naive local time.

The crawl subsystem used to write ``datetime.now(timezone.utc)`` instead, so on
a CST host one task read "创建 23:06 / 开始 15:37 / 结束 15:39" -- it looked
like the task finished before it started, and every duration the sync page
derived from those two columns was off by the UTC offset.
"""

from datetime import datetime

__all__ = ["naive_now"]


def naive_now() -> datetime:
    """Current local wall-clock time as a naive datetime (DB convention)."""
    return datetime.now()
