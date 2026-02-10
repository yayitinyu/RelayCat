import { TelegramApi } from "./telegram";
import { User, MessageRoute, Setting, Rule } from "./model";

export interface Env {
  DB: D1Database;
  RELAYCAT_BOT_TOKEN: string;
  RELAYCAT_ADMIN_ID: string;
  RELAYCAT_ENABLE_FORWARDING: string;
}

const EMOJIS = ["🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓"];

export class BotHandler {
  private api: TelegramApi;
  private env: Env;
  private adminId: number;

  constructor(env: Env) {
    this.env = env;
    this.api = new TelegramApi(env.RELAYCAT_BOT_TOKEN);
    this.adminId = parseInt(env.RELAYCAT_ADMIN_ID);
  }

  async handleUpdate(update: any) {
    if (update.message) {
      await this.handleMessage(update.message);
    } else if (update.callback_query) {
      await this.handleCallback(update.callback_query);
    }
  }

  async checkRules(message: any, user: string): Promise<string> {
      // Default: Block commands from non-admin
      const text = message.text || message.caption || "";
      if (text.startsWith("/") && message.from.id !== this.adminId) return "drop";

      const rules = await this.env.DB.prepare("SELECT * FROM rules WHERE is_active = 1").all<any>();
      
      for (const rule of rules.results) {
          let matched = false;
          try {
              const pattern = new RegExp(rule.pattern, "i");
              if (rule.rule_type === "message_content") {
                  if (pattern.test(text)) matched = true;
              } else if (rule.rule_type === "username") {
                  if (pattern.test(user || "")) matched = true;
              } else if (rule.rule_type === "is_forwarded") {
                  // Check forward origin (simplified)
                  if ((message.forward_date || message.forward_from) && rule.pattern === "true") matched = true;
              }
          } catch (e) {
              console.error("Invalid Regex", rule.pattern);
              continue;
          }

          if (matched) return rule.action; // block, drop, allow
      }
      return "allow";
  }

  async handleMessage(message: any) {
    const fromUser = message.from;
    const chatId = message.chat.id;
    const userId = fromUser.id;

    // 1. Check if Admin
    if (userId === this.adminId) {
       await this.handleAdminMessage(message);
       return;
    }

    if (message.chat.type !== 'private') return;

    // 2. Get User from DB
    let user = await this.env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(userId).first<User>();

    if (!user) {
      await this.env.DB.prepare(
        "INSERT INTO users (id, username, first_name, last_name) VALUES (?, ?, ?, ?)"
      ).bind(userId, fromUser.username || null, fromUser.first_name || null, fromUser.last_name || null).run();
      
      user = { id: userId, is_verified: 0, is_banned: 0 } as User;
    }

    if (user.is_banned) return;

    // 3. Verification
    if (!user.is_verified) {
       await this.sendVerificationChallenge(chatId);
       return;
    }

    // Handle /start for verified users
    if (message.text === "/start") {
        await this.api.sendMessage(chatId, "Hello again! You are verified. Messages you send here will be forwarded to the admin.");
        return;
    }

    // 4. Rule Check
    const action = await this.checkRules(message, user.username || "");
    if (action === "drop") return;
    if (action === "block") {
        await this.api.sendMessage(chatId, "🚫 Message blocked by filter.");
        return;
    }

    // 5. Forwarding (User -> Admin)
    await this.forwardToAdmin(message, user);
  }

  async handleCallback(cb: any) {
    const data = cb.data; 
    const userId = cb.from.id;

    if (data.startsWith("verify:")) {
       // Logic: Check if valid
       // Stateless hack: Parse text from message? Or just rely on random guess probability (1/9) + text check
       // Current Python logic checks if 'target' is in text. 
       // Text: "Tap the 🍎 button"
       const text = cb.message.text || "";
       const parts = text.split("tap the ");
       if (parts.length < 2) {
           await this.api.answerCallbackQuery(cb.id, { text: "Session expired", show_alert: true });
           return;
       }
       const targetEmoji = parts[1].trim().split(" ")[0]; // "🍎"
       const clickedEmoji = data.split(":")[1];

       if (targetEmoji === clickedEmoji) {
           await this.env.DB.prepare("UPDATE users SET is_verified = 1 WHERE id = ?").bind(userId).run();
           await this.api.editMessageText(cb.message.chat.id, cb.message.message_id, "✅ Verified! You can now send messages.");
           await this.api.answerCallbackQuery(cb.id);
       } else {
           // Wrong
           const [target, markup] = this.generateChallenge();
           await this.api.editMessageText(cb.message.chat.id, cb.message.message_id, `Wrong! Try again. Tap the ${target} button:`, { reply_markup: markup });
           await this.api.answerCallbackQuery(cb.id, { text: "Wrong emoji!" });
       }
    }
  }

