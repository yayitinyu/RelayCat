<?php
declare(strict_types=1);

/**
 * Telegram Bot Webhook（安全加固版）
 * - 强制校验 X-Telegram-Bot-Api-Secret-Token
 * - 仅私聊 + 断言 chat.id === from.id
 * - 文件级速率限制
 * - 其它功能与之前一致：验证、转发/信息卡片、管理员回复回推、ban/unban/banlist、badadd/baddel、出站失败告警等
 */

require __DIR__ . '/config.php';
require __DIR__ . '/vendor/autoload.php';

use Firebase\JWT\JWT;
use Firebase\JWT\Key;

JWT::$leeway = JWT_LEEWAY;

// ---------- Header 工具 ----------
function header_value(string $name): ?string {
    $arr = function_exists('getallheaders') ? getallheaders() : [];
    foreach ($arr as $k => $v) {
        if (strtolower($k) === strtolower($name)) return is_string($v) ? $v : null;
    }
    // 兼容部分环境变量方式
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return $_SERVER[$key] ?? null;
}

// ---------- Webhook 来源校验（最高优先级） ----------
if (ENFORCE_WEBHOOK_SECRET) {
    $secret = header_value('X-Telegram-Bot-Api-Secret-Token');
    if (!is_string($secret) || $secret !== TG_WEBHOOK_SECRET) {
        http_response_code(403);
        echo 'Forbidden';
        exit;
    }
}

