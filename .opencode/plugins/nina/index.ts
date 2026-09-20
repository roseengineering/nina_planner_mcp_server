import { Plugin } from "@opencode/plugin";

const NINA_ENDPOINT = process.env.NINA_ENDPOINT || "localhost:1888";
const intervalMinutesValue = +(
  process.env.NINA_INTERVAL_MINUTES ??
  process.env.INTERVAL_CHECK_MINUTES ??
  10
);
const INTERVAL_CHECK_MINUTES =
  Number.isFinite(intervalMinutesValue) && intervalMinutesValue > 0
    ? intervalMinutesValue
    : 10;
const WORKER_AGENT = "worker";
const DEBUG = process.env.DEBUG;

export default Plugin.define({
  id: "nina",
  async setup(ctx) {
    async function workerAgentExists(): Promise<boolean> {
      try {
        const agents = await ctx.agent.list();
        return agents.some(
          (a) => a.id === WORKER_AGENT || a.name === WORKER_AGENT,
        );
      } catch (err) {
        console.error("nina-plugin: failed to list agents:", err);
        return false;
      }
    }

    async function triggerIntervention(
      events: string | null = null,
    ): Promise<void> {
      if (!(await workerAgentExists())) {
        console.error(`nina-plugin: agent ${WORKER_AGENT} does not exist.`);
        return;
      }

      const text =
        events == null
          ? "Trigger: Routine interval check. No new N.I.N.A. events. Review observatory status and take action if needed."
          : `Trigger: The latest N.I.N.A. events follow:\n\n\`\`\`json\n${events}\n\`\`\``;

      try {
        const session = await ctx.session.create({
          title: "nina-intervention",
        });
        if (!session?.id) return;

        await ctx.session.switchAgent({
          sessionID: session.id,
          agent: WORKER_AGENT,
        });
        if (DEBUG)
          console.error(
            "nina-plugin: prompting",
            JSON.stringify({ sessionID: session.id, text }),
          );

        await ctx.session.prompt({ sessionID: session.id, text });

        // Best-effort cleanup of the throwaway session.
        try {
          await ctx.session.delete({ sessionID: session.id });
        } catch (err) {
          if (DEBUG) console.error("nina-plugin: session cleanup failed:", err);
        }
      } catch (err) {
        console.error("nina-plugin: intervention failed:", err);
      }
    }

    let eventBatch: string[] = [];
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;

    function flushBatch() {
      if (eventBatch.length === 0) return;
      const payload = JSON.stringify(eventBatch);
      eventBatch = [];
      void triggerIntervention(payload);
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
          if (msg.Response) pushEvent(msg.Response);
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
      void triggerIntervention();
    }, INTERVAL_CHECK_MINUTES * 60_000);

    return () => {
      closed = true;
      clearInterval(intervalId);
      if (debounceTimer) clearTimeout(debounceTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  },
});
