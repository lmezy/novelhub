"""Regression tests for the :mod:`app.core.clock` wall-clock convention.

Every naive ``DateTime`` column in the schema holds the *database server's*
local wall clock, and the frontend renders it as *browser* local time.  Python
code that wrote or compared aware UTC (or naive UTC) therefore drifted by the
UTC offset -- on a CST host a Cookie expiring at 10:00 local was judged fresh
until 18:00 local, and rows written from Python showed up 8 hours early.

These tests pin the convention down:

* naive/aware mixing must never raise ``TypeError`` (it used to kill the whole
  2am Cookie health check for any Cookie with ``expired_at`` set);
* the *local* wall clock decides expiry, not the UTC one;
* Python-written timestamps land in the column as naive local time;
* ``deleted_accounts.deleted_at`` is the one aware column and stays aware UTC.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.clock import naive_now
from app.models.deleted_account import DeletedAccount
from app.repositories.cookie import CookieRepository
from app.services.ai_diagnosis import collect_login_state
from app.services.cookie_health import CookieHealthService

# A fixed local wall clock far enough in the future that a regression to
# ``datetime.now(timezone.utc)`` cannot accidentally produce the same answer.
FIXED_LOCAL = datetime(2030, 6, 1, 12, 0, 0)


def _cookie(source: str = "src1", cookie_id: str = "c1", expired_at=None):
    return SimpleNamespace(
        source=source,
        id=cookie_id,
        cookie_data="encrypted",
        expired_at=expired_at,
    )


def _session(cookies) -> MagicMock:
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=cookies)
    db.rollback = AsyncMock()
    return db


class _ACM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc):
        return False


# --------------------------------------------------------------------------
# 1. services/cookie_health.py -- the crash that took down the 2am check
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_compares_a_non_null_expired_at_without_type_error():
    """``cookies.expired_at`` is naive; comparing it to aware UTC raised TypeError.

    The comparison only ran when ``expired_at`` was set, so any Cookie with a
    filled-in expiry aborted the entire health check with
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    """
    session = _session([_cookie(expired_at=naive_now() - timedelta(hours=1))])
    with (
        patch("app.services.cookie_health.SessionLocal", return_value=_ACM(session)),
        patch.object(
            CookieHealthService,
            "_try_refresh",
            AsyncMock(return_value={"success": True, "message": "Cookie auto-refreshed"}),
        ),
    ):
        result = await CookieHealthService.check_all_cookies()

    assert result["checked"] == 1
    assert result["expired"] == 1
    assert result["refreshed"] == 1
    assert result["details"][0]["reason"] == "expired"


@pytest.mark.asyncio
async def test_health_check_judges_expiry_on_the_local_clock():
    """A locally expired Cookie is expired, a locally fresh one is validated.

    With a naive-UTC ``now`` the first Cookie looked fresh for another 8 hours
    on a CST host (and raised TypeError besides).
    """
    expired = _cookie("expired-src", "c1", expired_at=FIXED_LOCAL - timedelta(hours=4))
    fresh = _cookie("fresh-src", "c2", expired_at=FIXED_LOCAL + timedelta(hours=11))
    session = _session([expired, fresh])
    with (
        patch("app.services.cookie_health.SessionLocal", return_value=_ACM(session)),
        patch("app.services.cookie_health.naive_now", return_value=FIXED_LOCAL),
        patch.object(
            CookieHealthService,
            "_validate_cookie",
            AsyncMock(return_value=True),
        ) as validate,
        patch.object(
            CookieHealthService,
            "_try_refresh",
            AsyncMock(return_value={"success": True}),
        ),
    ):
        result = await CookieHealthService.check_all_cookies()

    by_source = {d["source"]: d for d in result["details"]}
    assert by_source["expired-src"]["reason"] == "expired"
    assert by_source["fresh-src"]["status"] == "valid"
    # The fresh Cookie must have been validated, not blindly refreshed.
    assert [call.args[0]["source"] for call in validate.await_args_list] == ["fresh-src"]


# --------------------------------------------------------------------------
# 2. crawler/plugins/alicesw/login.py -- same naive/aware TypeError
# --------------------------------------------------------------------------


def _alice_login(cookie):
    from app.crawler.plugins.alicesw.login import AliceSWLogin

    repo = MagicMock()
    repo.get_by_source = AsyncMock(return_value=cookie)
    return AliceSWLogin("alicesw"), repo


@pytest.mark.asyncio
async def test_alicesw_login_treats_a_locally_expired_cookie_as_expired():
    login, repo = _alice_login(_cookie(expired_at=FIXED_LOCAL - timedelta(hours=4)))

    with (
        patch("app.crawler.plugins.alicesw.login.SessionLocal", return_value=_ACM(AsyncMock())),
        patch("app.crawler.plugins.alicesw.login.CookieRepository", return_value=repo),
        patch("app.crawler.plugins.alicesw.login.naive_now", return_value=FIXED_LOCAL),
        patch(
            "app.crawler.plugins.alicesw.login.safe_decrypt_cookie",
            return_value="a=b",
        ) as decrypt,
    ):
        assert await login.load_cookie_from_db() is None

    decrypt.assert_not_called()
    assert login.is_authenticated() is False


@pytest.mark.asyncio
async def test_alicesw_login_loads_a_cookie_that_is_still_valid_locally():
    login, repo = _alice_login(_cookie(expired_at=FIXED_LOCAL + timedelta(hours=11)))

    with (
        patch("app.crawler.plugins.alicesw.login.SessionLocal", return_value=_ACM(AsyncMock())),
        patch("app.crawler.plugins.alicesw.login.CookieRepository", return_value=repo),
        patch("app.crawler.plugins.alicesw.login.naive_now", return_value=FIXED_LOCAL),
        patch(
            "app.crawler.plugins.alicesw.login.safe_decrypt_cookie",
            return_value="ss_userid=1; cf_clearance=abc",
        ),
    ):
        assert await login.load_cookie_from_db() == "ss_userid=1; cf_clearance=abc"

    assert login.is_authenticated() is True


# --------------------------------------------------------------------------
# 3. services/ai_diagnosis.py -- naive UTC vs local, off by the UTC offset
# --------------------------------------------------------------------------


class _DiagnosisDB:
    """Minimal stand-in for the two queries ``collect_login_state`` makes."""

    def __init__(self, cookies, credential=None):
        self.cookies = list(cookies)
        self.credential = credential

    async def scalars(self, query):
        return list(self.cookies)

    async def scalar(self, query):
        return self.credential


def _cookie_row(expired_at):
    return SimpleNamespace(
        id="c1",
        source="src1",
        cookie_data="ss_userid=1",
        expired_at=expired_at,
        created_at=datetime(2030, 1, 1, 0, 0, 0),
    )


@pytest.mark.asyncio
async def test_login_state_marks_a_locally_expired_cookie():
    db = _DiagnosisDB([_cookie_row(FIXED_LOCAL - timedelta(hours=4))])

    with patch("app.services.ai_diagnosis.naive_now", return_value=FIXED_LOCAL):
        state = await collect_login_state(db, "src1")

    assert state["configured"] is True
    assert state["expired"] is True


@pytest.mark.asyncio
async def test_login_state_keeps_a_locally_fresh_cookie():
    db = _DiagnosisDB([_cookie_row(FIXED_LOCAL + timedelta(hours=11))])

    with patch("app.services.ai_diagnosis.naive_now", return_value=FIXED_LOCAL):
        state = await collect_login_state(db, "src1")

    assert state["expired"] is False


# --------------------------------------------------------------------------
# 4. services/token_service.py -- api_tokens columns are naive DateTime
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_token_expiry_is_written_as_naive_local_time():
    """An aware datetime in a naive column is an asyncpg hazard and shows 8h early."""
    from app.services.token_service import TokenService

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    service = TokenService(db)

    with patch("app.services.token_service.naive_now", return_value=FIXED_LOCAL):
        _, _raw = await service.create_token("u1", "cli", expires_days=1)

    token = db.add.call_args.args[0]
    assert token.expires_at == datetime(2030, 6, 2, 23, 59, 59)
    assert token.expires_at.tzinfo is None


@pytest.mark.asyncio
async def test_api_token_expiry_is_compared_and_stamped_in_local_time():
    from app.models.api_token import ApiToken
    from app.services.token_service import TokenService

    expired = ApiToken(id="t1", user_id="u1", is_active=True,
                       expires_at=FIXED_LOCAL - timedelta(hours=4))
    fresh = ApiToken(id="t2", user_id="u1", is_active=True,
                     expires_at=FIXED_LOCAL + timedelta(hours=11))

    for token, expected in ((expired, None), (fresh, fresh)):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.scalar = AsyncMock(return_value=token)

        with patch("app.services.token_service.naive_now", return_value=FIXED_LOCAL):
            result = await TokenService(db).verify_token("raw")

        assert result is expected
        if expected is not None:
            assert token.last_used_at == FIXED_LOCAL
            assert token.last_used_at.tzinfo is None


# --------------------------------------------------------------------------
# 5. api/routes/source_changes.py -- reviewed_at is a naive DateTime
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_change_stamps_a_naive_local_reviewed_at():
    from app.api.routes.source_changes import review_change
    from app.models.source_change import SourceChange
    from app.schemas.source_change import SourceChangeReview

    change = SourceChange(id="ch1", user_id="u1", action="update", status="pending")
    db = AsyncMock()
    db.get = AsyncMock(return_value=change)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    reviewer = SimpleNamespace(id="admin1", role="admin")

    with patch("app.api.routes.source_changes.naive_now", return_value=FIXED_LOCAL):
        result = await review_change(
            "ch1", SourceChangeReview(action="reject", note="nope"), reviewer, db
        )

    assert result is change
    assert change.reviewed_at == FIXED_LOCAL
    assert change.reviewed_at.tzinfo is None


@pytest.mark.asyncio
async def test_admin_source_change_is_auto_approved_with_local_time():
    from app.api.routes.source_changes import create_change
    from app.schemas.source_change import SourceChangeCreate

    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id="s1"))
    db.add = MagicMock()
    db.commit = AsyncMock()
    admin = SimpleNamespace(id="admin1", role="admin")

    with (
        patch("app.api.routes.source_changes.naive_now", return_value=FIXED_LOCAL),
        patch("app.api.routes.source_changes.apply_source_patch"),
    ):
        change = await create_change(
            SourceChangeCreate(action="update", source_id="s1", source_data={}),
            admin,
            db,
        )

    assert change.status == "approved"
    assert change.reviewed_at == FIXED_LOCAL
    assert change.reviewed_at.tzinfo is None


# --------------------------------------------------------------------------
# 6. repositories/cookie.py -- bound parameter must match the naive column
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_binds_a_naive_local_cutoff():
    """The old implementation raised TypeError before it ever reached the DB.

    ``Cookie.expired_at is None`` is a *Python* identity test that evaluates to
    ``False``, and ``False | <BinaryExpression>`` raises TypeError -- so
    ``list_active()`` could never run.  The cut-off was aware UTC on top of that,
    which Postgres would have coerced using the session time zone.
    """
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=[])

    with patch("app.repositories.cookie.naive_now", return_value=FIXED_LOCAL):
        cookies = await CookieRepository(db).list_active()

    assert cookies == []
    query = db.scalars.await_args.args[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "IS NULL" in sql.upper()

    bound = [v for v in query.compile().params.values() if isinstance(v, datetime)]
    assert bound, "the cut-off should be a bound datetime parameter"
    assert all(value.tzinfo is None for value in bound)
    assert all(abs(value - FIXED_LOCAL) < timedelta(seconds=1) for value in bound)


# --------------------------------------------------------------------------
# 7. services/account.py -- the documented aware exception
# --------------------------------------------------------------------------


def test_reservation_cutoff_stays_aware_utc_for_a_timestamptz_column():
    """``deleted_accounts.deleted_at`` is the one aware column in the schema."""
    from app.services.account import RESERVATION_DAYS, _reservation_cutoff

    assert DeletedAccount.__table__.c.deleted_at.type.timezone is True

    cutoff = _reservation_cutoff()
    assert cutoff.tzinfo is not None
    expected = datetime.now(timezone.utc) - timedelta(days=RESERVATION_DAYS)
    assert abs(cutoff - expected) < timedelta(seconds=5)