// ---------- 时间/转义/路径 ----------
function now_ts(): int { return time(); }
function ts_to_iso(int $ts): string { return date(DATE_ATOM, $ts); }
function h(string $s): string { return htmlspecialchars($s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
function abs_path(string $p): string {
    if ($p === '' || $p[0] === '/' || preg_match('~^[A-Za-z]:[\\\\/]~', $p)) return $p;
    return rtrim(__DIR__, '/\\') . '/' . ltrim($p, '/\\');
}

// ---------- 确保数据目录存在 ----------
ensure_dir(dirname(VERIFIED_USERS_FILE));
ensure_dir(dirname(ROUTE_MAP_FILE));
ensure_dir(dirname(BANNED_USERS_FILE));
ensure_dir(dirname(RATE_LIMIT_FILE));
ensure_dir(dirname(BAD_WORDS_FILE));

// ---------- 已验证用户 ----------
function load_verified_users(): array {
    if (!file_exists(VERIFIED_USERS_FILE)) return [];
    $j = file_get_contents(VERIFIED_USERS_FILE);
    $a = json_decode($j ?: '[]', true);
    return is_array($a) ? array_map('intval', $a) : [];
}
function save_verified_users(array $ids): void {
    $ids = array_values(array_unique(array_map('intval', $ids)));
    file_put_contents(VERIFIED_USERS_FILE . '.tmp', json_encode($ids, JSON_UNESCAPED_UNICODE));
    @rename(VERIFIED_USERS_FILE . '.tmp', VERIFIED_USERS_FILE);
}

// ---------- Ban / Unban ----------
function load_banned_users(): array {
    if (!file_exists(BANNED_USERS_FILE)) return [];
    $a = json_decode(file_get_contents(BANNED_USERS_FILE) ?: '[]', true);
    return is_array($a) ? array_map('intval', $a) : [];
}
function save_banned_users(array $ids): void {
    $ids = array_values(array_unique(array_map('intval', $ids)));
    file_put_contents(BANNED_USERS_FILE . '.tmp', json_encode($ids, JSON_UNESCAPED_UNICODE));
    @rename(BANNED_USERS_FILE . '.tmp', BANNED_USERS_FILE);
}
function is_banned(int $uid): bool {
    $list = load_banned_users();
    return in_array($uid, $list, true);
}
function ban_user(int $uid): bool {
    $list = load_banned_users();
    if (!in_array($uid, $list, true)) { $list[] = $uid; save_banned_users($list); return true; }
    return false;
}
function unban_user(int $uid): bool {
    $list = load_banned_users();
    $new  = array_values(array_filter($list, fn($x) => (int)$x !== $uid));
    save_banned_users($new);
    return count($new) !== count($list);
}

// ---------- 路由表 ----------
function route_load(): array {
    if (!file_exists(ROUTE_MAP_FILE)) return [];
    $a = json_decode(file_get_contents(ROUTE_MAP_FILE) ?: '[]', true);
    return is_array($a) ? $a : [];
}
function route_save(array $map): void {
    $now = now_ts();
    $map = array_filter($map, fn($v) => isset($v['ts']) && ($now - (int)$v['ts']) <= ROUTE_TTL_SECONDS);
    if (count($map) > ROUTE_MAX_ENTRIES) {
        uasort($map, fn($a,$b) => ($a['ts'] ?? 0) <=> ($b['ts'] ?? 0));
        $map = array_slice($map, -ROUTE_MAX_ENTRIES, null, true);
    }
    file_put_contents(ROUTE_MAP_FILE . '.tmp', json_encode($map, JSON_UNESCAPED_UNICODE));
    @rename(ROUTE_MAP_FILE . '.tmp', ROUTE_MAP_FILE);
}
function route_put(int $adminMsgId, int $userId, int $srcMsgId): void {
    $m = route_load();
    $m[(string)$adminMsgId] = ['user_id'=>$userId, 'src_msg_id'=>$srcMsgId, 'ts'=>now_ts()];
    route_save($m);
}
function route_get(int $adminMsgId): ?array {
    $m = route_load();
    return $m[(string)$adminMsgId] ?? null;
}

// ---------- Telegram API ----------
function tg_api(string $method, array $params): array {
    $url = 'https://api.telegram.org/bot' . BOT_TOKEN . '/' . $method;
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $params,
        CURLOPT_TIMEOUT        => 15,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);
    $resp = curl_exec($ch);
    $http = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    // curl_close($ch);

    if ($resp === false || $http !== 200) {
        error_log("[WEBHOOK] API {$method} failed HTTP={$http}, resp=" . substr((string)$resp, 0, 500));
        return ['ok' => false, 'error_code' => $http, 'description' => 'HTTP failure'];
    }
    $data = json_decode($resp, true);
    if (DEBUG) debug_log("[WEBHOOK] API {$method} OK: " . substr((string)$resp, 0, 500));
    return is_array($data) ? $data : ['ok' => false, 'description' => 'Invalid JSON'];
}
function tg_send_message(int $chatId, string $text, array $extra = []): ?int {
    $res = tg_api('sendMessage', array_merge(['chat_id'=>$chatId,'text'=>$text], $extra));
    return (($res['ok'] ?? false) && isset($res['result']['message_id'])) ? (int)$res['result']['message_id'] : null;
}
function tg_send_checked(string $method, array $params, int $toUserId, bool $retryWithoutReply = true): bool
{
    $res = tg_api($method, $params);
    if (($res['ok'] ?? false)) return true;

    $desc = (string)($res['description'] ?? '未知错误');
    if ($retryWithoutReply && isset($params['reply_to_message_id']) &&
        preg_match('~reply.*message.*not.*found~i', $desc)) {
        $p2 = $params; unset($p2['reply_to_message_id']); $p2['allow_sending_without_reply'] = true;
        $res2 = tg_api($method, $p2);
        if (($res2['ok'] ?? false)) {
            tg_send_message(ADMIN_ID, "ℹ️ 已改为不引用回复并成功发送给 user_id={$toUserId}。\n原错误：{$desc}");
            return true;
        }
        $desc .= " / 重试失败：" . (string)($res2['description'] ?? '未知错误');
    }
    $hint = stripos($desc, 'bot was blocked by the user') !== false ? "\n可能原因：对方已拉黑机器人。" : '';
    tg_send_message(ADMIN_ID, "❗️发送失败\nuser_id: {$toUserId}\n方法: {$method}\n错误: {$desc}{$hint}");
    return false;
}

// ---------- 屏蔽词 ----------
function bad_words_cfg(): array {
    $path = BAD_WORDS_FILE;
    if (!is_file($path)) return ['enabled'=>false,'mode'=>'substr','entries'=>[]];
    $lines = @file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    if (!$lines) return ['enabled'=>false,'mode'=>'substr','entries'=>[]];
    $lines = array_values(array_filter(array_map('trim', $lines), fn($s)=>$s!==''));
    if (!$lines) return ['enabled'=>false,'mode'=>'substr','entries'=>[]];

    if (BAD_WORDS_ENABLE_REGEX) {
        $entries=[]; foreach ($lines as $raw){ $entries[]=['type'=>'regex','raw'=>$raw,'pattern'=>'~'.$raw.'~u'.(BAD_WORDS_IGNORE_CASE?'i':'')]; }
        return ['enabled'=>true,'mode'=>'regex','entries'=>$entries];
    }
    if (BAD_WORDS_ENABLE_WILDCARD) {
        $entries=[]; foreach ($lines as $raw){
            $escaped=preg_quote($raw,'~'); $escaped=strtr($escaped,['\*'=>'.*','\?'=>'.']);
            $entries[]=['type'=>'wildcard','raw'=>$raw,'pattern'=>'~'.$escaped.'~u'.(BAD_WORDS_IGNORE_CASE?'i':'')];
        }
        return ['enabled'=>true,'mode'=>'wildcard','entries'=>$entries];
    }
    return ['enabled'=>true,'mode'=>'substr','entries'=>array_map(fn($r)=>['type'=>'substr','raw'=>$r],$lines)];
}
function bad_words_hit(?string $text): bool {
    if (!$text) return false;
    $cfg = bad_words_cfg(); if (!$cfg['enabled']) return false;
    foreach ($cfg['entries'] as $e) {
        if ($cfg['mode'] === 'regex' || $cfg['mode'] === 'wildcard') {
            if (@preg_match($e['pattern'], $text) === 1) return true;
        } else {
            if (BAD_WORDS_IGNORE_CASE ? (mb_stripos($text,$e['raw'],0,'UTF-8')!==false)
                                      : (mb_strpos($text,$e['raw'],0,'UTF-8')!==false)) return true;
        }
    }
    return false;
}
function bad_words_add(string $entry): bool {
    $entry=trim($entry); if ($entry==='') return false;
    $path=BAD_WORDS_FILE; ensure_dir(dirname($path));
    $lines = is_file($path)?(@file($path, FILE_IGNORE_NEW_LINES)?:[]):[];
    foreach ($lines as $line) if (trim($line)===$entry) return false;
    $ok=@file_put_contents($path, (empty($lines)?'':PHP_EOL).$entry.PHP_EOL, FILE_APPEND|LOCK_EX);
    return $ok!==false;
}
function bad_words_del(string $entry): bool {
    $entry=trim($entry); if ($entry==='') return false;
    $path=BAD_WORDS_FILE; if (!is_file($path)) return false;
    $lines=@file($path, FILE_IGNORE_NEW_LINES); if ($lines===false) return false;
    $out=[]; $changed=false; foreach ($lines as $line){ if (trim($line)===$entry){$changed=true;continue;} $out[]=$line; }
    if (!$changed) return false;
    $tmp=$path.'.tmp'; @file_put_contents($tmp, implode(PHP_EOL,$out).PHP_EOL, LOCK_EX); @rename($tmp,$path);
    return true;
}

// ---------- 管理员回复 → 发回原用户（非转发） ----------
function relay_admin_reply_to_user(int $toChatId, array $msg, ?int $replyToMsgId): bool
{
    $base=['chat_id'=>$toChatId,'allow_sending_without_reply'=>true];
    if ($replyToMsgId) $base['reply_to_message_id']=$replyToMsgId;

    if (isset($msg['text'])) {
        $p=$base+['text'=>$msg['text']]; if (!empty($msg['entities'])) $p['entities']=json_encode($msg['entities']);
        return tg_send_checked('sendMessage',$p,$toChatId);
    }
    if (isset($msg['photo'])) {
        $p=$base+['photo'=>end($msg['photo'])['file_id']]; if (isset($msg['caption'])) $p['caption']=$msg['caption'];
        if (!empty($msg['caption_entities'])) $p['caption_entities']=json_encode($msg['caption_entities']);
        return tg_send_checked('sendPhoto',$p,$toChatId);
    }
    if (isset($msg['document'])) {
        $p=$base+['document'=>$msg['document']['file_id']]; if (isset($msg['caption'])) $p['caption']=$msg['caption'];
        if (!empty($msg['caption_entities'])) $p['caption_entities']=json_encode($msg['caption_entities']);
        return tg_send_checked('sendDocument',$p,$toChatId);
    }
    if (isset($msg['video'])) {
        $p=$base+['video'=>$msg['video']['file_id']]; if (isset($msg['caption'])) $p['caption']=$msg['caption'];
        if (!empty($msg['caption_entities'])) $p['caption_entities']=json_encode($msg['caption_entities']);
        return tg_send_checked('sendVideo',$p,$toChatId);
    }
    if (isset($msg['audio'])) {
        $p=$base+['audio'=>$msg['audio']['file_id']]; if (isset($msg['caption'])) $p['caption']=$msg['caption'];
        if (!empty($msg['caption_entities'])) $p['caption_entities']=json_encode($msg['caption_entities']);
        return tg_send_checked('sendAudio',$p,$toChatId);
    }
    if (isset($msg['voice'])) {
        $p=$base+['voice'=>$msg['voice']['file_id']]; if (isset($msg['caption'])) $p['caption']=$msg['caption'];
        if (!empty($msg['caption_entities'])) $p['caption_entities']=json_encode($msg['caption_entities']);
        return tg_send_checked('sendVoice',$p,$toChatId);
    }
    if (isset($msg['animation'])) {
        $p=$base+['animation'=>$msg['animation']['file_id']]; if (isset($msg['caption'])) $p['caption']=$msg['caption'];
        if (!empty($msg['caption_entities'])) $p['caption_entities']=json_encode($msg['caption_entities']);
        return tg_send_checked('sendAnimation',$p,$toChatId);
    }
    if (isset($msg['sticker']))   return tg_send_checked('sendSticker',  $base+['sticker'=>$msg['sticker']['file_id']], $toChatId);
    if (isset($msg['video_note']))return tg_send_checked('sendVideoNote',$base+['video_note'=>$msg['video_note']['file_id']], $toChatId);
    if (isset($msg['contact']))   { $c=$msg['contact']; $p=$base+['phone_number'=>$c['phone_number']??'','first_name'=>$c['first_name']??'']; if(!empty($c['last_name']))$p['last_name']=$c['last_name']; if(!empty($c['vcard']))$p['vcard']=$c['vcard']; return tg_send_checked('sendContact',$p,$toChatId); }
    if (isset($msg['location']))  { $l=$msg['location']; return tg_send_checked('sendLocation',$base+['latitude'=>$l['latitude'],'longitude'=>$l['longitude']],$toChatId); }
    if (isset($msg['venue']))     { $v=$msg['venue']; $p=$base+['latitude'=>$v['location']['latitude'],'longitude'=>$v['location']['longitude'],'title'=>$v['title'],'address'=>$v['address']]; return tg_send_checked('sendVenue',$p,$toChatId); }
    if (isset($msg['dice']))      { $p=$base; if(!empty($msg['dice']['emoji'])) $p['emoji']=$msg['dice']['emoji']; return tg_send_checked('sendDice',$p,$toChatId); }

    tg_send_message(ADMIN_ID, "⚠️ 暂不支持将该类型消息回推给 user_id={$toChatId}。");
    return false;
}

// ---------- 速率限制（简单文件窗） ----------
function rate_limit_hit(int $uid): bool {
    if (!RATE_LIMIT_ENABLED) return false;
    $now = now_ts();
    $data = is_file(RATE_LIMIT_FILE) ? (json_decode(@file_get_contents(RATE_LIMIT_FILE)?:'[]', true) ?: []) : [];
    $key = (string)$uid;
    $arr = array_values(array_filter($data[$key] ?? [], fn($t)=>$now - (int)$t < RATE_LIMIT_WINDOW_SEC));
    $arr[] = $now;
    $data[$key] = $arr;
    // 剪枝总体体积
    foreach ($data as $k=>$list) {
        if (empty($list)) unset($data[$k]);
    }
    @file_put_contents(RATE_LIMIT_FILE, json_encode($data));
    return count($arr) > RATE_LIMIT_MAX_EVENTS;
}

// ---------- Webhook 主处理 ----------
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); echo 'Method Not Allowed'; exit; }
$raw = file_get_contents('php://input'); if (!$raw) { echo 'OK'; exit; }
$upd = json_decode($raw, true); if (!is_array($upd)) { echo 'OK'; exit; }
if (DEBUG) debug_log('[WEBHOOK] Update: ' . substr($raw, 0, 900));

