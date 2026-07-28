"""Backup service -- daily incremental and weekly full backups.

Uses zstd-compressed tar archives for efficient storage.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


class BackupService:
    """Handles database + file backups with zstd compression.

    Incremental (daily): files changed since last backup.
    Full (weekly): everything including pg_dump.
    """

    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        self._backup_dir = Path(settings.STORAGE_PATH).parent / "backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_db_url() -> dict[str, str]:
        """Parse DATABASE_URL into connection components for pg_dump/psql."""
        url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "postgres",
            "port": str(parsed.port or 5432),
            "user": parsed.username or "novelhub",
            "password": parsed.password or "",
            "dbname": parsed.path.lstrip("/") or "novelhub",
        }

    async def create_full_backup(self) -> str:
        """Create a full backup: database dump + books + covers."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"full_{ts}.tar.zst"
        backup_path = self._backup_dir / backup_name

        logger.info("Starting full backup: {}", backup_name)

        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "backup"
            staged.mkdir()

            # Database dump
            db_dump = staged / "database.sql"
            await self._dump_database(db_dump)

            # Books content
            books_src = Path(settings.STORAGE_PATH)
            if books_src.exists():
                shutil.copytree(books_src, staged / "books",
                                symlinks=True, dirs_exist_ok=True)

            # Covers
            covers_src = Path(settings.STORAGE_PATH).parent / "covers"
            if covers_src.exists():
                shutil.copytree(covers_src, staged / "covers",
                                symlinks=True, dirs_exist_ok=True)

            # Manifest
            (staged / "manifest.json").write_text(json.dumps({
                "type": "full",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, indent=2), encoding="utf-8")

            # Compress with zstd
            await self._compress_dir(staged, backup_path)

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        logger.info("Full backup complete: {} ({:.1f} MB)", backup_name, size_mb)
        return str(backup_path)

    async def create_incremental_backup(self) -> str:
        """Create incremental backup of files changed since last backup."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"incr_{ts}.tar.zst"
        backup_path = self._backup_dir / backup_name

        logger.info("Starting incremental backup: {}", backup_name)
        last_time = self._get_last_backup_time()

        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "backup"
            staged.mkdir()

            books_src = Path(settings.STORAGE_PATH)
            if books_src.exists():
                books_dest = staged / "books"
                books_dest.mkdir(parents=True)
                self._copy_changed(books_src, books_dest, last_time)

            (staged / "manifest.json").write_text(json.dumps({
                "type": "incremental",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "based_on": last_time.isoformat() if last_time else None,
            }, indent=2), encoding="utf-8")

            await self._compress_dir(staged, backup_path)

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        logger.info("Incremental backup complete: {} ({:.1f} MB)", backup_name, size_mb)
        return str(backup_path)

    def list_backups(self) -> list[dict]:
        """List all available backups sorted by date (newest first)."""
        backups = []
        for f in sorted(self._backup_dir.glob("*.tar.zst"), reverse=True):
            backups.append({
                "name": f.name,
                "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                "created_at": datetime.fromtimestamp(
                    f.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            })
        return backups

    def delete_backup(self, name: str) -> bool:
        """Delete a specific backup file."""
        path = self._backup_dir / name
        if path.exists():
            path.unlink()
            logger.info("Deleted backup: {}", name)
            return True
        return False

    async def restore_backup(self, backup_name: str) -> None:
        """Restore from a backup archive."""
        backup_path = self._backup_dir / backup_name
        if not backup_path.exists():
            raise ValueError(f"Backup not found: {backup_name}")

        logger.warning("Restoring from backup: {}", backup_name)

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # Decompress
            import zstandard as zstd
            with open(backup_path, "rb") as f:
                dctx = zstd.ZstdDecompressor()
                with dctx.stream_reader(f) as reader:
                    with tarfile.open(fileobj=reader, mode="r|") as tar:
                        tar.extractall(path=tmpdir)

            extracted = tmpdir / "backup"

            # Restore database
            db_dump = extracted / "database.sql"
            if db_dump.exists():
                await self._restore_database(db_dump)

            # Restore files
            for subdir in ["books", "covers"]:
                src = extracted / subdir
                if src.exists():
                    dest = Path(settings.STORAGE_PATH).parent / subdir
                    if subdir == "books":
                        dest = Path(settings.STORAGE_PATH)
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)

        logger.info("Restore complete from {}", backup_name)

    # -- internals --

    async def _dump_database(self, output: Path) -> None:
        db = self._parse_db_url()
        try:
            result = subprocess.run(
                ["pg_dump", "-h", db["host"], "-p", db["port"],
                 "-U", db["user"], "-d", db["dbname"],
                 "--no-owner", "--no-acl", "-f", str(output)],
                env={**os.environ, "PGPASSWORD": db["password"]},
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.error("pg_dump failed: {}", result.stderr)
                raise RuntimeError(f"pg_dump failed: {result.stderr}")
            logger.info("Database dump complete")
        except FileNotFoundError:
            logger.warning("pg_dump not available, skipping DB backup")
            output.write_text("-- pg_dump not available\n", encoding="utf-8")

    async def _restore_database(self, dump_path: Path) -> None:
        db = self._parse_db_url()
        try:
            result = subprocess.run(
                ["psql", "-h", db["host"], "-p", db["port"],
                 "-U", db["user"], "-d", db["dbname"],
                 "-f", str(dump_path)],
                env={**os.environ, "PGPASSWORD": db["password"]},
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                logger.error("psql restore failed: {}", result.stderr)
                raise RuntimeError(f"Restore failed: {result.stderr}")
            logger.info("Database restore complete")
        except FileNotFoundError:
            logger.error("psql not available, cannot restore database")

    async def _compress_dir(self, source: Path, dest: Path) -> None:
        import zstandard as zstd
        cctx = zstd.ZstdCompressor(level=3)
        loop = asyncio.get_event_loop()

        def _write():
            with open(dest, "wb") as f:
                with cctx.stream_writer(f) as compressor:
                    with tarfile.open(fileobj=compressor, mode="w|") as tar:
                        for item in sorted(source.rglob("*")):
                            arcname = item.relative_to(source.parent)
                            tar.add(item, arcname=arcname.as_posix())

        await loop.run_in_executor(None, _write)

    def _copy_changed(self, src: Path, dest: Path,
                       since: Optional[datetime]) -> None:
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            if since:
                mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                if mtime <= since:
                    continue
            rel = item.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

    def _get_last_backup_time(self) -> Optional[datetime]:
        backups = sorted(self._backup_dir.glob("*.tar.zst"))
        if not backups:
            return None
        return datetime.fromtimestamp(backups[-1].stat().st_mtime, tz=timezone.utc)
