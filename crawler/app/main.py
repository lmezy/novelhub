import asyncio
import signal

from loguru import logger

from crawler_service import run_crawl_for_source


running = True


def shutdown(sig, frame):
    global running
    logger.info("Crawler stopping...")
    running = False


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


async def main():
    logger.info("NovelHub Crawler started")
    try:
        while running:
            logger.info("Crawler heartbeat")
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    logger.info("Crawler stopped")


if __name__ == "__main__":
    asyncio.run(main())