if (!isset($upd['message'])) { echo 'OK'; exit; }
$msg  = $upd['message'];
$chat = $msg['chat'] ?? [];
$from = $msg['from'] ?? [];

$chatId = $chat['id'] ?? null;
$userId = $from['id'] ?? null;
$text   = $msg['text'] ?? null;
$caption= $msg['caption'] ?? null;

if (!is_int($chatId) || !is_int($userId)) { echo 'OK'; exit; }
// 仅私聊 & 私聊一致性守卫
if (($chat['type'] ?? '') !== 'private' || $chatId !== $userId) { echo 'OK'; exit; }

// 拒绝其他 Bot 主动来信（除非允许或是管理员）
$isAdmin = ($userId === ADMIN_ID);
if (!$isAdmin && !ALLOW_BOT_INITIATED && !empty($from['is_bot'])) { echo 'OK'; exit; }

// 被 Ban 用户：彻底忽略（包括 /start /help）
if (!$isAdmin && is_banned($userId)) { if (DEBUG) debug_log("[WEBHOOK] inbound from banned {$userId} ignored"); echo 'OK'; exit; }

// 速率限制（对所有入站应用；管理员可豁免）
if (!$isAdmin && rate_limit_hit($userId)) { if (DEBUG) debug_log("[WEBHOOK] rate limited {$userId}"); echo 'OK'; exit; }

