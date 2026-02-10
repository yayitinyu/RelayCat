import { Hono } from "hono";
import { BotHandler, Env } from "./bot";

const app = new Hono<{ Bindings: Env }>();

app.get("/", (c) => c.text("RelayCat Worker is running!"));

app.post("/webhook", async (c) => {
  const update = await c.req.json();
  const bot = new BotHandler(c.env);
  
  // Run in background to avoid timeout? 
  // Workers might kill execution if response is sent. 
  // usage of c.executionCtx.waitUntil is recommended.
  
  if (c.executionCtx) {
     c.executionCtx.waitUntil(bot.handleUpdate(update));
  } else {
     await bot.handleUpdate(update);
  }

  return c.text("OK");
});

export default app;
