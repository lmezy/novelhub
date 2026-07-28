"""Fanqie plugin configuration."""

from dataclasses import dataclass


@dataclass
class FanqieConfig:
    base_url: str = "https://fanqienovel.com"
    rate_limit: float = 1.0
    timeout: float = 30_000
    headless: bool = True