// ========== 管理员指令 ==========
if ($isAdmin && is_string($text) && str_starts_with(trim($text), '/')) {
    $t = trim($text);

    // /help
    if (preg_match('~^/help\b~i', $t)) {
        $help = "🤖 <b>管理员帮助</b>\n"
              . "/help - 查看本帮助\n"
              . "/ban &lt;user_id&gt; 或在“回复转发/信息卡片”时发送 /ban - 屏蔽用户\n"
              . "/unban &lt;user_id&gt; 或 /allow &lt;user_id&gt; - 解封用户\n"
              . "/banlist - 查看封禁名单\n"
              . "/badadd &lt;词条&gt; - 添加屏蔽词（推荐直接编辑文件）\n"
              . "/baddel &lt;词条&gt; - 移除屏蔽词";
        tg_send_message(ADMIN_ID, $help, ['parse_mode'=>'HTML','disable_web_page_preview'=>true]);
        echo 'OK'; exit;
    }

    // /ban /unban|allow
    if (preg_match('~^/(ban|unban|allow)\b~i', $t, $m)) {
        $cmd  = strtolower($m[1]);
        $args = trim(preg_replace('~^/\w+\s*~', '', $t));
        $targetId = null;

        if ($args !== '' && preg_match('~^\d+$~', $args)) {
            $targetId = (int)$args;
        } elseif (isset($msg['reply_to_message'])) {
            $replyMid = (int)$msg['reply_to_message']['message_id'];
            if ($r = route_get($replyMid)) $targetId = (int)$r['user_id'];
        }

        if ($targetId === null) {
            tg_send_message(ADMIN_ID, "用法：\n/ban <user_id>  或“回复转发/信息卡片”发送 /ban\n/unban <user_id> 或 /allow <user_id>");
            echo 'OK'; exit;
        }

        if ($cmd === 'ban') {
            $changed = ban_user($targetId);
            tg_send_message(ADMIN_ID, $changed ? "🔒 已屏蔽 user_id={$targetId}" : "ℹ️ 已在屏蔽列表中");
        } else {
            $changed = unban_user($targetId);
            tg_send_message(ADMIN_ID, $changed ? "✅ 已解封 user_id={$targetId}" : "ℹ️ 不在屏蔽列表中");
        }
        echo 'OK'; exit;
    }

    // /banlist
    if (preg_match('~^/banlist\b~i', $t)) {
        $list = load_banned_users();
        if (!$list) { tg_send_message(ADMIN_ID, "当前封禁名单为空。"); }
        else {
            $maxShow=500; $count=count($list); $show=array_slice($list,0,$maxShow);
            $lines=array_map(fn($id)=>'<code>'.h((string)$id).'</code>',$show);
            $more=$count>$maxShow?("\n…以及 ".($count-$maxShow)." 个"):'';
            tg_send_message(ADMIN_ID, "🔒 当前封禁 {$count} 人：\n".implode("\n",$lines).$more, ['parse_mode'=>'HTML']);
        }
        echo 'OK'; exit;
    }

    // /badadd
    if (preg_match('~^/badadd\b~i', $t)) {
        $entry = trim(preg_replace('~^/badadd\s*~i', '', $t));
        if ($entry==='') tg_send_message(ADMIN_ID,"用法：/badadd <词条>（推荐直接编辑 ".BAD_WORDS_FILE."）");
        else tg_send_message(ADMIN_ID, bad_words_add($entry) ? "✅ 已添加" : "ℹ️ 未添加：可能已存在或写入失败。");
        echo 'OK'; exit;
    }
    // /baddel
    if (preg_match('~^/baddel\b~i', $t)) {
        $entry = trim(preg_replace('~^/baddel\s*~i', '', $t));
        if ($entry==='') tg_send_message(ADMIN_ID,"用法：/baddel <词条>（推荐直接编辑 ".BAD_WORDS_FILE."）");
        else tg_send_message(ADMIN_ID, bad_words_del($entry) ? "✅ 已移除" : "ℹ️ 未移除：未找到或写入失败。");
        echo 'OK'; exit;
    }
}

