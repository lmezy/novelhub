"""Entry point for the DB-backed crawl queue worker."""

from app.services.crawl_runner import main


if __name__ == "__main__":
    main()