  async handleAdminMessage(message: any) {
    // Admin Commands
    const text = message.text || "";
    if (text.startsWith("/ban")) {
        const parts = text.split(" ");
        let targetId = parts.length > 1 ? parseInt(parts[1]) : 0;
        
        if (!targetId && message.reply_to_message) {
            targetId = (await this.getReplyTargetId(message.reply_to_message)) || 0;
        }

        if (targetId) {
            await this.env.DB.prepare("UPDATE users SET is_banned = 1 WHERE id = ?").bind(targetId).run();
            await this.api.sendMessage(this.adminId, `🚫 User ${targetId} has been banned.`);
        } else {
            await this.api.sendMessage(this.adminId, "⚠️ Usage: /ban <id> or reply to user message.");
        }
        return; 
    }

    if (text.startsWith("/unban")) {
        const parts = text.split(" ");
        let targetId = parts.length > 1 ? parseInt(parts[1]) : 0;
        
        if (!targetId && message.reply_to_message) {
            targetId = (await this.getReplyTargetId(message.reply_to_message)) || 0;
        }

        if (targetId) {
            await this.env.DB.prepare("UPDATE users SET is_banned = 0 WHERE id = ?").bind(targetId).run();
            await this.api.sendMessage(this.adminId, `✅ User ${targetId} has been unbanned.`);
        } else {
             await this.api.sendMessage(this.adminId, "⚠️ Usage: /unban <id> or reply to user message.");
        }
        return;
    }

    if (text.startsWith("/set")) {
        // Usage: /set <key> <value>
        // Example: /set confirm_reply true
        const parts = text.split(" ");
        if (parts.length < 3) {
             await this.api.sendMessage(this.adminId, "⚠️ Usage: /set <key> <value>\nKeys: confirm_reply");
             return;
        }
        const key = parts[1];
        const value = parts[2];
        
        // Upsert
        await this.env.DB.prepare(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?"
        ).bind(key, value, value).run();
        
        await this.api.sendMessage(this.adminId, `✅ Setting updated: ${key} = ${value}`);
        return;
    }

    if (text.startsWith("/addrule")) {
        // Usage: /addrule <pattern>
        const parts = text.split(" ");
        if (parts.length < 2) {
             await this.api.sendMessage(this.adminId, "⚠️ Usage: /addrule <regex_pattern>");
             return;
        }
        const pattern = parts.slice(1).join(" ");
        
        try {
            new RegExp(pattern); // Validate regex
        } catch (e) {
            await this.api.sendMessage(this.adminId, "❌ Invalid Regex pattern.");
            return;
        }

        await this.env.DB.prepare(
            "INSERT INTO rules (pattern, action, is_active) VALUES (?, 'block', 1)"
        ).bind(pattern).run();
        
        await this.api.sendMessage(this.adminId, `✅ Rule added: <code>${pattern}</code>`, { parse_mode: "HTML" });
        return;
    }

    if (text.startsWith("/rules")) {
        const rules = await this.env.DB.prepare("SELECT id, pattern FROM rules WHERE is_active = 1").all<Rule>();
        if (!rules.results || rules.results.length === 0) {
             await this.api.sendMessage(this.adminId, "No active rules.");
             return;
        }
        const list = rules.results.map(r => `${r.id}: <code>${r.pattern}</code>`).join("\n");
        await this.api.sendMessage(this.adminId, `📜 <b>Active Rules</b>\n${list}`, { parse_mode: "HTML" });
        return;
    }

    if (text.startsWith("/delrule")) {
        const parts = text.split(" ");
        const id = parts[1] ? parseInt(parts[1]) : 0;
        if (!id) {
             await this.api.sendMessage(this.adminId, "⚠️ Usage: /delrule <id>");
             return;
        }
        await this.env.DB.prepare("DELETE FROM rules WHERE id = ?").bind(id).run();
        await this.api.sendMessage(this.adminId, `✅ Rule ${id} deleted.`);
        return;
    }

    // Reply Logic
    if (message.reply_to_message) {
        const replyId = message.reply_to_message.message_id;
        // Lookup route
        const route = await this.env.DB.prepare(
            "SELECT * FROM message_routes WHERE admin_message_id = ?"
        ).bind(replyId).first<MessageRoute>();

        let targetUserId = route?.user_id;

        // Fallback: Check text for ID (Info card)
        if (!targetUserId) {
            const replyText = message.reply_to_message.text || "";
            const match = replyText.match(/ID: (\d+)/);
            if (match) targetUserId = parseInt(match[1]);
        }

        if (targetUserId) {
            try {
                await this.api.copyMessage(targetUserId, this.adminId, message.message_id);
                // Confirm if enabled
                const setting = await this.env.DB.prepare("SELECT value FROM settings WHERE key = 'confirm_reply'").first<Setting>();
                if (!setting || setting.value === 'true') {
                     await this.api.react(this.adminId, message.message_id, "👍");
                }
            } catch (e) {
                await this.api.sendMessage(this.adminId, `Failed to send: ${e}`);
            }
        } else {
             await this.api.sendMessage(this.adminId, "⚠️ Cannot find user to reply to.");
        }
    }
  }

