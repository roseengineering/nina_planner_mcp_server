import { Plugin } from "@opencode/plugin";
import * as fs from "node:fs";

type Logs = string | null;

let pluginLogs: Logs = null;

function log(message: string) {
  if (!pluginLogs) return;
  try {
    fs.appendFileSync(pluginLogs, `${message}\n`);
  } catch {
    // If configured path failed (e.g. Windows path on POSIX), try /tmp fallback
    if (process.platform !== "win32") {
      try {
        fs.appendFileSync("/tmp/nina-notify-debug.log", `${message}\n`);
      } catch {
        /* logging must never break the plugin */
      }
    }
  }
}

// One compact JSON document per line (JSON Lines).
function toJSONL(value: unknown): string {
  const items = Array.isArray(value) ? value : [value];
  return items.map((item) => JSON.stringify(item)).join("\n");
}

// Events that must reach the controller with NO batching delay: safety state,
// sequence lifecycle, mount/stow, and anything that looks like a failure.
// Everything else — per-frame IMAGE-* saves, autofocus, guider chatter, and any
// event we don't explicitly recognize — is treated as telemetry and batched on
// a slower lane. Per-frame telemetry dominated the old prompt volume (~1 prompt
// per frame ≈ 1100/night); batching it cuts prompt count by ~80% while safety
// and target transitions still arrive instantly.
const IMMEDIATE_EVENT =
  /SAFETY|SAFE|UNSAFE|WEATHER|DOME|ROOF|RUN-(STARTING|FINISHED)|SEQUENCE-(STARTING|FINISHED)|STOW|MOUNT|OFFLINE|DISCONNECT|LOST|EMERGENCY|ERROR|FAIL|EXCEPTION|ABORT|INTERRUPT|CRITICAL/i;

function eventText(value: unknown): string {
  if (typeof value === "string") return value;
  const ev = (value as { Event?: unknown } | null)?.Event;
  return typeof ev === "string" ? ev : "";
}

function isImmediate(value: unknown): boolean {
  return IMMEDIATE_EVENT.test(eventText(value));
}

// Module-level handle to ensure only one instance is active per process
let activeCleanup: (() => Promise<void>) | null = null;

// Module-level deduplication cache
let lastInjectedText: string | null = null;
let lastInjectedTime = 0;

