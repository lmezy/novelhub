"""Hot-swappable proxy configuration for Playwright manual login.

Stored in-memory; survives as long as the process runs.
Can be read/written via the admin API without restart.
Also reads HTTPS_PROXY env var as initial default.
"""

import os
import threading
from dataclasses import dataclass


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


def get_proxy_config() -> ProxyConfig:
    with _lock:
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


def get_playwright_proxy() -> dict | None:
    """Return Playwright proxy dict or None if disabled."""
    cfg = get_proxy_config()
    if not cfg.enabled:
        return None
    server = cfg.https_proxy or cfg.http_proxy
    if not server:
        return None
    return {"server": server}
