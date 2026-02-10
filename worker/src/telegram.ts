
export class TelegramApi {
  private token: string;

  constructor(token: string) {
    this.token = token;
  }

  private async call(method: string, body: any): Promise<any> {
    const url = `https://api.telegram.org/bot${this.token}/${method}`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    return response.json();
  }

  async sendMessage(chat_id: number | string, text: string, options: any = {}) {
    return this.call("sendMessage", { chat_id, text, ...options });
  }

  async forwardMessage(chat_id: number | string, from_chat_id: number | string, message_id: number) {
    return this.call("forwardMessage", { chat_id, from_chat_id, message_id });
  }

  async copyMessage(chat_id: number | string, from_chat_id: number | string, message_id: number, options: any = {}) {
    return this.call("copyMessage", { chat_id, from_chat_id, message_id, ...options });
  }

  async answerCallbackQuery(callback_query_id: string, options: any = {}) {
    return this.call("answerCallbackQuery", { callback_query_id, ...options });
  }
  
  async editMessageText(chat_id: number | string, message_id: number, text: string, options: any = {}) {
    return this.call("editMessageText", { chat_id, message_id, text, ...options });
  }

  async react(chat_id: number | string, message_id: number, emoji: string) {
     return this.call("setMessageReaction", { 
         chat_id, 
         message_id, 
         reaction: [{ type: "emoji", emoji }]
     });
  }
}
