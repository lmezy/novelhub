import sys
from loguru import logger

from app.core.config import settings

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level="INFO",
        colorize=True,
    )
    logger.add(
        "logs/novelhub_{time:YYYY-MM-DD}.log",
        format=LOG_FORMAT,
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
    )
    logger.info("Logging configured for {}", settings.PROJECT_NAME)