// ========== 普通用户 /help（未验证也可给出说明；被 Ban 已前置拦截） ==========
if (!$isAdmin && is_string($text) && preg_match('~^/help\b~i', trim($text))) {
    $isVerified = in_array($userId, load_verified_users(), true);
    if ($isVerified) tg_send_message($userId, "🤖 帮助\n直接发消息给我，我会转发给管理员；管理员回复后我会发还给你。");
    else {
        $now=now_ts(); $exp=$now+VERIFICATION_TOKEN_TTL;
        $token=JWT::encode(['type'=>'verify','user_id'=>$userId,'exp'=>$exp], SHARED_JWT_SECRET,'HS256');
        $link = VERIFY_URL.'?token='.urlencode($token);
        tg_send_message($userId, "🤖 帮助\n首次使用需人机验证：\n➡️ {$link}\n通过后再与我对话。");
    }
    echo 'OK'; exit;
}

// ---------- 屏蔽词（只拦普通用户；不回显词条） ----------
$composite = trim((string)($text ?? ''))."\n".trim((string)($caption ?? ''));
if (!$isAdmin && bad_words_hit($composite)) {
    tg_send_message($userId, "⚠️ 你的消息包含被屏蔽的内容，未被发送。");
    if (DEBUG) debug_log("[WEBHOOK] blocked by bad words user={$userId}");
    echo 'OK'; exit;
}

