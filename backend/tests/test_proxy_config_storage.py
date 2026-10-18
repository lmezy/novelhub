"""The proxy setting has to survive a container restart.

It used to live in ``Path(STORAGE_PATH).parent / "proxy_config.json"``, i.e.
``/app/proxy_config.json`` -- the image's own writable layer, which is wiped
whenever the container is recreated.  On 2026-10-18 that showed up as 爱丽丝书屋
suddenly being unreachable: the crawler has no direct route to it, so without
the proxy every request failed while the admin page had shown the setting as
configured right up to the restart.  It now lives inside the shared
``STORAGE_PATH`` mount, and the old location is still read for compatibility.
"""

import json

import pytest

from app.services import proxy_config


@pytest.fixture()
def storage_root(tmp_path, monkeypatch):
    """Point ``STORAGE_PATH`` at a temporary directory."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path / "storage"))
    (tmp_path / "storage").mkdir()
    yield tmp_path


def test_saved_proxy_survives_a_restart(storage_root):
    proxy_config.set_proxy_config(
        proxy_config.ProxyConfig(
            enabled=True,
            https_proxy="http://192.168.1.17:7890",
            http_proxy="http://192.168.1.17:7890",
        )
    )

    written = storage_root / "storage" / "proxy_config.json"
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["https_proxy"] == (
        "http://192.168.1.17:7890"
    )
    # Nothing is written next to the image layer any more.
    assert not (storage_root / "proxy_config.json").exists()

    # A fresh process (empty in-memory config) reads it back from disk.
    proxy_config._config.enabled = False
    proxy_config._config.https_proxy = ""
    proxy_config._config.http_proxy = ""
    assert proxy_config.get_proxy_config().enabled is True
    assert proxy_config.get_proxy_config().https_proxy == "http://192.168.1.17:7890"


def test_the_old_location_is_still_read(storage_root):
    (storage_root / "proxy_config.json").write_text(
        json.dumps({
            "enabled": True,
            "https_proxy": "http://127.0.0.1:10808",
            "http_proxy": "",
        }),
        encoding="utf-8",
    )

    config = proxy_config.get_proxy_config()

    assert config.enabled is True
    assert config.https_proxy == "http://127.0.0.1:10808"


def test_a_disabled_config_is_returned_as_written(storage_root):
    proxy_config.set_proxy_config(proxy_config.ProxyConfig())
    config = proxy_config.get_proxy_config()
    assert config.enabled is False
    assert config.https_proxy == ""