export default Plugin.define({
  id: "nina_notify",
  async setup(ctx) {
    const instanceId = Math.random().toString(36).slice(2, 6);

    // If a previous instance is still running in this process, cleanly dispose it first
    if (activeCleanup) {
      log(`[${instanceId}] disposing previous active instance before starting`);
      try {
        await activeCleanup();
      } catch (err) {
        log(`[${instanceId}] error disposing previous instance: ${err}`);
      }
      activeCleanup = null;
    }

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
    // Telemetry (non-immediate) batching. Debounce is the quiet gap before a
    // flush; maxWait is the hard cap so a steady stream still delivers. Safety
    // and lifecycle events bypass both and flush immediately.
    const telemetryDebounceMs =
      typeof options.telemetryDebounceMs === "number"
        ? options.telemetryDebounceMs
        : 30_000;
    const telemetryMaxWaitMs =
      typeof options.telemetryMaxWaitMs === "number"
        ? options.telemetryMaxWaitMs
        : 120_000;

    log(
      `[${instanceId}] initializing (endpoint=${ninaEndpoint}, interval=${intervalCheckMinutes}, debounce=${telemetryDebounceMs}ms, maxWait=${telemetryMaxWaitMs}ms)`,
    );

    const savedSessionID = await ctx.storage.get("targetSessionID");
    let targetSessionID =
      typeof savedSessionID === "string" ? savedSessionID : undefined;
    if (targetSessionID) {
      log(`[${instanceId}] restored target session: ${targetSessionID}`);
    }

    const promptHook = await ctx.session.hook("prompt", async (event) => {
      targetSessionID = event.sessionID;
      log(`[${instanceId}] target session set from prompt: ${targetSessionID}`);
      try {
        await ctx.storage.set("targetSessionID", event.sessionID);
        log(`[${instanceId}] persisted target session: ${event.sessionID}`);
      } catch (err) {
        log(`[${instanceId}] failed to persist session ID: ${err}`);
      }
    });

    async function triggerIntervention(events: unknown = null) {
      const text =
        events == null
          ? "Routine interval check. No new N.I.N.A. events."
          : `New N.I.N.A. events.\n\n\`\`\`jsonl\n${toJSONL(events)}\n\`\`\``;

      if (!targetSessionID) {
        log(`[${instanceId}] no target session yet; skipping injection`);
        return;
      }

      const now = Date.now();

      // 1. In-memory deduplication (catches same-process duplicate triggers)
      if (lastInjectedText === text && now - lastInjectedTime < 30_000) {
        log(
          `[${instanceId}] duplicate prompt suppressed (in-memory, ${now - lastInjectedTime}ms ago)`,
        );
        return;
      }

      // 2. Storage-backed deduplication (catches cross-process / cross-server races)
      try {
        const last = (await ctx.storage.get("lastInjectedPrompt")) as
          { text?: string; time?: number } | undefined;
        if (
          last &&
          last.text === text &&
          typeof last.time === "number" &&
          now - last.time < 30_000
        ) {
          log(
            `[${instanceId}] duplicate prompt suppressed (storage, ${now - last.time}ms ago)`,
          );
          return;
        }
        await ctx.storage.set("lastInjectedPrompt", { text, time: now });
      } catch (err) {
        log(`[${instanceId}] storage dedupe error: ${err}`);
      }

      lastInjectedText = text;
      lastInjectedTime = now;

      log(`[${instanceId}] intervention: ${text}`);
      try {
        await ctx.session.prompt({ sessionID: targetSessionID as any, text });
      } catch (err) {
        log(`[${instanceId}] prompt failed: ${err}`);
      }
    }

    let eventBatch: unknown[] = [];
    let batchHasImmediate = false;
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;
    let maxWaitTimer: ReturnType<typeof setTimeout> | null = null;
    let isFlushing = false;

    async function flushBatch() {
      if (debounceTimer) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }
      if (maxWaitTimer) {
        clearTimeout(maxWaitTimer);
        maxWaitTimer = null;
      }
      if (eventBatch.length === 0 || isFlushing) return;

      isFlushing = true;
      batchHasImmediate = false;
      try {
        const batch = eventBatch;
        eventBatch = [];
        await triggerIntervention(batch);
      } finally {
        isFlushing = false;
        // If more events arrived while flushing, schedule another pass —
        // immediately if one of them was a safety/lifecycle event.
        if (eventBatch.length > 0 && !debounceTimer) {
          debounceTimer = setTimeout(
            () => {
              flushBatch().catch((err) =>
                log(`[${instanceId}] flushBatch retry failed: ${err}`),
              );
            },
            batchHasImmediate ? 0 : telemetryDebounceMs,
          );
        }
      }
    }

    function scheduleTelemetryFlush() {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        flushBatch().catch((err) =>
          log(`[${instanceId}] flushBatch failed: ${err}`),
        );
      }, telemetryDebounceMs);

      if (!maxWaitTimer) {
        maxWaitTimer = setTimeout(() => {
          flushBatch().catch((err) =>
            log(`[${instanceId}] maxWait flushBatch failed: ${err}`),
          );
        }, telemetryMaxWaitMs);
      }
    }

    function pushEvent(response: unknown) {
      // Append in order so an early flush never reorders ahead of prior frames.
      eventBatch.push(response);

      // Safety, sequence lifecycle, and errors bypass the batch window: deliver
      // now (including anything already queued). This keeps is_safe flips and
      // RUN-FINISHED transitions instant while per-frame telemetry batches up.
      if (isImmediate(response)) {
        batchHasImmediate = true;
        flushBatch().catch((err) =>
          log(`[${instanceId}] immediate flush failed: ${err}`),
        );
        return;
      }

      scheduleTelemetryFlush();
    }

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    function cleanupSocket() {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (ws) {
        try {
          ws.onopen = null;
          ws.onmessage = null;
          ws.onclose = null;
          ws.onerror = null;
          ws.close();
        } catch {
          /* ignore */
        }
        ws = null;
      }
    }

    function connect() {
      if (closed) return;
      cleanupSocket();

      const url = `ws://${ninaEndpoint}/v2/socket`;
      log(`[${instanceId}] connecting to: ${url}`);
      const socket = new WebSocket(url);
      ws = socket;

      socket.onopen = () => {
        if (closed || ws !== socket) return;
        log(`[${instanceId}] ws onopen`);
        socket.send("SUB /socket");
      };

      socket.onmessage = (event) => {
        if (closed || ws !== socket) return;
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
            `[${instanceId}] ws onmessage ${JSON.stringify(response, null, 2)}`,
          );
          pushEvent(response);
        }
      };

      socket.onclose = () => {
        if (closed || ws !== socket) return;
        log(`[${instanceId}] ws onclose`);
        reconnectTimer = setTimeout(connect, 5_000);
      };

      socket.onerror = () => {
        if (closed || ws !== socket) return;
        log(`[${instanceId}] ws error`);
        socket.close();
      };
    }

    connect();

    let intervalId: ReturnType<typeof setInterval> | null = null;
    if (intervalCheckMinutes > 0) {
      intervalId = setInterval(() => {
        if (!(ws && ws.readyState === WebSocket.OPEN)) return;
        triggerIntervention().catch((err) =>
          log(`[${instanceId}] interval trigger failed: ${err}`),
        );
      }, intervalCheckMinutes * 60_000);
    }

    const cleanup = async () => {
      if (activeCleanup === cleanup) {
        activeCleanup = null;
      }
      closed = true;
      await promptHook.dispose();
      if (intervalId !== null) {
        clearInterval(intervalId);
        intervalId = null;
      }
      if (debounceTimer) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }
      if (maxWaitTimer) {
        clearTimeout(maxWaitTimer);
        maxWaitTimer = null;
      }
      cleanupSocket();
      log(`[${instanceId}] disposed`);
    };

    activeCleanup = cleanup;
    return cleanup;
  },
});
