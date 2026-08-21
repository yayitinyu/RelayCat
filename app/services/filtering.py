from dataclasses import dataclass
import re
import unicodedata
from typing import Any

import regex as safe_regex
from sqlalchemy import case, select

from app.database.core import AsyncSessionLocal
from app.database.models import Rule, User

RULE_TYPES = {
    "message_content",
    "username",
    "sender_name",
    "is_command",
    "is_forwarded",
    "has_link",
    "message_type",
}
RULE_ACTIONS = {"allow", "block", "drop"}
MATCH_MODES = {"contains_any", "equals", "starts_with", "ends_with", "regex"}
BOOLEAN_RULE_TYPES = {"is_forwarded", "has_link"}

RULE_TYPE_LABELS = {
    "message_content": "消息正文",
    "username": "用户名",
    "sender_name": "显示名称",
    "is_command": "Bot 命令",
    "is_forwarded": "转发消息",
    "has_link": "含有链接",
    "message_type": "消息类型",
}
MATCH_MODE_LABELS = {
    "contains_any": "包含任一关键词",
    "equals": "完全相同",
    "starts_with": "以关键词开头",
    "ends_with": "以关键词结尾",
    "regex": "自定义正则",
}
ACTION_LABELS = {"allow": "直接放行", "block": "拦截并告知", "drop": "静默丢弃"}
MESSAGE_TYPE_LABELS = {
    "text": "纯文字",
    "photo": "图片",
    "video": "视频",
    "document": "文件",
    "audio": "音频",
    "voice": "语音",
    "sticker": "贴纸",
    "animation": "动图",
    "contact": "联系人",
    "location": "位置",
    "other": "其他",
}

_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "α": "a",
        "в": "b",
        "β": "b",
        "ԁ": "d",
        "е": "e",
        "ε": "e",
        "ё": "e",
        "ɡ": "g",
        "і": "i",
        "ї": "i",
        "ι": "i",
        "ј": "j",
        "к": "k",
        "κ": "k",
        "ⅼ": "l",
        "м": "m",
        "μ": "m",
        "ν": "v",
        "о": "o",
        "ο": "o",
        "р": "p",
        "ρ": "p",
        "ԛ": "q",
        "с": "c",
        "ѕ": "s",
        "т": "t",
        "τ": "t",
        "ѵ": "v",
        "ԝ": "w",
        "х": "x",
        "χ": "x",
        "у": "y",
        "υ": "y",
        "ɑ": "a",
        "單": "单",
        "幣": "币",
        "號": "号",
        "聯": "联",
        "繫": "系",
        "賺": "赚",
        "貸": "贷",
        "資": "资",
        "穩": "稳",
        "賠": "赔",
        "磚": "砖",
    }
)
_LINK_PATTERN = re.compile(
    r"(?i)(?:https?://|www\.|(?:t|telegram)\.me/|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?:[/?:#][^\s<]*)?)"
)


@dataclass(frozen=True)
class RuleMatch:
    action: str
    rule_id: int | None = None
    rule_name: str | None = None


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold().translate(_CONFUSABLES)
    visible = "".join(
        char
        for char in normalized
        if unicodedata.category(char) not in {"Cf", "Mn", "Me"}
    )
    return " ".join(visible.split())


def compact_text(value: str) -> str:
    return "".join(char for char in normalize_text(value) if char.isalnum())


def keyword_values(pattern: str) -> list[str]:
    return [
        normalize_text(item)
        for item in re.split(r"[\r\n,，;；]+", pattern)
        if item.strip()
    ]


def detect_message_type(message: Any) -> str:
    for message_type in (
        "photo",
        "video",
        "document",
        "audio",
        "voice",
        "sticker",
        "animation",
        "contact",
        "location",
    ):
        if getattr(message, message_type, None):
            return message_type
    if getattr(message, "text", None) or getattr(message, "caption", None):
        return "text"
    return "other"


