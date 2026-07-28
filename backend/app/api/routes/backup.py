"""Backup management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.services.auth import require_admin
from app.services.backup import BackupService


router = APIRouter(prefix="/backup", tags=["backup"], dependencies=[Depends(require_admin)])


@router.post("/full")
async def create_full_backup(db: AsyncSession = Depends(get_db)):
    """Create a full backup (database + all files)."""
    try:
        path = await BackupService(db).create_full_backup()
        return {"status": "ok", "path": path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/incremental")
async def create_incremental_backup(db: AsyncSession = Depends(get_db)):
    """Create an incremental backup (changed files only)."""
    try:
        path = await BackupService(db).create_incremental_backup()
        return {"status": "ok", "path": path}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
async def list_backups():
    """List all available backups."""
    return BackupService().list_backups()


@router.delete("/{name}")
async def delete_backup(name: str):
    """Delete a specific backup file."""
    if not BackupService().delete_backup(name):
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"status": "deleted"}


@router.post("/restore/{name}")
async def restore_backup(name: str):
    """Restore from a backup archive."""
    try:
        await BackupService().restore_backup(name)
        return {"status": "restored"}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
