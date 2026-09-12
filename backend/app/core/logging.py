import sys
import logging

from loguru import logger

from app.core.config import settings

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)


class InterceptHandler(logging.Handler):
    """Forward standard-library log records into loguru.

    The YueDu plugin and its Node JS runtime use ``logging.getLogger(__name__)``.
    Without this bridge their diagnostics were emitted by logging's last-resort
    handler -- bare text with no timestamp or level, so they were invisible to
    the ``grep "| ERROR"`` troubleshooting flow -- and any call that used
    loguru-style ``{}`` placeholders died inside ``record.getMessage()`` with a
    "not all arguments converted during string formatting" logging error that
    swallowed the message.
    """

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def install_stdlib_logging_bridge(level: int = logging.INFO) -> None:
    """Send every standard-library log record to loguru (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(InterceptHandler())
    root.setLevel(level)
    # Chatty third-party transport logs would drown the crawl diagnostics.
    for noisy in ("httpx", "httpcore", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


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
    install_stdlib_logging_bridge()
    logger.info("Logging configured for {}", settings.PROJECT_NAME)
