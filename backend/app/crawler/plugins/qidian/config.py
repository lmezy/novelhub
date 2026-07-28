"""Qidian plugin configuration."""

from dataclasses import dataclass


@dataclass
class QidianConfig:
    base_url: str = "https://www.qidian.com"
    rate_limit: float = 1.5
    timeout: float = 30_000
    headless: bool = True