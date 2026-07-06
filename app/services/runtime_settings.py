from collections.abc import Mapping

from sqlalchemy import select

from app.database.core import AsyncSessionLocal
from app.database.models import Setting


async def get_setting(key: str, default: str | None = None) -> str | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        return setting.value if setting and setting.value is not None else default


async def get_bool_setting(key: str, default: bool = False) -> bool:
    value = await get_setting(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def upsert_settings(values: Mapping[str, str]) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Setting).where(Setting.key.in_(values.keys())))
        existing = {item.key: item for item in result.scalars()}
        for key, value in values.items():
            if key in existing:
                existing[key].value = value
            else:
                session.add(Setting(key=key, value=value))
        await session.commit()
