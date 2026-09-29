import type { Plugin, PluginInput, PluginOptions } from "@opencode-ai/plugin";
import { appendFile } from "node:fs/promises";
import * as fs from "node:fs";
import { WebSocket } from "ws";

function fileLog(pluginLogs : string | null, message: string, data?: any) {
  const payload = `${message} ${JSON.stringify(data, null, 2)}`;
  if (pluginLogs) {
    fs.appendFileSync(pluginLogs , `${payload}\n`);
  }
}

const plugin: Plugin = async (
  ctx: PluginInput,
  options: PluginOptions = {},
) => {
  const ninaEndpoint = (options.ninaEndpoint as string) || "127.0.0.1:1888";
  const intervalCheck: number =
    typeof options.intervalCheck === "number" ? options.intervalCheck : 10;
  const pluginLogs = options.pluginLogs as string || null;

  async function triggerIntervention(events: any | null = null) {
    // get prompt
    const payload = JSON.stringify(events, null, 2);
    const text = (events == null
        ? "Trigger: Routine interval check. No new N.I.N.A. events."
        : `Trigger: New N.I.N.A. events follow:\n\n\`\`\`json\n${payload}\n\`\`\``);
    fileLog(pluginLogs, "events:", text);

    // get active session
    const sessions = await ctx.client.session.list().catch(() => null)
    if (!sessions?.data?.length) return
    const session = sessions.data[0]

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
  }

  let eventBatch: string[] = [];
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  async function flushBatch() {
    if (eventBatch.length === 0) return;
    await triggerIntervention(eventBatch);
    eventBatch = [];
  }

  function safeJsonParse(str: string) {
    try {
      return JSON.parse(str);
    } catch {
      return null;
    }
  }

  function pushEvent(response: string) {
    eventBatch.push(response);
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      flushBatch().catch((err: unknown) =>
        fileLog(pluginLogs, "nina-plugin: flushBatch failed:", err),
      );
    }, 5_000);
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
      const msg = safeJsonParse(event.data as string);
      if (msg && msg.Response) {
        const response = typeof msg.Response == "string" ? { Event: msg.Response } : msg.Response
        fileLog(pluginLogs, "nina-plugin: ws onmessage", response);
        pushEvent(response);
      }
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

  function isSocketOpen(): boolean {
    return ws !== null && ws.readyState === WebSocket.OPEN;
  }

  connect();

  let intervalId: ReturnType<typeof setInterval> | null = null;
  if (intervalCheck > 0) {
    intervalId = setInterval(() => {
      if (!isSocketOpen()) return;
      triggerIntervention().catch((err: unknown) =>
        fileLog(pluginLogs, "nina-plugin: interval trigger failed:", err),
      );
    }, intervalCheck * 60_000);
  }

  return {
    dispose: async () => {
      closed = true;
      if (intervalId !== null) clearInterval(intervalId);
      if (debounceTimer) clearTimeout(debounceTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    },
  };
};

export default plugin;
