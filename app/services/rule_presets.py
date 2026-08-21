from dataclasses import dataclass


@dataclass(frozen=True)
class RulePreset:
    preset_id: str
    version: int
    name: str
    description: str
    rule_type: str
    match_mode: str
    pattern: str
    action: str = "block"
    enabled_by_default: bool = True
    legacy_patterns: tuple[str, ...] = ()


RULE_PRESETS = {
    "scam_solicitation": RulePreset(
        preset_id="scam_solicitation",
        version=2,
        name="诈骗与资金盘话术",
        description="刷单返利、跑分、博彩、虚拟币搬砖和高收益招揽。",
        rule_type="message_content",
        match_mode="contains_any",
        pattern=(
            "刷单返利\n兼职刷单\n点赞返现\n跑分洗钱\n代收代付\n稳赚不赔\n"
            "保本高收益\n内幕带单\n带单老师\n博彩平台\n彩票导师\n裸聊敲诈\n"
            "代充返利\nUSDT搬砖\n虚拟币搬砖\n投资群\n杀猪盘\n资金盘\n高额回报"
        ),
        legacy_patterns=(
            "刷单\n跑分\n稳赚不赔\n博彩平台\n裸聊\n代充返利\nUSDT 搬砖\n带单老师",
        ),
    ),
    "risky_links": RulePreset(
        preset_id="risky_links",
        version=2,
        name="邀请与短链接",
        description="Telegram 邀请链接和常被滥用的短链接，包含常见混淆写法。",
        rule_type="message_content",
        match_mode="regex",
        pattern=(
            r"(?:t(?:elegram)?[\W_]*\.[\W_]*me|telegram[\W_]*me)[\W_]*"
            r"(?:joinchat|join|\+)|(?:bit[\W_]*ly|tinyurl[\W_]*com|t[\W_]*co|"
            r"cutt[\W_]*ly|shorturl[\W_]*at|rebrand[\W_]*ly)[\W_]*(?:/|$)"
        ),
        legacy_patterns=(
            r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)|(?:https?://)?(?:bit\.ly|tinyurl\.com)/",
        ),
    ),
    "contact_diversion": RulePreset(
        preset_id="contact_diversion",
        version=2,
        name="站外导流",
        description="诱导添加微信、QQ、WhatsApp 或 Telegram 小号。",
        rule_type="message_content",
        match_mode="regex",
        pattern=(
            r"(?:加|联系|私聊|添加|搜索|扫码).{0,10}(?:微[\W_]*信|v[\W_]*x|"
            r"v[\W_]*信|q[\W_]*q|w(?:hat)?s[\W_]*app|飞[\W_]*机|"
            r"纸[\W_]*飞[\W_]*机|t[\W_]*g)|(?:客服|助理|老师).{0,10}"
            r"(?:微[\W_]*信|v[\W_]*x|q[\W_]*q|t[\W_]*g)"
        ),
        legacy_patterns=("加微信\n加V详聊\n私聊返利\n联系客服领\n进群领取",),
    ),
    "credential_theft": RulePreset(
        preset_id="credential_theft",
        version=1,
        name="凭据与转账索取",
        description="索取验证码、助记词、私钥，或要求向所谓安全账户付款。",
        rule_type="message_content",
        match_mode="regex",
        pattern=(
            r"(?:发送|提供|告诉|提交|填写|索取|需要).{0,10}"
            r"(?:验证码|登录码|助记词|私钥|钱包密钥)|"
            r"(?:转入|转到|汇入).{0,10}(?:安全账户|指定账户)|"
            r"(?:缴纳|支付|先付).{0,10}(?:保证金|认证金|解冻金)"
        ),
    ),
    "mass_marketing": RulePreset(
        preset_id="mass_marketing",
        version=1,
        name="批量营销招揽",
        description="高薪日结、免费领取、代理招募和精准引流等群发话术。",
        rule_type="message_content",
        match_mode="contains_any",
        pattern=(
            "免费领取\n限时福利\n内部名额\n零门槛兼职\n在家兼职\n高薪日结\n"
            "日结兼职\n推广合作\n代理招募\n渠道合作\n资源互换\n精准引流"
        ),
    ),
    "recovery_scam": RulePreset(
        preset_id="recovery_scam",
        version=1,
        name="追款与解冻骗局",
        description="冒充黑客或维权团队，声称能追回损失、解冻账户或退款。",
        rule_type="message_content",
        match_mode="regex",
        pattern=(
            r"(?:追回|找回|挽回|解冻|退回).{0,10}(?:被骗|损失|资金|款项|账户)|"
            r"(?:黑客|技术团队|维权团队).{0,10}(?:追款|追回|找回|退款)|"
            r"被骗.{0,10}(?:联系|私聊|加)"
        ),
    ),
    "loan_scam": RulePreset(
        preset_id="loan_scam",
        version=1,
        name="贷款与征信骗局",
        description="无抵押秒下款、刷流水、包装征信和强开额度等话术。",
        rule_type="message_content",
        match_mode="contains_any",
        pattern=(
            "无抵押贷款\n免征信贷款\n黑户贷款\n秒批秒下\n秒下款\n"
            "包装征信\n包装流水\n刷流水\n强开额度\n内部提额\n征信修复"
        ),
    ),
    "support_impersonation": RulePreset(
        preset_id="support_impersonation",
        version=2,
        name="客服身份冒充",
        description="用户名伪装成管理员、客服、官方或安全团队。",
        rule_type="username",
        match_mode="regex",
        pattern=(
            r"(?:^|[_.-])(?:admin|support|service|customer|official|helpdesk|security)"
            r"(?:[_.-]|\d|$)"
        ),
        legacy_patterns=(r"(?:^|[_.-])(?:admin|support|service|customer)(?:[_.-]|$)",),
    ),
    "forwarded_messages": RulePreset(
        preset_id="forwarded_messages",
        version=1,
        name="转发消息",
        description="拦截所有带转发来源的消息。",
        rule_type="is_forwarded",
        match_mode="equals",
        pattern="true",
        enabled_by_default=False,
    ),
    "all_links": RulePreset(
        preset_id="all_links",
        version=1,
        name="所有外部链接",
        description="拦截任何网页链接，适合不接受链接的场景。",
        rule_type="has_link",
        match_mode="equals",
        pattern="true",
        enabled_by_default=False,
    ),
}
