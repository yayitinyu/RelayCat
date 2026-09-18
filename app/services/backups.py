from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import zipfile

from sqlalchemy import DateTime, delete, insert, select

from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import (
    AuditLog,
    MessageRoute,
    Rule,
    Setting,
    User,
    VerificationChallenge,
)

BACKUP_FORMAT = "relaycat-backup"
BACKUP_SCHEMA_VERSION = 1
MAX_BACKUP_BYTES = 128 * 1024 * 1024
BACKUP_NAME_RE = re.compile(
    r"relaycat-(?:backup|pre-restore)-\d{8}T\d{6}Z-[a-f0-9]{8}\.zip\Z"
)

_MODELS = (
    User,
    Setting,
    Rule,
    AuditLog,
    MessageRoute,
    VerificationChallenge,
)
_MODELS_BY_TABLE = {model.__tablename__: model for model in _MODELS}
_DELETE_ORDER = (
    VerificationChallenge,
    MessageRoute,
    AuditLog,
    Rule,
    Setting,
    User,
)
_backup_lock = asyncio.Lock()


class BackupError(ValueError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    name: str
    created_at: datetime
    size: int
    counts: dict[str, int]
    reason: str


@dataclass(frozen=True)
class RestoreResult:
    restored: BackupInfo
    rollback: BackupInfo


def backup_directory() -> Path:
    return Path(settings.data_dir).expanduser().resolve() / "backups"


def _ensure_backup_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _encode_row(row, model) -> dict[str, object]:
    encoded: dict[str, object] = {}
    for column in model.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            encoded[column.name] = value.isoformat(timespec="microseconds") + "Z"
        else:
            encoded[column.name] = value
    return encoded


def _decode_datetime(value: object, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BackupError(f"invalid datetime in {field}")
    try:
        parsed = datetime.fromisoformat(value[:-1])
    except ValueError as exc:
        raise BackupError(f"invalid datetime in {field}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _decode_rows(table: str, rows: object) -> list[dict[str, object]]:
    model = _MODELS_BY_TABLE[table]
    if not isinstance(rows, list):
        raise BackupError(f"table {table} is not a list")
    expected = {column.name for column in model.__table__.columns}
    decoded: list[dict[str, object]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict) or set(raw) != expected:
            raise BackupError(f"table {table} row {index} has an invalid schema")
        item = dict(raw)
        for column in model.__table__.columns:
            if isinstance(column.type, DateTime):
                item[column.name] = _decode_datetime(
                    item[column.name], field=f"{table}.{column.name}"
                )
        decoded.append(item)
    return decoded


def _backup_filename(reason: str, created_at: datetime) -> str:
    prefix = "pre-restore" if reason == "pre-restore" else "backup"
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    return f"relaycat-{prefix}-{stamp}-{secrets.token_hex(4)}.zip"


def _write_archive(
    target: Path,
    *,
    manifest_bytes: bytes,
    data_bytes: bytes,
) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix=".relaycat-backup-",
        suffix=".tmp",
        dir=target.parent,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(
            temp_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("data.json", data_bytes)
        temp_path.chmod(0o600)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()


async def _create_backup_unlocked(
    *,
    reason: str,
    directory: Path,
    session_factory,
) -> BackupInfo:
    _ensure_backup_directory(directory)
    created_at = datetime.now(UTC).replace(tzinfo=None)
    tables: dict[str, list[dict[str, object]]] = {}
    async with session_factory() as session:
        async with session.begin():
            for model in _MODELS:
                rows = (await session.execute(select(model))).scalars().all()
                tables[model.__tablename__] = [_encode_row(row, model) for row in rows]

    data_document = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "tables": tables,
    }
    data_bytes = json.dumps(
        data_document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    counts = {table: len(rows) for table, rows in tables.items()}
    manifest = {
        "format": BACKUP_FORMAT,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": created_at.isoformat(timespec="seconds") + "Z",
        "reason": reason,
        "counts": counts,
        "data_sha256": hashlib.sha256(data_bytes).hexdigest(),
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    name = _backup_filename(reason, created_at)
    target = directory / name
    await asyncio.to_thread(
        _write_archive,
        target,
        manifest_bytes=manifest_bytes,
        data_bytes=data_bytes,
    )
    return BackupInfo(
        name=name,
        created_at=created_at,
        size=target.stat().st_size,
        counts=counts,
        reason=reason,
    )


async def create_backup(
    *,
    reason: str = "manual",
    directory: Path | None = None,
    session_factory=AsyncSessionLocal,
) -> BackupInfo:
    async with _backup_lock:
        return await _create_backup_unlocked(
            reason=reason,
            directory=(directory or backup_directory()).resolve(),
            session_factory=session_factory,
        )


def resolve_backup(name: str, *, directory: Path | None = None) -> Path:
    if not BACKUP_NAME_RE.fullmatch(name):
        raise BackupError("invalid backup name")
    root = (directory or backup_directory()).resolve()
    candidate = (root / name).resolve(strict=True)
    if candidate.parent != root or not candidate.is_file():
        raise BackupError("backup is outside the backup directory")
    return candidate


def _read_archive(path: Path) -> tuple[BackupInfo, dict[str, list[dict[str, object]]]]:
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise BackupError("backup archive is too large")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if {item.filename for item in infos} != {"manifest.json", "data.json"}:
                raise BackupError("backup archive has unexpected files")
            if any(item.file_size > MAX_BACKUP_BYTES for item in infos):
                raise BackupError("backup contents are too large")
            manifest_bytes = archive.read("manifest.json")
            data_bytes = archive.read("data.json")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise BackupError("backup archive is unreadable") from exc
    try:
        manifest = json.loads(manifest_bytes)
        document = json.loads(data_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup JSON is invalid") from exc
    if not isinstance(manifest, dict) or not isinstance(document, dict):
        raise BackupError("backup structure is invalid")
    if (
        manifest.get("format") != BACKUP_FORMAT
        or manifest.get("schema_version") != BACKUP_SCHEMA_VERSION
        or document.get("schema_version") != BACKUP_SCHEMA_VERSION
    ):
        raise BackupError("backup format version is unsupported")
    if manifest.get("data_sha256") != hashlib.sha256(data_bytes).hexdigest():
        raise BackupError("backup integrity check failed")
    raw_tables = document.get("tables")
    if not isinstance(raw_tables, dict) or set(raw_tables) != set(_MODELS_BY_TABLE):
        raise BackupError("backup table set is invalid")
    tables = {table: _decode_rows(table, rows) for table, rows in raw_tables.items()}
    counts = {table: len(rows) for table, rows in tables.items()}
    if manifest.get("counts") != counts:
        raise BackupError("backup record counts do not match")
    created_at = _decode_datetime(manifest.get("created_at"), field="created_at")
    if created_at is None:
        raise BackupError("backup creation time is missing")
    reason = str(manifest.get("reason") or "unknown")[:32]
    return (
        BackupInfo(
            name=path.name,
            created_at=created_at,
            size=path.stat().st_size,
            counts=counts,
            reason=reason,
        ),
        tables,
    )


async def list_backups(*, directory: Path | None = None) -> list[BackupInfo]:
    root = (directory or backup_directory()).resolve()
    _ensure_backup_directory(root)
    entries: list[BackupInfo] = []
    for path in root.iterdir():
        if not path.is_file() or not BACKUP_NAME_RE.fullmatch(path.name):
            continue
        try:
            info, _ = await asyncio.to_thread(_read_archive, path)
        except BackupError:
            continue
        entries.append(info)
    return sorted(entries, key=lambda item: item.created_at, reverse=True)


async def restore_backup(
    name: str,
    *,
    directory: Path | None = None,
    session_factory=AsyncSessionLocal,
) -> RestoreResult:
    root = (directory or backup_directory()).resolve()
    path = resolve_backup(name, directory=root)
    restored, tables = await asyncio.to_thread(_read_archive, path)
    async with _backup_lock:
        rollback = await _create_backup_unlocked(
            reason="pre-restore",
            directory=root,
            session_factory=session_factory,
        )
        async with session_factory() as session:
            async with session.begin():
                for model in _DELETE_ORDER:
                    await session.execute(delete(model))
                for model in _MODELS:
                    records = tables[model.__tablename__]
                    if records:
                        await session.execute(insert(model), records)
    return RestoreResult(restored=restored, rollback=rollback)
