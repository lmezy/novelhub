"""Seed the initial super admin user.

Usage:
    python scripts/seed_super_admin.py --username admin --password changeme --email admin@example.com

Set NOVELHUB_DATABASE_URL or DATABASE_URL env var before running.
"""

import argparse
import asyncio
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import User
from app.services.security import hash_password


async def seed(username: str, password: str, email: str) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        existing = await db.scalar(select(User).where(User.username == username))
        if existing:
            existing.role = "super_admin"
            await db.commit()
            print(f"User '{username}' already exists -- upgraded to super_admin.")
        else:
            user = User(
                id=str(uuid4()),
                username=username,
                email=email,
                password_hash=hash_password(password),
                role="super_admin",
            )
            db.add(user)
            await db.commit()
            print(f"Super admin '{username}' created successfully.")

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Seed initial super admin user")
    parser.add_argument("--username", required=True, help="Super admin username")
    parser.add_argument("--password", required=True, help="Super admin password")
    parser.add_argument("--email", default="admin@novelhub.local", help="Super admin email")
    args = parser.parse_args()
    asyncio.run(seed(args.username, args.password, args.email))


if __name__ == "__main__":
    main()