def message_has_link(message: Any) -> bool:
    entities = [
        *(getattr(message, "entities", None) or []),
        *(getattr(message, "caption_entities", None) or []),
    ]
    if any(
        getattr(entity, "type", None) in {"url", "text_link"} for entity in entities
    ):
        return True
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    normalized = normalize_text(text)
    normalized = re.sub(
        r"\[\s*(?:\.|dot|点)\s*\]|\(\s*(?:\.|dot|点)\s*\)|"
        r"(?<=\w)\s+(?:dot|点)\s+(?=\w)",
        ".",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bh\s*t\s*t\s*p\s*(s?)\s*:",
        lambda match: f"http{match.group(1)}:",
        normalized,
    )
    normalized = re.sub(
        r"\bh\s*x\s*x\s*p\s*(s?)\s*:",
        lambda match: f"http{match.group(1)}:",
        normalized,
    )
    normalized = re.sub(r"\bw\s*w\s*w\s*\.", "www.", normalized)
    normalized = re.sub(r"\s*([./:])\s*", r"\1", normalized)
    return bool(_LINK_PATTERN.search(normalized))


def _command_value(message: Any) -> str:
    text = (getattr(message, "text", None) or "").strip()
    if not text.startswith("/"):
        return ""
    return text.split(maxsplit=1)[0].removeprefix("/").split("@", 1)[0]


def is_bot_command(message: Any) -> bool:
    return bool(_command_value(message))


def rule_value(rule_type: str, message: Any, user: User) -> str:
    if rule_type == "message_content":
        return getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if rule_type == "username":
        return user.username or ""
    if rule_type == "sender_name":
        return " ".join(part for part in (user.first_name, user.last_name) if part)
    if rule_type == "is_command":
        return _command_value(message)
    if rule_type == "is_forwarded":
        return "true" if getattr(message, "forward_origin", None) else "false"
    if rule_type == "has_link":
        return "true" if message_has_link(message) else "false"
    if rule_type == "message_type":
        return detect_message_type(message)
    return ""


def matches_pattern(value: str, pattern: str, match_mode: str) -> bool:
    normalized_value = normalize_text(value)
    if match_mode == "regex":
        try:
            return bool(
                safe_regex.search(
                    pattern,
                    normalized_value[:8000],
                    flags=safe_regex.IGNORECASE,
                    timeout=0.05,
                )
            )
        except (safe_regex.error, TimeoutError):
            return False

    values = keyword_values(pattern)
    if not values:
        return False
    compact_value = compact_text(normalized_value)

    def matches(item: str) -> bool:
        pairs = [(normalized_value, item)]
        compact_item = compact_text(item)
        if len(compact_item) >= 2:
            pairs.append((compact_value, compact_item))
        if match_mode == "equals":
            return any(candidate == expected for candidate, expected in pairs)
        if match_mode == "starts_with":
            return any(candidate.startswith(expected) for candidate, expected in pairs)
        if match_mode == "ends_with":
            return any(candidate.endswith(expected) for candidate, expected in pairs)
        return any(expected in candidate for candidate, expected in pairs)

    return any(matches(item) for item in values)


def rule_matches(rule: Rule, message: Any, user: User) -> bool:
    return matches_pattern(
        rule_value(rule.rule_type, message, user),
        rule.pattern,
        rule.match_mode or "regex",
    )


async def evaluate_rules(message: Any, user: User) -> RuleMatch:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Rule)
            .where(Rule.is_active.is_(True))
            .order_by(case((Rule.action == "allow", 0), else_=1), Rule.id.asc())
        )
        rules = result.scalars().all()

    for rule in rules:
        if rule_matches(rule, message, user):
            return RuleMatch(rule.action, rule.id, rule.name)
    return RuleMatch("allow")


def validate_rule_values(
    rule_type: str,
    pattern: str,
    action: str,
    match_mode: str,
) -> str | None:
    if rule_type not in RULE_TYPES or action not in RULE_ACTIONS:
        return "规则范围或处理方式无效"
    if match_mode not in MATCH_MODES:
        return "匹配方式无效"
    if not pattern.strip() or len(pattern) > 500:
        return "规则内容必须为 1–500 个字符"
    if rule_type in BOOLEAN_RULE_TYPES and normalize_text(pattern) not in {
        "true",
        "false",
    }:
        return "开关型规则只能匹配“是”或“否”"
    if rule_type in BOOLEAN_RULE_TYPES and match_mode != "equals":
        return "开关型规则只能使用完全匹配"
    if (
        rule_type == "message_type"
        and normalize_text(pattern) not in MESSAGE_TYPE_LABELS
    ):
        return "消息类型无效"
    if rule_type == "message_type" and match_mode != "equals":
        return "消息类型只能使用完全匹配"
    if match_mode == "regex":
        try:
            safe_regex.compile(pattern)
        except safe_regex.error:
            return "正则表达式格式无效"
    return None
