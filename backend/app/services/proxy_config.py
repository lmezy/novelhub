"""Hot-swappable proxy configuration for Playwright manual login.

Stored in-memory; survives as long as the process runs.
Can be read/written via the admin API without restart.
Also reads HTTPS_PROXY env var as initial default.
"""

import os
import json
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProxyConfig:
    enabled: bool = False
    https_proxy: str = ""
    http_proxy: str = ""


_lock = threading.Lock()

# Initialize from environment
_default_https = os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")
_default_http = os.environ.get("HTTP_PROXY", "") or os.environ.get("http_proxy", "")

_config = ProxyConfig(
    enabled=bool(_default_https or _default_http),
    https_proxy=_default_https,
    http_proxy=_default_http or _default_https,
)


def _config_path() -> Path:
    """Where the proxy setting is persisted.

    Inside ``STORAGE_PATH`` -- that directory is the one mount every NovelHub
    container shares, while the parent (``/app``) is the image's own writable
    layer.  A file there survived until the container was recreated and then
    vanished silently, which on 2026-10-18 showed up as "alicesw cannot be
    reached any more" (the crawler has no direct route to it, so without the
    proxy every request failed) even though the admin page still had the
    setting on screen before the restart.
    """
    from app.core.config import settings
    return Path(settings.STORAGE_PATH) / "proxy_config.json"


def _legacy_config_path() -> Path:
    """The pre-2026-10-18 location, next to the image's ``/app`` directory.

    Still read (never written) so an install that saved its proxy before the
    move keeps working without the admin having to re-enter it.
    """
    from app.core.config import settings
    return Path(settings.STORAGE_PATH).parent / "proxy_config.json"


def _read_file_config() -> ProxyConfig | None:
    for path in (_config_path(), _legacy_config_path()):
        config = _read_config_file(path)
        if config is not None:
            return config
    return None


def _read_config_file(path: Path) -> ProxyConfig | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        return ProxyConfig(
            enabled=bool(data.get("enabled", False)),
            https_proxy=str(data.get("https_proxy", "") or ""),
            http_proxy=str(data.get("http_proxy", "") or ""),
        )
    except Exception:
        return None


def get_proxy_config() -> ProxyConfig:
    with _lock:
        file_cfg = _read_file_config()
        if file_cfg is not None:
            return file_cfg
        return ProxyConfig(
            enabled=_config.enabled,
            https_proxy=_config.https_proxy,
            http_proxy=_config.http_proxy,
        )


def set_proxy_config(cfg: ProxyConfig) -> None:
    with _lock:
        _config.enabled = cfg.enabled
        _config.https_proxy = cfg.https_proxy
        _config.http_proxy = cfg.http_proxy
        try:
            path = _config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({
                    "enabled": cfg.enabled,
                    "https_proxy": cfg.https_proxy,
                    "http_proxy": cfg.http_proxy,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception:
            pass


def get_playwright_proxy() -> dict | None:
    """Return Playwright proxy dict or None if disabled."""
    cfg = get_proxy_config()
    if not cfg.enabled:
        return None
    server = cfg.https_proxy or cfg.http_proxy
    if not server:
        return None
    return {"server": server}


def playwright_context_kwargs() -> dict:
    """Context kwargs that keep a Playwright context on the configured proxy.

    ``browser.new_context(**playwright_context_kwargs())`` makes the browser's
    egress match the proxied HTTP path.  A context that connects directly
    cannot open blocked sites, and cookies like ``cf_clearance`` are bound to
    the egress IP, so a direct context invalidates the session the proxied
    path obtained.
    """
    proxy = get_playwright_proxy()
    return {"proxy": proxy} if proxy else {}