// ---------- 验证状态 ----------
$verifiedUsers = load_verified_users();
$isVerified = $isAdmin ? true : in_array($userId, $verifiedUsers, true);

// ---------- /start（被 Ban 已在前面拦截） ----------
if (is_string($text) && str_starts_with(trim($text), '/start')) {
    $parts = preg_split('/\s+/', trim($text), 2); $payload = $parts[1] ?? '';

    if ($payload !== '') {
        try { $obj = JWT::decode($payload, new Key(SHARED_JWT_SECRET,'HS256')); }
        catch (\Firebase\JWT\ExpiredException $e){ tg_send_message($userId, "❌ 验证失败：令牌已过期。"); echo 'OK'; exit; }
        catch (\Throwable $e){ tg_send_message($userId, "❌ 验证失败：令牌无效。"); echo 'OK'; exit; }

        $data = json_decode(json_encode($obj), true) ?: [];
        if (($data['type']??null)!=='success' || !($data['verified']??false) || (int)($data['user_id']??0)!==$userId) {
            tg_send_message($userId, "❌ 验证失败：令牌不匹配。"); echo 'OK'; exit;
        }
        if (!$isAdmin && !$isVerified) { $verifiedUsers[]=$userId; save_verified_users($verifiedUsers); }
        tg_send_message($userId, "✅ 验证通过！现在可以正常与机器人互动了。");
        echo 'OK'; exit;
    }

    if ($isVerified || $isAdmin) tg_send_message($userId, "欢迎，你可以直接发送消息了。");
    else {
        $now=now_ts(); $exp=$now+VERIFICATION_TOKEN_TTL;
        $token=JWT::encode(['type'=>'verify','user_id'=>$userId,'exp'=>$exp], SHARED_JWT_SECRET,'HS256');
        $link = VERIFY_URL.'?token='.urlencode($token);
        tg_send_message($userId, "👋 你好，请先完成一次性人机验证：\n\n➡️ {$link}\n\n此链接在 ".(VERIFICATION_TOKEN_TTL/60)." 分钟内有效。");
    }
    echo 'OK'; exit;
}

