<?php
declare(strict_types=1);

/**
 * 全局配置（安全加固版）
 * webhook.php / verify.php 都会 include 本文件
 */

/**
 * 读取环境变量的便捷函数，配合 Docker 等无状态部署使用。
 */
function env_string(string $key, ?string $default = null): ?string
{
    $value = getenv($key);
    if ($value === false) return $default;
    $value = trim((string)$value);
    return $value === '' ? $default : $value;
}

function env_int(string $key, int $default): int
{
    $value = env_string($key);
    return $value === null ? $default : (int)$value;
}

function env_bool(string $key, bool $default): bool
{
    $value = env_string($key);
    if ($value === null) return $default;
    $filtered = filter_var($value, FILTER_VALIDATE_BOOLEAN, FILTER_NULL_ON_FAILURE);
    return $filtered ?? $default;
}

function define_env(string $name, $value): void
{
    if (!defined($name)) {
        define($name, $value);
    }
}

// ==================== 调试开关（生产务必 false） ====================
define_env('DEBUG', env_bool('RELAYCAT_DEBUG', false)); // 开发可改 true

// ==================== Telegram Bot 基本配置 ====================
define_env('BOT_TOKEN', env_string('RELAYCAT_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN'));
define_env('BOT_USERNAME', env_string('RELAYCAT_BOT_USERNAME', 'YourBotUserName'));   // 不带 @，如 MyAntiSpamBot
define_env('ADMIN_ID', env_int('RELAYCAT_ADMIN_ID', 123456789));           // 你的 Telegram 数字 ID

// 仅私聊 & 拒绝其他 Bot 主动来信
define_env('ALLOW_BOT_INITIATED', env_bool('RELAYCAT_ALLOW_BOT_INITIATED', false));

// ==================== Webhook 来源校验（强制） ====================
// setWebhook 时使用同一密钥：?secret_token=CHANGE_TO_A_LONG_RANDOM_SECRET
define_env('TG_WEBHOOK_SECRET', env_string('RELAYCAT_TG_WEBHOOK_SECRET', 'CHANGE_TO_A_LONG_RANDOM_SECRET'));
define_env('ENFORCE_WEBHOOK_SECRET', env_bool('RELAYCAT_ENFORCE_WEBHOOK_SECRET', true)); // true=强制校验请求头

// ==================== 数据与状态文件（移出 Web 根） ====================
define_env('DATA_DIR', env_string('RELAYCAT_DATA_DIR', __DIR__ . '/botdata'));
define_env('VERIFIED_USERS_FILE', env_string('RELAYCAT_VERIFIED_USERS_FILE', DATA_DIR . '/verified_users.json'));
define_env('ROUTE_MAP_FILE', env_string('RELAYCAT_ROUTE_MAP_FILE', DATA_DIR . '/routes.json'));
define_env('BANNED_USERS_FILE', env_string('RELAYCAT_BANNED_USERS_FILE', DATA_DIR . '/banned_users.json'));
define_env('RATE_LIMIT_FILE', env_string('RELAYCAT_RATE_LIMIT_FILE', DATA_DIR . '/rate_limit.json'));

// 路由表清理策略（webhook.php 的 route_save 会用到）
define_env('ROUTE_TTL_SECONDS', env_int('RELAYCAT_ROUTE_TTL_SECONDS', 7 * 24 * 60 * 60)); // 7 天内未使用的路由记录会被清理
define_env('ROUTE_MAX_ENTRIES', env_int('RELAYCAT_ROUTE_MAX_ENTRIES', 20000));           // 最多保留 2 万条路由映射

// ==================== 速率限制（防风暴/DoS） ====================
define_env('RATE_LIMIT_ENABLED', env_bool('RELAYCAT_RATE_LIMIT_ENABLED', true));
define_env('RATE_LIMIT_WINDOW_SEC', env_int('RELAYCAT_RATE_LIMIT_WINDOW_SEC', 10));    // 滑窗：10 秒
define_env('RATE_LIMIT_MAX_EVENTS', env_int('RELAYCAT_RATE_LIMIT_MAX_EVENTS', 30));    // 每窗最多 30 条

// ==================== JWT / 验证配置 ====================
define_env('SHARED_JWT_SECRET', env_string('RELAYCAT_SHARED_JWT_SECRET', 'CHANGE_THIS_TO_A_LONG_RANDOM_SECRET'));
define_env('VERIFICATION_TOKEN_TTL', env_int('RELAYCAT_VERIFICATION_TOKEN_TTL', 600));   // 验证 JWT 有效期（秒）
define_env('JWT_LEEWAY', env_int('RELAYCAT_JWT_LEEWAY', 300));   // 解码容忍（秒）

// ==================== reCAPTCHA 配置 ====================
define_env('RECAPTCHA_SITE_KEY', env_string('RELAYCAT_RECAPTCHA_SITE_KEY', 'YOUR_RECAPTCHA_SITE_KEY'));
define_env('RECAPTCHA_SECRET_KEY', env_string('RELAYCAT_RECAPTCHA_SECRET_KEY', 'YOUR_RECAPTCHA_SECRET_KEY'));
define_env('VERIFY_URL', env_string('RELAYCAT_VERIFY_URL', 'https://yourdomain.com/verify.php')); // HTTPS

// ==================== 屏蔽词（文件可在 Web 根外；为空则忽略） ====================
define_env('BAD_WORDS_FILE', env_string('RELAYCAT_BAD_WORDS_FILE', DATA_DIR . '/bad_words.txt'));
define_env('BAD_WORDS_IGNORE_CASE', env_bool('RELAYCAT_BAD_WORDS_IGNORE_CASE', true));
define_env('BAD_WORDS_ENABLE_WILDCARD', env_bool('RELAYCAT_BAD_WORDS_ENABLE_WILDCARD', true));
define_env('BAD_WORDS_ENABLE_REGEX', env_bool('RELAYCAT_BAD_WORDS_ENABLE_REGEX', false));

// ==================== 全局 PHP 错误/日志 ====================
if (DEBUG) {
    ini_set('display_errors', '1');
    error_reporting(E_ALL);
} else {
    ini_set('display_errors', '0');
    error_reporting(E_ERROR | E_PARSE | E_CORE_ERROR | E_COMPILE_ERROR);
}
ini_set('log_errors', '1');

/** 仅在 DEBUG 写入 error_log 的便捷函数 */
function debug_log(string $message): void
{
    if (DEBUG) error_log($message);
}

/** 确保目录存在 */
function ensure_dir(string $dir): void
{
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
}
