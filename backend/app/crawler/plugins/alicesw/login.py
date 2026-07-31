"""AliceSW cookie authentication and session management."""

import asyncio
from http.cookiejar import Cookie, CookieJar
from typing import Optional

from loguru import logger

from app.core.database import SessionLocal
from app.services.cookie_crypto import safe_decrypt_cookie
from app.repositories.cookie import CookieRepository


class AliceSWLogin:
    """Manages cookie-based authentication for AliceSW."""

    def __init__(self, source_id: str = "alicesw"):
        self.source_id = source_id
        self._cookie_string: Optional[str] = None
        self._cookie_jar: Optional[CookieJar] = None

    async def load_cookie_from_db(self) -> Optional[str]:
        """Load the stored cookie for this source from the database."""
        async with SessionLocal() as db:
            repo = CookieRepository(db)
            cookie = await repo.get_by_source(self.source_id)
            if cookie is None:
                logger.warning("No cookie found for source={}", self.source_id)
                return None

            from datetime import datetime, timezone
            if cookie.expired_at and cookie.expired_at < datetime.now(timezone.utc):
                logger.warning("Cookie for source={} is expired", self.source_id)
                return None

            self._cookie_string = safe_decrypt_cookie(cookie.cookie_data)
            self._parse_cookie()
            logger.info("Loaded cookie for source={}", self.source_id)
            return self._cookie_string

    def set_cookie(self, cookie_string: str) -> None:
        """Set cookie string directly."""
        self._cookie_string = cookie_string
        self._parse_cookie()

    def _parse_cookie(self) -> None:
        """Parse cookie string into a CookieJar for requests."""
        if not self._cookie_string:
            self._cookie_jar = None
            return

        from urllib.parse import urlparse

        jar = CookieJar()
        domain = urlparse("https://www.alicesw.com").netloc

        for item in self._cookie_string.split(";"):
            item = item.strip()
            if "=" not in item:
                continue
            name, _, value = item.partition("=")
            cookie = Cookie(
                version=0,
                name=name.strip(),
                value=value.strip(),
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=False,
                expires=None,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
            jar.set_cookie(cookie)

        self._cookie_jar = jar

    @property
    def cookie_jar(self) -> Optional[CookieJar]:
        return self._cookie_jar

    @property
    def cookie_string(self) -> Optional[str]:
        return self._cookie_string

    def is_authenticated(self) -> bool:
        return self._cookie_string is not None and len(self._cookie_string) > 0

    def get_cookie_header(self) -> str:
        """Return cookie string formatted for HTTP header."""
        if not self._cookie_string:
            return ""
        # Remove newlines and extra spaces
        return self._cookie_string.strip().replace("\n", "")
