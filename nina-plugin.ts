import type { Plugin, PluginInput, PluginOptions } from "@opencode-ai/plugin";
import { appendFile } from "node:fs/promises";
import * as fs from "node:fs";
import { WebSocket } from "ws";

function fileLog(pluginLogs : string | null, message: string, data?: any) {
  const payload = `${message} ${JSON.stringify(data)}`;
  if (pluginLogs) {
    fs.appendFileSync(pluginLogs , `${payload}\n`);
  }
}

const plugin: Plugin = async (
  ctx: PluginInput,
  options: PluginOptions = {},
) => {
  const ninaEndpoint = (options.ninaEndpoint as string) || "127.0.0.1:1888";
  const intervalCheck = (options.intervalCheck as number) || 10;
  const pluginLogs = options.pluginLogs as string || null;
  const agentHistory = options.agentHistory as string || null;

  async function triggerIntervention(events: string | null = null) {
    const sessions = await ctx.client.session.list().catch(() => null)
    if (!sessions?.data?.length) {
      fileLog(pluginLogs, "nina-plugin: active session does not exist.");
      return
    }
    const session = sessions.data[0]

    // target the new session ID, ignoring the user's active console
    const text = events == null
        ? "Trigger: Routine interval check. No new N.I.N.A. events."
        : `Trigger: New N.I.N.A. events follow:\n\n\`\`\`json\n${events}\n\`\`\``;
    fileLog(pluginLogs, "text:", text);

    // inject prompt
    await ctx.client.session.prompt({
      path: { id: session.id },
      body: {
        parts: [
          {
            type: "text",
            text
          },
        ],
      },
    });

    // capture everything the worker did
    const messages = await ctx.client.session.messages({
      path: { id: sessionId },
    });

    // append session to file
    if (agentHistory) {
      await appendFile(
        agentHistory,
        JSON.stringify({
          timestamp: new Date().toISOString(),
          sessionId,
          agent: agentName,
          messages: messages.data,
        }) + "\n",
        "utf8",
      );
    }

    // delete session data
    await ctx.client.session.delete({
      path: { id: sessionId },
    });
  }

  let eventBatch: string[] = [];
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  async function flushBatch() {
    if (eventBatch.length === 0) return;
    const payload = JSON.stringify(eventBatch);
    eventBatch = [];
    await triggerIntervention(payload);
  }

  function pushEvent(response: string) {
    eventBatch.push(response);
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      flushBatch().catch((err: unknown) =>
        fileLog(pluginLogs, "nina-plugin: flushBatch failed:", err),
      );
    }, 1_000);
  }

  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  function connect() {
    if (closed) return;
    const url = `ws://${ninaEndpoint}/v2/socket`;
    fileLog(pluginLogs, "nina-plugin: connecting to:", url);
    ws = new WebSocket(url);

    ws.onopen = () => {
      fileLog(pluginLogs, "nina-plugin: ws onopen");
      ws!.send("SUB /socket");
    };

    ws.onmessage = (event: { data: unknown }) => {
      try {
        fileLog(pluginLogs, "nina-plugin: ws onmessage:", event.data);
        const msg = JSON.parse(event.data as string);
        if (msg.Response) {
          pushEvent(msg.Response);
        }
      } catch {}
    };

    ws.onclose = () => {
      if (!closed) {
        fileLog(pluginLogs, "nina-plugin: ws onclose");
        reconnectTimer = setTimeout(connect, 5_000);
      }
    };

    ws.onerror = () => {
      fileLog(pluginLogs, "nina-plugin: ws error");
      ws?.close();
    };
  }

  connect();

  const intervalId = setInterval(() => {
    triggerIntervention().catch((err: unknown) =>
      fileLog(pluginLogs, "nina-plugin: interval trigger failed:", err),
    );
  }, intervalCheck * 60_000);

  return {
    dispose: async () => {
      closed = true;
      clearInterval(intervalId);
      if (debounceTimer) clearTimeout(debounceTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    },
  };
};

export default plugin;
