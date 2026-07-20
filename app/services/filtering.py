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

DEFAULT_MODERATION_POLICY = (
    "拦截明确的诈骗、钓鱼、恶意推广、色情招揽、暴力威胁、仇恨骚扰、"
    "索取密码或验证码、传播恶意软件的消息。正常咨询、批评、讨论敏感主题、"
    "引用风险词进行求助或举报时应放行。"
)

RULE_PRESETS = {
    "scam_solicitation": {
        "name": "高风险诈骗招揽",
        "description": "拦截刷单、跑分、虚假返利、博彩和虚拟币搬砖等常见话术。",
        "rule_type": "message_content",
        "match_mode": "contains_any",
        "pattern": "刷单\n跑分\n稳赚不赔\n博彩平台\n裸聊\n代充返利\nUSDT 搬砖\n带单老师",
        "action": "block",
    },
    "risky_links": {
        "name": "高风险邀请与短链接",
        "description": "拦截 Telegram 邀请链接和常被滥用的短链接。",
        "rule_type": "message_content",
        "match_mode": "regex",
        "pattern": r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)|(?:https?://)?(?:bit\.ly|tinyurl\.com)/",
        "action": "block",
    },
    "contact_diversion": {
        "name": "常见导流联系方式",
        "description": "拦截要求加微信、联系客服领福利或私聊返利的话术。",
        "rule_type": "message_content",
        "match_mode": "contains_any",
        "pattern": "加微信\n加V详聊\n私聊返利\n联系客服领\n进群领取",
        "action": "block",
    },
    "support_impersonation": {
        "name": "疑似客服身份冒充",
        "description": "拦截用户名中独立出现 admin、support、service 或 customer 的账号。",
        "rule_type": "username",
        "match_mode": "regex",
        "pattern": r"(?:^|[_.-])(?:admin|support|service|customer)(?:[_.-]|$)",
        "action": "block",
    },
    "forwarded_messages": {
        "name": "转发消息",
        "description": "拦截所有带转发来源的消息，适合不接收群发内容的场景。",
        "rule_type": "is_forwarded",
        "match_mode": "equals",
        "pattern": "true",
        "action": "block",
    },
    "all_links": {
        "name": "所有外部链接",
        "description": "拦截任何含网页链接的消息，强度较高，建议按需启用。",
        "rule_type": "has_link",
        "match_mode": "equals",
        "pattern": "true",
        "action": "block",
    },
}


@dataclass(frozen=True)
class RuleMatch:
    action: str
    rule_id: int | None = None
    rule_name: str | None = None


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


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
    if any(getattr(entity, "type", None) in {"url", "text_link"} for entity in entities):
        return True
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    return bool(
        re.search(r"(?i)(?:https?://|www\.|(?:t|telegram)\.me/)[^\s<]+", text)
    )


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
                    value[:8000],
                    flags=safe_regex.IGNORECASE,
                    timeout=0.05,
                )
            )
        except (safe_regex.error, TimeoutError):
            return False

    values = keyword_values(pattern)
    if not values:
        return False
    if match_mode == "equals":
        return any(normalized_value == item for item in values)
    if match_mode == "starts_with":
        return any(normalized_value.startswith(item) for item in values)
    if match_mode == "ends_with":
        return any(normalized_value.endswith(item) for item in values)
    return any(item in normalized_value for item in values)


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
    if rule_type in BOOLEAN_RULE_TYPES and normalize_text(pattern) not in {"true", "false"}:
        return "开关型规则只能匹配“是”或“否”"
    if rule_type == "message_type" and normalize_text(pattern) not in MESSAGE_TYPE_LABELS:
        return "消息类型无效"
    if match_mode == "regex":
        try:
            safe_regex.compile(pattern)
        except safe_regex.error:
            return "正则表达式格式无效"
    return None