  async getReplyTargetId(message: any): Promise<number | null> {
      // 1. Check DB Route
      const route = await this.env.DB.prepare(
          "SELECT * FROM message_routes WHERE admin_message_id = ?"
      ).bind(message.message_id).first<MessageRoute>();
      
      if (route) return route.user_id;

      // 2. Check Info Card Text
      const text = message.text || message.caption || "";
      const match = text.match(/ID: (\d+)/);
      if (match) return parseInt(match[1]);

      return null;
  }

  async forwardToAdmin(message: any, user: any) {
     // Forward
     const fwd = await this.api.forwardMessage(this.adminId, message.chat.id, message.message_id);
     
     // Send Card
     const infoText = `👤 <b>User Info</b>\nID: <code>${user.id}</code>\nName: ${user.first_name || ''}\nUsername: @${user.username || 'none'}`;
     
     const card = await this.api.sendMessage(this.adminId, infoText, { 
         parse_mode: 'HTML', 
         reply_to_message_id: fwd.result.message_id 
     });

     // Save Routes
     if (fwd.ok && card.ok) {
         await this.env.DB.prepare(
             "INSERT INTO message_routes (user_id, admin_message_id, user_message_id) VALUES (?, ?, ?), (?, ?, ?)"
         ).bind(
             user.id, fwd.result.message_id, message.message_id,
             user.id, card.result.message_id, message.message_id
         ).run();
     }
  }

  async sendVerificationChallenge(chatId: number) {
      const [target, markup] = this.generateChallenge();
      await this.api.sendMessage(chatId, `Welcome! To verify, please tap the ${target} button below:`, { reply_markup: markup });
  }

  generateChallenge(): [string, any] {
      const target = EMOJIS[Math.floor(Math.random() * EMOJIS.length)];
      // 3x3 grid
      const options = [target];
      while (options.length < 9) {
          options.push(EMOJIS[Math.floor(Math.random() * EMOJIS.length)]);
      }
      // Shuffle
      options.sort(() => Math.random() - 0.5);

      const inline_keyboard = [];
      let row = [];
      for (const emoji of options) {
          row.push({ text: emoji, callback_data: `verify:${emoji}` });
          if (row.length === 3) {
              inline_keyboard.push(row);
              row = [];
          }
      }
      return [target, { inline_keyboard }];
  }
}
