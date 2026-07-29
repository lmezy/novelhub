"""Manual browser login API endpoints.

POST   /credentials/{id}/manual-login/start  - Start a browser session
POST   /manual-login/{sid}/click             - Click at coordinates
POST   /manual-login/{sid}/type              - Type text
POST   /manual-login/{sid}/key               - Press a key
POST   /manual-login/{sid}/scroll            - Scroll page
GET    /manual-login/{sid}/screenshot        - Get current screenshot
GET    /manual-login/{sid}/url               - Get current URL
POST   /manual-login/{sid}/finish            - Capture cookies and save
POST   /manual-login/{sid}/cancel            - Cancel session
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Source, Cookie
from app.models.source_credential import SourceCredential
from app.services.cookie_crypto import decrypt_cookie
from app.services.manual_login import ManualLoginManager, ManualLoginSession
from app.services.auth import require_admin

router = APIRouter(tags=["manual-login"])


class ClickRequest(BaseModel):
    x: int
    y: int


class TypeRequest(BaseModel):
    text: str


class KeyRequest(BaseModel):
    key: str


class ScrollRequest(BaseModel):
    delta_y: int = 300


@router.post("/credentials/{cred_id}/manual-login/start")
async def start_manual_login(cred_id: str, db: AsyncSession = Depends(get_db)):
    """Start a remote-controlled browser session for manual login."""
    cred = await db.get(SourceCredential, cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    source = await db.get(Source, cred.source)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source not found: {cred.source}")

    # Determine the login URL
    config = source.config if source.plugin_name == "yuedu" and source.config else {}
    base_url = config.get("bookSourceUrl", source.url or "")
    login_url_js = config.get("loginUrl", "")

    if login_url_js and login_url_js.strip():
        # Resolve login URL like the login parser does
        from urllib.parse import urljoin
        login_url = login_url_js.strip()
        if login_url.startswith("http"):
            pass
        elif login_url.startswith("/"):
            login_url = urljoin(base_url, login_url)
        else:
            login_url = urljoin(base_url, login_url)
    else:
        # No loginUrl configured: default to base URL
        login_url = base_url

    if not login_url:
        raise HTTPException(status_code=400, detail="No login URL could be determined for this source")

    password = decrypt_cookie(cred.password_encrypted)

    session = ManualLoginManager.create_session(
        source_id=cred.source,
        source_name=source.name or cred.source,
        base_url=base_url,
        login_url=login_url,
        username=cred.username,
        password=password,
    )

    screenshot = await session.start()
    if screenshot is None:
        ManualLoginManager.remove_session(session.session_id)
        raise HTTPException(status_code=500, detail="Failed to start browser session")

    return {
        "session_id": session.session_id,
        "screenshot": screenshot,
        "login_url": login_url,
        "source_name": session.source_name,
    }


def _get_session(session_id: str) -> ManualLoginSession:
    session = ManualLoginManager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return session


@router.post("/manual-login/{session_id}/click")
async def manual_login_click(session_id: str, req: ClickRequest):
    session = _get_session(session_id)
    screenshot = await session.click(req.x, req.y)
    return {"screenshot": screenshot}


@router.post("/manual-login/{session_id}/type")
async def manual_login_type(session_id: str, req: TypeRequest):
    session = _get_session(session_id)
    screenshot = await session.type_text(req.text)
    return {"screenshot": screenshot}


@router.post("/manual-login/{session_id}/key")
async def manual_login_key(session_id: str, req: KeyRequest):
    session = _get_session(session_id)
    screenshot = await session.press_key(req.key)
    return {"screenshot": screenshot}


@router.post("/manual-login/{session_id}/scroll")
async def manual_login_scroll(session_id: str, req: ScrollRequest):
    session = _get_session(session_id)
    screenshot = await session.scroll(req.delta_y)
    return {"screenshot": screenshot}


@router.get("/manual-login/{session_id}/screenshot")
async def manual_login_screenshot(session_id: str):
    session = _get_session(session_id)
    screenshot = await session.screenshot()
    return {"screenshot": screenshot, "url": await session.get_url()}


@router.post("/manual-login/{session_id}/finish")
async def manual_login_finish(session_id: str, db: AsyncSession = Depends(get_db)):
    """Capture cookies from the browser session and save to the database."""
    session = _get_session(session_id)
    cookie_str = await session.finish()

    ManualLoginManager.remove_session(session_id)

    if not cookie_str:
        raise HTTPException(status_code=400, detail="No cookies captured from browser session")

    # Save to Cookie table
    existing = await db.scalar(select(Cookie).where(Cookie.source == session.source_id))
    if existing:
        existing.cookie_data = cookie_str
    else:
        new_cookie = Cookie(
            id=str(uuid4()),
            source=session.source_id,
            cookie_data=cookie_str,
        )
        db.add(new_cookie)
    await db.commit()

    return {"message": "Login successful, cookie saved", "source": session.source_id}


@router.post("/manual-login/{session_id}/cancel")
async def manual_login_cancel(session_id: str):
    session = _get_session(session_id)
    await session.cancel()
    ManualLoginManager.remove_session(session_id)
    return {"message": "Session cancelled"}