// ---------- 管理员“回复转发/信息卡片” → 回推 ----------
if ($isAdmin && isset($msg['reply_to_message'])) {
    $replyMid = (int)$msg['reply_to_message']['message_id'];
    if ($r = route_get($replyMid)) {
        $dstUserId   = (int)$r['user_id'];
        $dstReplyMid = (int)$r['src_msg_id'];
        if (!relay_admin_reply_to_user($dstUserId, $msg, $dstReplyMid)) {
            tg_send_message(ADMIN_ID, "⚠️ 回推失败或类型不支持（已尝试告警）。");
        }
    } else {
        tg_send_message(ADMIN_ID, "⚠️ 未找到路由映射，请“回复那条转发消息”或“信息卡片”。");
    }
    echo 'OK'; exit;
}

// ---------- 未验证 → 引导验证 ----------
if (!$isAdmin && !$isVerified) {
    $now=now_ts(); $exp=$now+VERIFICATION_TOKEN_TTL;
    $token=JWT::encode(['type'=>'verify','user_id'=>$userId,'exp'=>$exp], SHARED_JWT_SECRET,'HS256');
    $link = VERIFY_URL.'?token='.urlencode($token);
    tg_send_message($userId, "👋 你好，为了防骚扰，请先完成一次性人机验证：\n\n➡️ {$link}\n\n此链接在 ".(VERIFICATION_TOKEN_TTL/60)." 分钟内有效。");
    echo 'OK'; exit;
}

// ---------- 已验证用户 → 转发 + 精简信息卡片 ----------
if (!$isAdmin && $isVerified) {
    $fw = tg_api('forwardMessage', ['chat_id'=>ADMIN_ID,'from_chat_id'=>$userId,'message_id'=>$msg['message_id']]);
    if (($fw['ok'] ?? false) && isset($fw['result']['message_id'])) {
        $adminFwdMid = (int)$fw['result']['message_id'];
        route_put($adminFwdMid, $userId, (int)$msg['message_id']);

        $username  = isset($from['username']) && $from['username']!=='' ? '@'.$from['username'] : '（无）';
        $firstName = $from['first_name'] ?? '';
        $lastName  = $from['last_name']  ?? '';
        $fullName  = trim($firstName.' '.$lastName); if ($fullName==='') $fullName='（无）';
        if (!empty($from['is_premium'])) $fullName .= ' ⭐️';

        $card = "👤 <b>用户信息</b>\n"
              . "ID：<code>".h((string)$userId)."</code>\n"
              . "用户名：<b>".h($username)."</b>\n"
              . "姓名：<b>".h($fullName)."</b>\n"
              . "<i>回复此消息或其上方的转发消息即可回信。</i>";
        $detailMid = tg_send_message(ADMIN_ID, $card, [
            'parse_mode'=>'HTML',
            'reply_to_message_id'=>$adminFwdMid,
            'disable_web_page_preview'=>true,
        ]);
        if ($detailMid !== null) route_put($detailMid, $userId, (int)$msg['message_id']);
    }
    echo 'OK'; exit;
}

// ---------- 管理员非“回复”的消息 ----------
if ($isAdmin) tg_send_message(ADMIN_ID, "📌 请“回复某条转发消息”或“回复信息卡片”来把内容发回对应用户。");

echo 'OK'; exit;
