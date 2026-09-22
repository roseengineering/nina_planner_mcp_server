import type { PluginInput, PluginModule } from "@opencode-ai/plugin";
declare const process: { env: Record<string, string | undefined> };

const NINA_ENDPOINT = process.env.NINA_ENDPOINT || "127.0.0.1:1888";
const INTERVAL_CHECK_MINUTES = +(process.env.INTERVAL_CHECK_MINUTES || 10);
const WORKER_AGENT = "worker";
const DEBUG = process.env.DEBUG;

const plugin: PluginModule = {
  id: "nina-plug",
  server: async (ctx: PluginInput) => {
    async function triggerIntervention(events: string | null = null) {
      // does agent exist?
      const { data: agents } = await ctx.client.app.agents();
      const exists = agents.some((a: any) => a.name === WORKER_AGENT);
      if (!exists) {
        console.error(`nina-plugin: agent ${WORKER_AGENT} does not exist.`);
        return null;
      }

      // create a brand new session
      const newSession = await ctx.client.session
        .create()
        .catch((err: unknown) => {
          console.error("nina-plugin: failed to create isolated session:", err);
          return null;
        });

      // ensure the session was created successfully
      if (!newSession?.data?.id) return;

      // target the new session ID, ignoring the user's active console
      const text =
        events == null
          ? "Trigger: Routine interval check. No new N.I.N.A. events. Review observatory status and take action if needed."
          : `Trigger: The latest N.I.N.A. events follow:\n\n\`\`\`json\n${events}\n\`\`\``;

      const payload = {
        path: { id: newSession.data.id },
        body: {
          agent: WORKER_AGENT,
          parts: [
            {
              type: "text",
              text,
            },
          ],
        },
      };
      if (DEBUG) console.error("prompt:", JSON.stringify(payload, null, 2));
      await ctx.client.session.prompt(payload);
      await ctx.client.session.delete({ path: { id: newSession.data.id } });
    }

    let eventBatch: string[] = [];
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;

    function flushBatch() {
      if (eventBatch.length === 0) return;
      const payload = JSON.stringify(eventBatch);
      eventBatch = [];
      triggerIntervention(payload);
    }

    function pushEvent(response: string) {
      eventBatch.push(response);
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flushBatch, 1_000);
    }

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      const url = `ws://${NINA_ENDPOINT}/v2/socket`;
      if (DEBUG) console.error(`nina-plugin: connecting to ${url}`);
      ws = new WebSocket(url);

      ws.onopen = () => {
        if (DEBUG) console.error("nina-plugin: ws onopen");
        ws!.send("SUB /socket");
      };

      ws.onmessage = (event) => {
        try {
          if (DEBUG) console.error("nina-plugin: ws onmessage");
          const msg = JSON.parse(event.data as string);
          if (msg.Response) {
            pushEvent(msg.Response);
          }
        } catch {}
      };

      ws.onclose = () => {
        if (!closed) {
          if (DEBUG) console.error("nina-plugin: ws onclose");
          reconnectTimer = setTimeout(connect, 5_000);
        }
      };

      ws.onerror = () => {
        if (DEBUG) console.error("nina-plugin: ws error");
        ws?.close();
      };
    }

    connect();

    const intervalId = setInterval(() => {
      triggerIntervention();
    }, INTERVAL_CHECK_MINUTES * 60_000);

    return {
      dispose: async () => {
        closed = true;
        clearInterval(intervalId);
        if (debounceTimer) clearTimeout(debounceTimer);
        if (reconnectTimer) clearTimeout(reconnectTimer);
        ws?.close();
      },
    };
  },
};

export default plugin;
