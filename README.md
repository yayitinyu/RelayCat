# RelayCat

> 一个用于 Telegram 的轻量级“双向消息中继 Bot”，主打：**双向转发、防骚扰、易上手、免数据库**

- 用户只需和 Bot 私聊。
- RelayCat 会把消息原样转发给管理员。
- 管理员只要“回复转发消息或附带的信息卡片”，Bot 就会把回复发送回原用户（不是转发，而是正常消息）。
- 首次使用前，用户需要通过网页 reCAPTCHA 验证，防止脚本和恶意骚扰。

英文版请见：[`README_en.md`](./README_en.md)

---

## 目录

- [功能概览](#功能概览)
- [工作流程](#工作流程)
- [架构说明](#架构说明)
- [环境要求](#环境要求)
- [安装部署](#安装部署)
- [配置说明](#配置说明)
  - [Telegram / Webhook](#telegram--webhook)
  - [验证与 JWT](#验证与-jwt)
  - [数据存储](#数据存储)
  - [屏蔽词过滤](#屏蔽词过滤)
  - [限流](#限流)
- [运行时行为](#运行时行为)
  - [用户视角](#用户视角)
  - [管理员视角](#管理员视角)
- [安全注意事项](#安全注意事项)
- [常见问题 / 排错](#常见问题--排错)
- [后续扩展方向](#后续扩展方向)
- [许可证](#许可证)

---

## 功能概览

- **双向消息中继**
  - 所有“用户 → Bot”的私聊消息都会被转发到管理员账户。
  - 管理员回复那条转发消息或信息卡片，Bot 会把这条回复发送回对应用户。
  - 支持多种消息类型：文本、图片、文件、语音、贴纸等，均通过 Telegram API 原样转发/重发，而不是截图或拼接文本。

- **一次性人机验证**
  - 首次私聊时，用户必须通过一次 Google reCAPTCHA 验证。
  - 验证在 `verify.php` 上完成，验证成功后生成一次性 JWT，用户向 Bot 发送 `/start <token>` 完成绑定。
  - 验证状态持久化到 JSON 文件，之后不再重复验证（除非你清除数据）。

- **防骚扰 / 防滥用**
  - 支持“屏蔽词”过滤：命中后直接拦截该条消息，不会转发给管理员，也不会回显具体敏感词。
  - 支持用户封禁：`/ban` / `/unban`（`/allow` 同义）控制一个用户是否被接收消息。
  - 用户一旦被 ban，其任何指令和消息（包括 `/start`、`/help`）都不会再触发逻辑，但管理员仍可主动向其发消息。

- **对管理员友好**
  - 每条转发消息下面，会自动附上一条“用户信息卡片”：
    - `user_id`（用 `<code>` 包裹，方便复制）
    - `@username`（可能为空）
    - 用户姓名（`first_name last_name`，可能为空）
    - 如果是 Telegram Premium 用户，会在姓名后附加一个 ⭐
  - 管理员只需“回复转发消息/信息卡片”，就能直接回用户，无需手写 ID。

- **免数据库设计**
  - 使用 JSON 文件保存用户验证状态、路由映射（admin 消息 → 用户）、封禁名单、限流状态、屏蔽词等。
  - 不依赖 MySQL / PostgreSQL 等数据库，方便在小机器上部署与迁移。
  - 手动备份/查看也很简单（注意保护隐私）。

- **安全性有考虑**
  - 支持 Telegram Webhook `secret_token` 校验，防止他人伪造 Update 调用 `webhook.php`。
  - 验证页设置了 CSP、HSTS（在 HTTPS 下）、Referrer Policy、X-Frame-Options 等安全头。
  - 支持将所有 JSON 状态文件放在 Web 根目录之外。

---

## 工作流程

简化版时序：

1. **用户第一次给 Bot 发送消息**
   - 用户在私聊窗口向 Bot 发送 `/start` 或任意文本。
   - 如果该用户未通过验证，Bot 会回复一个验证链接：
     - `https://yourdomain.com/verify.php?token=<JWT>`

2. **打开验证页（`verify.php`）**
   - `verify.php` 解码 URL 中的 JWT，检查签名和过期时间，确认 `user_id`。
   - 显示 Google reCAPTCHA v2 小组件。

3. **用户完成 reCAPTCHA**
   - 前端将 `g-recaptcha-response` 提交给 `verify.php`。
   - 后端把该值发送到 Google `siteverify` 接口进行校验。
   - 成功后生成“成功 JWT”（`type=success`），仅包含 `user_id`、`verified=true` 和 `exp`。
   - 页面显示 `/start <success_jwt>`，并提供“一键复制”按钮，用户手动回到 Telegram 粘贴发送。

4. **用户向 Bot 发送 `/start <success_jwt>`**
   - Bot 解码这个成功 JWT，校验签名、过期时间、`type` 和 `user_id` 是否与当前用户一致。
   - 校验通过后，将该用户 ID 写入 `verified_users.json`。
   - Bot 回复“验证成功”，用户从此可以正常使用。

5. **正常使用阶段**
   - 用户发给 Bot 的所有消息：
     - 若通过屏蔽词、封禁等检查 → 转发给管理员。
     - 管理员回复那条转发消息/卡片 → Bot 将回复内容发回该用户。

---

## 架构说明

项目主要包含以下入口文件：

- `webhook.php`
  - Telegram Webhook 接口。
  - 处理所有 Update，包括：
    - 用户消息、管理员消息和各类命令；
    - 验证状态检查与 JWT 验证；
    - 消息转发和反向发送；
    - 屏蔽词过滤、封禁逻辑；
    - Webhook Secret 校验。
- `verify.php`
  - 浏览器访问的验证页。
  - 负责：
    - 解码“验证 JWT”，展示 reCAPTCHA；
    - 将 reCAPTCHA response 提交给 Google 校验；
    - 生成“成功 JWT”，展示 `/start <token>` 指令方便复制。

公共配置：

- `config.php`
  - 集中所有项目配置：
    - Bot Token、Admin ID；
    - Webhook Secret；
    - JWT Secret、reCAPTCHA 相关配置；
    - 数据存储目录、屏蔽词开关；
    - 限流参数等。

依赖（通过 Composer 安装）：

- `firebase/php-jwt`（用于 JWT 编解码）

---

## 环境要求

- PHP 8.0+（建议 8.1+）
- Composer（安装 PHP 依赖）
- 可运行 PHP 的 Web 服务器（Apache / Nginx + PHP-FPM 等）
  - 必须支持 HTTPS（有效证书，而非自签名）
- 一个 Telegram Bot（@BotFather 创建）
- 一组 Google reCAPTCHA v2 “我不是机器人”密钥（Site Key + Secret）

---

## 安装部署

1. **获取代码**

   ```bash
   git clone <your-repo-url> relaycat
   cd relaycat
   ```

2. **安装 PHP 依赖**

   ```bash
   composer require firebase/php-jwt
   ```

3. **准备数据目录**

   推荐将数据目录放在 Web 根之外，例如：

   ```text
   /var/www/html/relaycat       # 放 webhook.php / verify.php / config.php
   /var/www/relaycat-data       # 放 JSON、bad_words.txt 等数据文件
   ```

4. **配置 Web 服务器**

   * 将站点根目录指向包含 `webhook.php` 和 `verify.php` 的目录。
   * 确保 `https://yourdomain.com/webhook.php` 与 `https://yourdomain.com/verify.php` 可访问。
   * 禁用目录列表（Apache 中关闭 `Indexes`；Nginx 中不使用 `autoindex on;`）。
   * 如果无法将数据目录移到 Web 根之外，请在 Web 服务器上禁止直接访问 `.json` / `.tmp` 等文件。

5. **编辑 `config.php`**

   * 设置 Bot Token、Admin ID 等。
   * 设置 Webhook Secret、JWT Secret、reCAPTCHA 密钥、数据目录路径等。

6. **设置 Telegram Webhook**

   带 Webhook Secret 的示例：

   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -d "url=https://yourdomain.com/webhook.php" \
     -d "secret_token=<YOUR_TG_WEBHOOK_SECRET>"
   ```

7. **检查 Webhook 状态**

   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
   ```

   确认：

   * `url` 是你的 `webhook.php` 地址；
   * `last_error_message` 为空；
   * `pending_update_count` 不异常。

---

## 配置说明

以下仅列关键配置，完整参数请直接参考 `config.php`。

### Telegram / Webhook

```php
const BOT_TOKEN    = 'YOUR_TELEGRAM_BOT_TOKEN';
const BOT_USERNAME = 'YourBotUserName';  // 不带 @
const ADMIN_ID     = 123456789;          // 管理员（你的） Telegram 数字 ID

// 是否允许其他 Bot 主动向本 Bot 发私聊（一般不需要）
const ALLOW_BOT_INITIATED = false;
```

Webhook Secret：

```php
const TG_WEBHOOK_SECRET      = 'CHANGE_TO_A_LONG_RANDOM_SECRET';
const ENFORCE_WEBHOOK_SECRET = true; // 开启后必须校验 X-Telegram-Bot-Api-Secret-Token
```

> 设置 Webhook 时的 `secret_token` 必须与 `TG_WEBHOOK_SECRET` 一致。

### 验证与 JWT

```php
const SHARED_JWT_SECRET      = 'CHANGE_THIS_TO_A_LONG_RANDOM_SECRET';
const VERIFICATION_TOKEN_TTL = 600;  // 验证 JWT 有效期（秒）
const JWT_LEEWAY             = 300;  // 容忍的时间偏差（秒）

const RECAPTCHA_SITE_KEY   = 'YOUR_RECAPTCHA_SITE_KEY';
const RECAPTCHA_SECRET_KEY = 'YOUR_RECAPTCHA_SECRET_KEY';
const VERIFY_URL           = 'https://yourdomain.com/verify.php';
```

* `SHARED_JWT_SECRET`：`webhook.php` 与 `verify.php` 共同使用的签名密钥。
* `VERIFICATION_TOKEN_TTL + JWT_LEEWAY` 决定验证链接的大致有效时间窗口。
* `VERIFY_URL` 必须与用户访问的验证页地址一致。

### 数据存储

```php
const DATA_DIR            = '/var/www/relaycat-data';
const VERIFIED_USERS_FILE = DATA_DIR . '/verified_users.json';
const ROUTE_MAP_FILE      = DATA_DIR . '/routes.json';
const BANNED_USERS_FILE   = DATA_DIR . '/banned_users.json';
const RATE_LIMIT_FILE     = DATA_DIR . '/rate_limit.json';
const BAD_WORDS_FILE      = DATA_DIR . '/bad_words.txt';
```

* 推荐 `DATA_DIR` 放在 Web 根之外。
* PHP 进程需要对该目录拥有读写权限（至少 `rw`）。

### 屏蔽词过滤

```php
const BAD_WORDS_IGNORE_CASE     = true;  // 忽略大小写
const BAD_WORDS_ENABLE_WILDCARD = true;  // 支持 * / ? 通配
const BAD_WORDS_ENABLE_REGEX    = false; // 每行作为正则（实验性）
```

* `bad_words.txt`：一行一个屏蔽词或模式。
* 如果启用正则模式，每行会被直接拼入 PCRE，需自己保证合法。
* 命中屏蔽词时：

  * 消息不会被转发给管理员；
  * 用户只会得到一条“包含被屏蔽内容”的提示，而不会看到具体词条。

### 限流

简单的“按用户滑动窗口限流”：

```php
const RATE_LIMIT_ENABLED    = true;
const RATE_LIMIT_WINDOW_SEC = 10;  // 时间窗口（秒）
const RATE_LIMIT_MAX_EVENTS = 30;  // 每个用户在窗口内允许的最大消息数
```

* 若某用户在 10 秒内发送超过 30 条消息，后续消息会被静默丢弃，直到落回阈值以下。
* 仅对“单一用户的高频刷消息”生效，不会互相影响。
* 大规模部署建议结合 Nginx / Cloudflare 等在网络层做限流。

---

## 运行时行为

### 用户视角

* `/start`

  * 未验证用户：

    * 收到包含验证链接的提示，点击打开 `verify.php` 完成人机验证。
  * 已验证用户：

    * 收到“可以直接发送消息”的提示。

* `/help`

  * 未验证：

    * 简要说明 + 验证链接。
  * 已验证：

    * 说明“消息会被转发给管理员，管理员回复即会通过 Bot 返回”。

* 普通消息

  * 已验证且未被 ban：

    * 消息被转发给管理员。
  * 未验证：

    * 收到“请先完成验证”的提示。
  * 已被 ban：

    * 消息完全被忽略，不会收到回复。

### 管理员视角

`ADMIN_ID` 对应账户的特殊行为：

* `/help`

  * 显示管理员帮助：

    * 各种管理命令说明（/ban, /unban, /banlist, /badadd, /baddel 等）。

* 收到用户消息（通过验证/过滤后）

  * Bot 会先使用 `forwardMessage` 将原始消息转发给管理员。
  * 然后再发送一条“用户信息卡片”回复在该转发消息下，包含：

    * `user_id`（用 `<code>` 包裹的纯数字）
    * `@username`（或“（无）”）
    * 姓名（空则显示“（无）”，Premium 用户加 ⭐）
  * Bot 会把这两条消息的 `message_id` 与用户 ID 建立路由映射，用于后续回复。

* 回复消息

  * 管理员只要“回复那条转发的消息”或者“回复信息卡片”，Bot 就会：

    * 识别对应用户；
    * 将回复内容以正常消息形式发送给原用户（按消息类型选择 `sendMessage` / `sendPhoto` 等）；
    * 当原消息已不存在时，会尝试去掉 `reply_to_message_id` 再重发，并向管理员说明。

* 管理命令（仅管理员有效）

  * Ban / Unban：

    ```text
    /ban <user_id>           # 通过 ID 封禁
    /ban                     # 在“回复转发消息/信息卡片”时发送，封禁对应用户
    /unban <user_id>         # 解封
    /allow <user_id>         # /unban 的别名
    ```

  * 查看封禁名单：

    ```text
    /banlist
    ```

  * 屏蔽词（基础管理）：

    ```text
    /badadd <词条或模式>
    /baddel <词条或模式>
    ```

    这部分功能偏“应急/小改动”，大量调整时仍推荐直接编辑 `bad_words.txt`。

---

## 安全注意事项

* **Webhook 来源校验**

  * 通过 `TG_WEBHOOK_SECRET + ENFORCE_WEBHOOK_SECRET` 校验 Telegram 请求的 `X-Telegram-Bot-Api-Secret-Token`。
  * 未携带或不匹配的请求直接返回 403，避免他人伪造 Update 调用你的 `webhook.php`。

* **仅支持 HTTPS**

  * `VERIFY_URL` 必须为 HTTPS。
  * `verify.php` 在检测到 HTTPS 时会自动开启 HSTS 头，强化浏览器安全。

* **验证页安全头**

  * 严格的 CSP：仅允许本域和 Google reCAPTCHA 相关域名的脚本与资源。
  * `X-Frame-Options: DENY`，防止被嵌入到 iframe 中进行点击劫持。
  * `Referrer-Policy: no-referrer`，减少 Token 等信息的潜在泄露。
  * 禁用缓存：`Cache-Control: no-store, no-cache, must-revalidate`。

* **数据文件存放位置**

  * 最推荐：`DATA_DIR` 位于 Web 根之外（HTTP 无法直接访问）。
  * 如确实需要放在 Web 根中，请通过服务器配置阻止对 `.json` / `.tmp` 等文件的直接访问。

* **最小权限原则**

  * 管理命令只对 `ADMIN_ID` 生效。
  * 被 ban 的用户无法触发任何命令或消息转发逻辑，但 Bot 仍可以向其发送消息。

---

## 常见问题 / 排错

* **Webhook 已设置，但 Bot 没反应**

  1. 查看 Webhook 状态：

     ```bash
     curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
     ```

  2. 重点看：

     * `url` 是否指向 `webhook.php`，而不是 `verify.php`。
     * `last_error_message` 是否为空：

       * 如果包含 `Forbidden` 或 `403`，很可能是 `secret_token` 与 `TG_WEBHOOK_SECRET` 不一致。
       * 如果是 `500` 或 `wrong response`，查看 Web 服务器 `error_log`，多半是 PHP 运行错误或权限问题。

* **验证链接提示 Token 无效/过期**

  * 检查服务器的系统时间和时区是否正确。
  * 开启 DEBUG 模式后，页面会显示当前 PHP 时间与 Token 的 exp 时间（Unix 时间戳和 ISO 格式），便于比对。

* **JSON 文件无法写入**

  * 确认 `DATA_DIR` 存在且 Web 服务器用户对其有写权限。
  * 查看错误日志中是否有 `Permission denied` 等信息。

* **屏蔽词不生效**

  * 检查 `BAD_WORDS_FILE` 路径是否正确。
  * 确认文件内容不为空，且没有被注释掉。
  * 如果启用了 regex 模式，确保正则表达式语法正确。

---

## 后续扩展方向

如果你想在 RelayCat 的基础上继续拓展，可以考虑：

* 多管理员支持 / 分组路由策略。
* 给用户添加备注标签（例如“已付款”、“黑名单”等）。
* 简单 Web 面板，用于查看路由记录和封禁名单。
* 使用 Redis / 数据库替代 JSON 做持久化和限流。
* 支持把媒体相册（media group）作为一个整体转发和回复。

---

## 许可证

本项目使用 MIT License 发布。

你可以自由地将 RelayCat 用于个人或商业项目，也可以修改和再发布。
如果你发布了基于 RelayCat 的改进版本，欢迎在文档中简单提及原项目名称以示来源。

