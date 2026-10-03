import { Plugin } from "@opencode/plugin";
import * as fs from "node:fs";

type Logs = string | null;

let pluginLogs: Logs = null;

function log(message: string) {
  if (!pluginLogs) return;
  try {
    fs.appendFileSync(pluginLogs, `${message}\n`);
  } catch {
    /* logging must never break the plugin */
  }
}

export default Plugin.define({
  id: "nina_notify",
  async setup(ctx) {
    const options = ctx.options ?? {};
    const ninaEndpoint =
      typeof options.ninaEndpoint === "string"
        ? options.ninaEndpoint
        : "127.0.0.1:1888";
    pluginLogs =
      typeof options.pluginLogs === "string" ? options.pluginLogs : null;
    const intervalCheckMinutes =
      typeof options.intervalCheckMinutes === "number"
        ? options.intervalCheckMinutes
        : -1;

    const savedSessionID = await ctx.storage.get("targetSessionID");
    let targetSessionID =
      typeof savedSessionID === "string" ? savedSessionID : undefined;
    if (targetSessionID) {
      log(`nina-plugin: restored target session: ${targetSessionID}`);
    }

    const promptHook = await ctx.session.hook("prompt", async (event) => {
      targetSessionID = event.sessionID;
      log(`nina-plugin: target session set from prompt: ${targetSessionID}`);
      try {
        await ctx.storage.set("targetSessionID", event.sessionID);
        log(`nina-plugin: persisted target session: ${event.sessionID}`);
      } catch (err) {
        log(`nina-plugin: failed to persist session ID: ${err}`);
      }
    });

    async function triggerIntervention(events: unknown = null) {
      const text =
        events == null
          ? "Trigger: Routine interval check. No new N.I.N.A. events."
          : `Trigger: New N.I.N.A. events follow:\n\n\`\`\`json\n${JSON.stringify(events, null, 2)}\n\`\`\``;
      log(`nina-plugin: intervention: ${text}`);
      if (!targetSessionID) {
        log("nina-plugin: no target session yet; skipping injection");
        return;
      }
      await ctx.session.prompt({ sessionID: targetSessionID as any, text });
    }

    let eventBatch: unknown[] = [];
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;

    async function flushBatch() {
      if (eventBatch.length === 0) return;
      const batch = eventBatch;
      eventBatch = [];
      await triggerIntervention(batch);
    }

    function pushEvent(response: unknown) {
      eventBatch.push(response);
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        flushBatch().catch((err) =>
          log(`nina-plugin: flushBatch failed: ${err}`),
        );
      }, 5_000);
    }

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      const url = `ws://${ninaEndpoint}/v2/socket`;
      log(`nina-plugin: connecting to: ${url}`);
      ws = new WebSocket(url);
      ws.onopen = () => {
        log("nina-plugin: ws onopen");
        ws?.send("SUB /socket");
      };
      ws.onmessage = (event) => {
        let msg: any;
        try {
          msg = JSON.parse(String(event.data));
        } catch {
          return;
        }
        if (msg?.Response) {
          const response =
            typeof msg.Response === "string"
              ? { Event: msg.Response }
              : msg.Response;
          log(
            `nina-plugin: ws onmessage ${JSON.stringify(response, null, 2)}`,
          );
          pushEvent(response);
        }
      };
      ws.onclose = () => {
        if (closed) return;
        log("nina-plugin: ws onclose");
        reconnectTimer = setTimeout(connect, 5_000);
      };
      ws.onerror = () => {
        log("nina-plugin: ws error");
        ws?.close();
      };
    }

    connect();

    let intervalId: ReturnType<typeof setInterval> | null = null;
    if (intervalCheckMinutes > 0) {
      intervalId = setInterval(() => {
        if (!(ws && ws.readyState === WebSocket.OPEN)) return;
        triggerIntervention().catch((err) =>
          log(`nina-plugin: interval trigger failed: ${err}`),
        );
      }, intervalCheckMinutes * 60_000);
    }

    return async () => {
      closed = true;
      await promptHook.dispose();
      if (intervalId !== null) clearInterval(intervalId);
      if (debounceTimer) clearTimeout(debounceTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  },
});
