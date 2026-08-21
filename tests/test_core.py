import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.core.config import Settings  # noqa: E402
from app.database.models import Rule, User  # noqa: E402
from app.services.filtering import (  # noqa: E402
    is_bot_command,
    matches_pattern,
    message_has_link,
    rule_matches,
)
from app.services.rule_presets import RULE_PRESETS  # noqa: E402
from app.web.rules import validate_rule  # noqa: E402


def make_settings(**overrides) -> Settings:
    values = {
        "bot_token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "admin_id": 123456789,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class SettingsTests(unittest.TestCase):
    def test_accepts_custom_port(self) -> None:
        self.assertEqual(make_settings(port=9180).port, 9180)

    def test_rejects_invalid_port(self) -> None:
        with self.assertRaises(ValidationError):
            make_settings(port=70000)


class RuleTests(unittest.TestCase):
    def test_rejects_invalid_regex(self) -> None:
        self.assertEqual(
            validate_rule("message_content", "([", "block"),
            "正则表达式格式无效",
        )

    def test_plain_keywords_are_not_parsed_as_regex(self) -> None:
        self.assertIsNone(
            validate_rule("message_content", "([\n广告", "block", "contains_any")
        )

    def test_matches_case_width_spacing_and_punctuation_evasion(self) -> None:
        rule = Rule(
            rule_type="message_content",
            match_mode="contains_any",
            pattern="USDT搬砖\n刷单返利",
            action="block",
        )
        user = User(id=1)
        self.assertTrue(
            rule_matches(
                rule,
                SimpleNamespace(text="Ｕ S\u200bD.T 搬 砖", caption=None),
                user,
            )
        )
        self.assertTrue(
            rule_matches(
                rule,
                SimpleNamespace(text="刷 · 单 · 返 · 利", caption=None),
                user,
            )
        )

    def test_detects_obfuscated_links(self) -> None:
        message = SimpleNamespace(
            text="hxxps://exa [.] mple.com/login",
            caption=None,
            entities=None,
            caption_entities=None,
        )
        self.assertTrue(message_has_link(message))

        spaced = SimpleNamespace(
            text="h t t p s : / / example dot com/login",
            caption=None,
            entities=None,
            caption_entities=None,
        )
        self.assertTrue(message_has_link(spaced))

    def test_detects_links_from_entities(self) -> None:
        message = SimpleNamespace(
            text="点这里",
            caption=None,
            entities=[SimpleNamespace(type="text_link")],
            caption_entities=None,
        )
        self.assertTrue(message_has_link(message))

    def test_detects_bot_command_with_username_suffix(self) -> None:
        message = SimpleNamespace(text="/help@RelayCatBot details")
        self.assertTrue(is_bot_command(message))

    def test_normalizes_common_homoglyphs_and_traditional_text(self) -> None:
        self.assertTrue(matches_pattern("UЅDT搬磚", "usdt搬砖", "contains_any"))
        self.assertTrue(matches_pattern("穩賺不賠", "稳赚不赔", "contains_any"))

    def test_security_presets_cover_common_evasion(self) -> None:
        risky_links = RULE_PRESETS["risky_links"]
        self.assertTrue(
            matches_pattern("t [.] me / + invite", risky_links.pattern, "regex")
        )
        credential_theft = RULE_PRESETS["credential_theft"]
        self.assertTrue(
            matches_pattern(
                "请先提供你的登录验证码",
                credential_theft.pattern,
                "regex",
            )
        )


class TemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = Environment(
            loader=FileSystemLoader("app/templates"),
            autoescape=select_autoescape(),
        )

    def test_plain_keywords_keep_line_separators_in_editor(self) -> None:
        rule = SimpleNamespace(
            id=1,
            is_active=True,
            name="常见导流联系方式",
            rule_type="message_content",
            match_mode="contains_any",
            pattern="加微信\n私聊返利",
            action="block",
            preset_id=None,
        )
        rendered = self.environment.get_template("rules.html").render(
            request=SimpleNamespace(query_params={}),
            active_path="/rules",
            page_title="过滤规则",
            csrf_token="test-token",
            error=None,
            rules=[rule],
            rule_presets={},
            existing_presets=set(),
            rule_type_labels={"message_content": "消息正文"},
            match_mode_labels={"contains_any": "包含任一项"},
            action_labels={"block": "拦截"},
            message_type_labels={},
        )
        self.assertIn(">加微信\n私聊返利</textarea>", rendered)
        self.assertNotIn('value="加微信\n私聊返利"', rendered)

    def test_removed_modules_are_absent_from_ui_and_dependencies(self) -> None:
        templates = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path("app/templates").glob("*.html")
        ).casefold()
        requirements = Path("requirements.txt").read_text(encoding="utf-8").casefold()
        for removed_term in (
            "openai",
            "business",
            "secretary",
            "httpx",
            "cryptography",
        ):
            self.assertNotIn(removed_term, templates)
            self.assertNotIn(removed_term, requirements)

    def test_brand_uses_main_font(self) -> None:
        css = Path("app/static/css/app.css").read_text(encoding="utf-8")
        base = Path("app/templates/base.html").read_text(encoding="utf-8")
        self.assertIn(".brand strong { font-family: inherit;", css)
        self.assertIn('font-family: "寒蝉全圆体"', css)
        self.assertNotIn("Lora", base)
        self.assertNotIn("Maple", base)


if __name__ == "__main__":
    unittest.main()
