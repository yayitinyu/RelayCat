import os
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.database.models import AuditLog, Base, Rule, Setting, User  # noqa: E402
from app.services.backups import (  # noqa: E402
    BackupError,
    create_backup,
    list_backups,
    restore_backup,
)


class BackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        async with self.sessions() as session:
            session.add_all(
                [
                    User(id=42, username="alice", is_verified=True),
                    Setting(key="verification_method", value="turnstile"),
                    Rule(
                        name="test",
                        rule_type="message_content",
                        pattern="spam",
                        action="block",
                    ),
                    AuditLog(event_type="message_received", outcome="received"),
                ]
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_backup_round_trip_and_pre_restore_snapshot(self) -> None:
        backup = await create_backup(
            directory=self.directory,
            session_factory=self.sessions,
        )
        self.assertTrue((self.directory / backup.name).is_file())
        self.assertEqual(backup.counts["users"], 1)

        async with self.sessions() as session:
            await session.execute(delete(User))
            session.add(User(id=99, username="replacement"))
            await session.commit()

        result = await restore_backup(
            backup.name,
            directory=self.directory,
            session_factory=self.sessions,
        )
        self.assertEqual(result.restored.name, backup.name)
        self.assertEqual(result.rollback.reason, "pre-restore")
        async with self.sessions() as session:
            users = (await session.execute(select(User))).scalars().all()
            setting = await session.get(Setting, "verification_method")
        self.assertEqual([user.id for user in users], [42])
        self.assertEqual(setting.value, "turnstile")
        self.assertEqual(len(await list_backups(directory=self.directory)), 2)

    async def test_invalid_archive_is_rejected_before_restore(self) -> None:
        name = "relaycat-backup-20260917T000000Z-deadbeef.zip"
        (self.directory / name).write_bytes(b"not a zip")
        with self.assertRaises(BackupError):
            await restore_backup(
                name,
                directory=self.directory,
                session_factory=self.sessions,
            )
        async with self.sessions() as session:
            user = await session.get(User, 42)
        self.assertIsNotNone(user)


if __name__ == "__main__":
    unittest.main()
