from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging

from sqlalchemy import select

from app.database.core import AsyncSessionLocal
from app.database.models import Setting
from app.core.config import settings
from app.core.secret_store import SecretDecryptionError, decrypt_secret

logger = logging.getLogger(__name__)

AI_MODEL_ID_MAX_LENGTH = 200
AI_MODEL_CATALOG_LIMIT = 500


@dataclass(frozen=True)
class AIProviderConfig:
    base_url: str
    api_key: str | None
    model: str
    source: str

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.model.strip() and self.base_url.strip())


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


async def get_int_setting(
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = await get_setting(key)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


async def get_settings(keys: set[str]) -> dict[str, str | None]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Setting).where(Setting.key.in_(keys)))
        found = {item.key: item.value for item in result.scalars()}
    return {key: found.get(key) for key in keys}


def clean_ai_model_id(value: object) -> str:
    model_id = str(value).strip()
    if (
        not model_id
        or len(model_id) > AI_MODEL_ID_MAX_LENGTH
        or any(ord(char) < 32 for char in model_id)
    ):
        raise ValueError(
            f"AI 模型名必须为 1–{AI_MODEL_ID_MAX_LENGTH} 个可见字符"
        )
    return model_id


def normalize_ai_models(values: list[object]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            model_id = clean_ai_model_id(value)
        except ValueError:
            continue
        if model_id not in seen:
            seen.add(model_id)
            models.append(model_id)
        if len(models) >= AI_MODEL_CATALOG_LIMIT:
            break
    return models


async def get_saved_ai_models(active_model: str | None = None) -> list[str]:
    raw = await get_setting("ai_models")
    stored: list[object] = []
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Saved AI model catalog is not valid JSON; ignoring it")
        else:
            if isinstance(payload, list):
                stored = payload
    if active_model:
        stored.insert(0, active_model)
    return normalize_ai_models(stored)


async def get_ai_provider_config() -> AIProviderConfig:
    values = await get_settings(
        {"ai_base_url", "ai_model", "ai_api_key_encrypted"}
    )
    managed_key: str | None = None
    if values["ai_api_key_encrypted"]:
        try:
            managed_key = decrypt_secret(values["ai_api_key_encrypted"] or "")
        except SecretDecryptionError:
            logger.exception(
                "Managed AI API key could not be decrypted; check RELAYCAT_SECRET_KEY"
            )

    environment_key = (
        settings.ai_api_key.get_secret_value() if settings.ai_api_key is not None else None
    )
    return AIProviderConfig(
        base_url=(values["ai_base_url"] or settings.ai_base_url).rstrip("/"),
        api_key=managed_key or environment_key,
        model=(values["ai_model"] or settings.ai_model).strip(),
        source="admin" if managed_key else ("environment" if environment_key else "none"),
    )


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
