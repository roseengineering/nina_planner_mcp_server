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

// The ONLY events that wake the agent. N.I.N.A. drives routine work itself —
// autofocus, dithering and drift correction are its own internal triggers — so
// per-frame `IMAGE-*` saves, guider/autofocus chatter and anything unrecognized
// are deliberately NOT prompted. Nothing is buffered here either: N.I.N.A.
// keeps its own event history server-side (`get_events` -> GET
// /v2/api/event-history), so every wake prompt tells the agent the exact
// `since=` window to read instead.
//
// The waking set covers three of the four event classes:
//   milestones  RUN-*/SEQUENCE-* and TS-NEWTARGETSTART -> evaluate the queue
//   faults      failures, aborts, timeouts and link loss -> diagnose + recover
//   safety      SAFE/UNSAFE/WEATHER/DOME/ROOF -> stow or stand down
// External alerts (transient broker / GCN) are not wired up yet.
const IMMEDIATE_EVENT =
  /SAFETY|SAFE|UNSAFE|WEATHER|DOME|ROOF|RUN-(STARTING|FINISHED)|SEQUENCE-(STARTING|FINISHED)|TS-NEWTARGETSTART|STOW|MOUNT|OFFLINE|DISCONNECT|LOST|EMERGENCY|ERROR|FAIL|EXCEPTION|ABORT|INTERRUPT|CRITICAL|TIMEOUT/i;

// Two demotions keep the waking set honest, both derived from 16h of real
// N.I.N.A. events (476 total -> 45 matched the regex above -> but only 11 of
// the 24 coalesced prompts actually carried a milestone, fault or safety
// change; the other 13 were pure noise):
//
//   ROUTINE_OVERRIDE — matches a wake token yet is housekeeping. MOUNT-CENTER
//     is the slew/settle centering the mount performs itself on every
//     acquisition, and it alone produced 8 of the 45 wake events.
//   LINK_EVENT — every `…-CONNECTED` / `…-DISCONNECTED` device event (a
//     substring match catches both spellings). N.I.N.A. cycles its devices at
//     session start and teardown, which produced 29 of the 45 wake events, and
//     a link change is only operationally interesting while something is
//     imaging — so these wake only while a sequence is running. Safety *state*
//     changes (`SAFETY-CHANGED`) contain neither token and always wake, as do
//     the per-device failures that carry ERROR/FAIL/LOST.
const ROUTINE_OVERRIDE = /MOUNT-CENTER/i;
const LINK_EVENT = /CONNECTED/i;

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

// Quality watchdog thresholds, from the `frame-quality-diagnostics` skill:
// both standing triggers are evaluated over *consecutive* light frames, never
// a single frame, so the one-frame GUIDER-START settles and post-recenter dips
// the skill documents cannot wake the agent.
const HFR_LIMIT_ARCSEC = 3.0;
const STARS_LIMIT = 10;
const STREAK_REQUIRED = 3;
// Light frames land ~14s apart; this only stops a burst of save events from
// costing repeated REST round trips (short exposure runs land faster).
const STATS_MIN_INTERVAL_MS = 5_000;
// One deferred second look when a frame's own statistics are not in the buffer
// yet. N.I.N.A. commits them only after readout and processing, so a sample
// taken at that frame's own IMAGE-SAVE can race the commit.
const STATS_RETRY_DELAY_MS = 1_500;

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
        : 45;
    // Routine telemetry never wakes the agent, so without this a sustained
    // degradation (climbing HFR, collapsing star count) would stay invisible
    // until the next milestone. See the watchdog below.
    const qualityWatchdog =
      typeof options.qualityWatchdog === "boolean"
        ? options.qualityWatchdog
        : true;

    log(
      `[${instanceId}] initializing (endpoint=${ninaEndpoint}, interval=${intervalCheckMinutes}, watchdog=${qualityWatchdog})`,
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

    // Wake bookkeeping. Telemetry is never stored, so the only thing worth
    // remembering is *when* we last woke and *how much* routine traffic went
    // by — both are needed to tell the agent how far back to read.
    let lastWakeAt = Date.now();
    let routineSinceWake = 0;

    // How far back the agent should read N.I.N.A.'s own event history. Capped
    // at 60s so a fresh instance never asks for a zero-width window.
    function contextHint(sinceSeconds: number): string {
      const waiting =
        routineSinceWake > 0
          ? ` (${routineSinceWake} routine events since your last wake)`
          : "";
      return (
        `\n\nRoutine telemetry is not pushed to you — N.I.N.A. keeps it server-side. ` +
        `Call \`get_events(since=${sinceSeconds})\` to read the last ${sinceSeconds}s of it${waiting}; ` +
        `add \`get_logs(since=${sinceSeconds})\` when you need diagnostics.`
      );
    }

    // Prompts are serialized through one chain so an immediate flush, the
    // interval timer and a watchdog wake can never interleave their
    // storage reads/writes.
    let promptChain: Promise<void> = Promise.resolve();

    function triggerIntervention(events: unknown = null): Promise<void> {
      promptChain = promptChain
        .then(() => sendIntervention(events))
        .catch((err) => {
          log(`[${instanceId}] prompt chain error: ${err}`);
        });
      return promptChain;
    }

    async function sendIntervention(events: unknown = null) {
      if (!targetSessionID) {
        log(`[${instanceId}] no target session yet; skipping injection`);
        return;
      }

      const now = Date.now();
      const since = Math.max(60, Math.round((now - lastWakeAt) / 1000));
      const body =
        events == null
          ? "Routine interval check. No new immediate N.I.N.A. events."
          : `New N.I.N.A. events.\n\n\`\`\`jsonl\n${toJSONL(events)}\n\`\`\``;
      const text = `${body}${contextHint(since)}`;

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
        // Clear the dedupe record so the next attempt is not suppressed for
        // 30s by a prompt that never reached the agent.
        try {
          await ctx.storage.set("lastInjectedPrompt", { text: "", time: 0 });
        } catch {
          /* best effort */
        }
        lastInjectedText = null;
        return;
      }

      // Only a delivered prompt closes the window: the next wake's `since=`
      // and routine count must cover everything back to this one.
      lastWakeAt = now;
      routineSinceWake = 0;
    }

    // --- Quality watchdog ---------------------------------------------------
    // The socket carries event names only (confirmed on a live capture:
    // {"Event":"IMAGE-SAVE"} with no statistics attached), so per-frame numbers
    // come from GET /v2/api/image-history, whose current-image record carries
    // Stars, HFR, HFRStDev, Mean, StDev, Temperature — plus `Date` and
    // `ImageType`.
    //
    // Chosen over /equipment/camera/capture/statistics on purpose. That one is
    // a transient "last capture" buffer wiped by any camera reconnect, profile
    // switch or sequence load — it answered `400 No image available` all
    // afternoon because the camera reconnected at 07:12 with no frame since
    // 23:56 — while the 2026-10-27 frame was *still* served from image-history
    // the next day. image-history also makes freshness checkable (`Date`
    // cross-checked against the IMAGE-SAVE event to 0.95ms) and carries
    // `ImageType`, so light-only gating is per-frame rather than inferred from
    // event text alone. Every call is fenced: an unreachable or empty source
    // degrades to "no sample", never to a broken event stream.
    let currentKind = "";
    let hfrStreak = 0;
    let starsStreak = 0;
    let hfrWakeArmed = true;
    let starsWakeArmed = true;
    let lastStatsAt = 0;
    let statsInFlight = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let retryPending = false;
    // `Date` of the last record actually accounted for — the freshness test.
    let lastSampleDate: string | null = null;

    function resetQualityState(kind?: string) {
      hfrStreak = 0;
      starsStreak = 0;
      hfrWakeArmed = true;
      starsWakeArmed = true;
      if (kind !== undefined) currentKind = kind;
    }

    // Frame kind comes from ACQUISITION-STARTING: [{kind}] {target} — the
    // same token night-loop reads. Only `light` frames have stars: without
    // this gate every dark, bias and flat would read as a star-count failure.
    // Runs for waking events too, so a run/target boundary is never missed
    // just because the boundary event itself travelled down the wake lane.
    function trackContext(name: string) {
      if (/^(RUN|SEQUENCE)-STARTING/.test(name)) sequenceRunning = true;
      else if (/^(RUN|SEQUENCE)-FINISHED/.test(name)) sequenceRunning = false;

      const m = /^ACQUISITION-STARTING:\s*\[([^\]]+)\]/.exec(name);
      if (m) {
        resetQualityState(m[1].trim().toLowerCase());
        log(`[${instanceId}] watchdog scope -> ${currentKind}`);
        return;
      }
      if (/^(TS-NEWTARGETSTART|RUN-STARTING|SEQUENCE-STARTING)/.test(name)) {
        resetQualityState();
      }
    }

    // Sequence-running state, which gates device link events. The plugin can
    // attach while N.I.N.A. is already mid-run, before any lifecycle event has
    // arrived, so it is seeded from the API rather than assumed idle.
    let sequenceRunning = true;

    // Same rule as the MCP server's `_sequence_running`: any node anywhere in
    // the payload reporting RUNNING. Kept in lockstep so the plugin and the
    // tools never disagree about whether a sequence is live.
    function payloadHasRunning(node: unknown): boolean {
      if (Array.isArray(node)) return node.some(payloadHasRunning);
      if (node && typeof node === "object") {
        if ((node as { Status?: unknown }).Status === "RUNNING") return true;
        return Object.values(node).some(payloadHasRunning);
      }
      return false;
    }

    // Failure leaves the gate at `true` — the safe default, since an
    // unrecognised link event is still worth surfacing.
    async function seedSequenceState() {
      try {
        const res = await fetch(`http://${ninaEndpoint}/v2/api/sequence/json`, {
          signal: AbortSignal.timeout(3_000),
        });
        if (res.ok) {
          const body = await res.json();
          sequenceRunning = payloadHasRunning(body?.Response ?? body);
        }
        log(`[${instanceId}] sequence running at startup: ${sequenceRunning}`);
      } catch (err) {
        log(
          `[${instanceId}] sequence-state seed failed (${err}); link events stay wake-worthy`,
        );
      }
    }

    // Every non-"ok" outcome is distinct on purpose: only a record whose
    // freshness could be proven may reach the streak counters.
    type StatsResult =
      | {
          status: "ok";
          hfr: number | null;
          stars: number | null;
          date: string;
          imageType: string;
        }
      | { status: "empty" } // nothing recorded yet: a fresh session
      | { status: "stale" } // same record as last time, or no provable Date
      | { status: "error" }; // unreachable or malformed

    async function fetchStats(): Promise<StatsResult> {
      const url = `http://${ninaEndpoint}/v2/api/image-history`;
      let res: Response;
      try {
        res = await fetch(url, { signal: AbortSignal.timeout(3_000) });
      } catch {
        return { status: "error" };
      }
      if (!res.ok) return { status: "error" };
      let payload: any;
      try {
        payload = await res.json();
      } catch {
        return { status: "error" };
      }
      // The default call (no query parameters) returns only the current image.
      const record = Array.isArray(payload?.Response)
        ? payload.Response[0]
        : null;
      if (!record || typeof record !== "object") return { status: "empty" };
      const date =
        typeof record.Date === "string" && record.Date ? record.Date : null;
      // Freshness is the whole basis for treating this as a *new* sample: two
      // IMAGE-SAVE events reading one identical record would count one frame
      // twice and manufacture a sustained-degradation wake. No provable Date,
      // or the same Date as last time -> not a sample.
      if (date === null || date === lastSampleDate) return { status: "stale" };
      const hfr = typeof record.HFR === "number" ? record.HFR : null;
      const stars = typeof record.Stars === "number" ? record.Stars : null;
      const imageType =
        typeof record.ImageType === "string" ? record.ImageType : "";
      return { status: "ok", hfr, stars, date, imageType };
    }

    async function watchdogCheck(isRetry = false) {
      if (!qualityWatchdog) return;
      // A *known* calibration step means don't even poll. An unknown step ("" —
      // the plugin attached after that step's ACQUISITION-STARTING) is allowed
      // through, because the record's own ImageType gates it below: that is
      // what lets the watchdog work when it attaches mid-step.
      if (currentKind !== "" && currentKind !== "light") return;
      const now = Date.now();
      if (statsInFlight) return;
      // A retry deliberately bypasses the spacing gate: it is this same
      // frame's sample, not a new one.
      if (!isRetry && now - lastStatsAt < STATS_MIN_INTERVAL_MS) return;
      statsInFlight = true;
      if (!isRetry) lastStatsAt = now;
      try {
        const result = await fetchStats();
        if (result.status !== "ok") {
          // No sample is not a sample. One deferred look covers both cases —
          // the save event leading the history write (stale) and a session
          // with nothing recorded yet (empty) — after which the streaks stay
          // untouched: a missed sample may only *delay* a detection, never
          // manufacture one. Only the first look may arm that re-check, so a
          // source that never advances cannot escalate into a retry loop.
          if (
            (result.status === "empty" || result.status === "stale") &&
            !isRetry &&
            !retryPending
          ) {
            retryPending = true;
            retryTimer = setTimeout(() => {
              retryTimer = null;
              retryPending = false;
              watchdogCheck(true).catch((err) =>
                log(`[${instanceId}] watchdog retry failed: ${err}`),
              );
            }, STATS_RETRY_DELAY_MS);
          } else if (result.status === "stale" && isRetry) {
            // History not advancing is the one failure mode that could leave
            // the watchdog blind, so surface it instead of sitting inert.
            log(
              `[${instanceId}] image-history still not advanced after re-check; sample skipped`,
            );
          }
          return;
        }
        const stats = result;
        // Account for the record whatever it is, so one frame can never be
        // counted twice even if the same record is read again later.
        lastSampleDate = stats.date;
        // Per-frame proof that a light is a light: darks, biases and flats
        // carry no stars at all, so this is the hard stop behind the
        // event-text gate.
        if (stats.imageType !== "LIGHT") {
          log(
            `[${instanceId}] skipping ${stats.imageType || "untitled"} record for quality`,
          );
          return;
        }

        if (stats.hfr !== null) {
          hfrStreak = stats.hfr > HFR_LIMIT_ARCSEC ? hfrStreak + 1 : 0;
          if (hfrStreak === 0) hfrWakeArmed = true;
        }
        if (stats.stars !== null) {
          starsStreak = stats.stars < STARS_LIMIT ? starsStreak + 1 : 0;
          if (starsStreak === 0) starsWakeArmed = true;
        }

        const faults: string[] = [];
        if (hfrWakeArmed && hfrStreak >= STREAK_REQUIRED) {
          faults.push(
            `HFR ${stats.hfr?.toFixed(2)}" over ${hfrStreak} consecutive light frames (limit ${HFR_LIMIT_ARCSEC}")`,
          );
          hfrWakeArmed = false;
        }
        if (starsWakeArmed && starsStreak >= STREAK_REQUIRED) {
          faults.push(
            `${stats.stars} detected stars over ${starsStreak} consecutive light frames (limit ${STARS_LIMIT})`,
          );
          starsWakeArmed = false;
        }
        if (faults.length === 0) return;

        const summary = `QUALITY-WATCHDOG: ${faults.join("; ")}`;
        log(`[${instanceId}] ${summary}`);
        queueImmediate({ Event: summary });
      } catch (err) {
        log(`[${instanceId}] watchdog check failed: ${err}`);
      } finally {
        statsInFlight = false;
      }
    }

    // --- Event routing ------------------------------------------------------
    // Immediate events are coalesced over a 250ms window: an error cascade
    // (several distinct fault events in a burst) must cost the agent one wake,
    // not one per event. 250ms is imperceptible against an agent wake, which
    // already takes seconds, so safety events still land effectively instantly.
    let pendingImmediate: unknown[] = [];
    let immediateTimer: ReturnType<typeof setTimeout> | null = null;

    function flushImmediate() {
      if (immediateTimer) {
        clearTimeout(immediateTimer);
        immediateTimer = null;
      }
      if (pendingImmediate.length === 0) return;
      const batch = pendingImmediate;
      pendingImmediate = [];
      triggerIntervention(batch).catch((err) =>
        log(`[${instanceId}] immediate flush failed: ${err}`),
      );
    }

    function queueImmediate(response: unknown) {
      pendingImmediate.push(response);
      if (!immediateTimer) immediateTimer = setTimeout(flushImmediate, 250);
    }

    function pushEvent(response: unknown) {
      const name = eventText(response);

      // Track run/target boundaries and frame kind for EVERY event, waking or
      // not: the link gate needs to know whether we are imaging, and the
      // watchdog needs the frame kind regardless of the lane an event takes.
      trackContext(name);

      // Demote an event that matched a wake token but is housekeeping —
      // routine mount centering, or device link chatter while nothing is
      // imaging.
      const housekeeping =
        ROUTINE_OVERRIDE.test(name) ||
        (LINK_EVENT.test(name) && !sequenceRunning);

      // Milestones, faults, safety changes and (while imaging) link loss wake.
      if (isImmediate(response) && !housekeeping) {
        queueImmediate(response);
        return;
      }

      // Counted for the wake digest, then dropped. N.I.N.A. retains it in
      // /v2/api/event-history, which get_events() already reads.
      routineSinceWake++;
      // IMAGE-SAVE only: it is the moment a frame becomes a history record,
      // and this rig emits no IMAGE-PREPARED at all (0 of 476 sampled events)
      // — "prepared" could only ever race the write we then have to read.
      if (/^IMAGE-SAVE$/.test(name)) {
        watchdogCheck().catch((err) =>
          log(`[${instanceId}] watchdog dispatch failed: ${err}`),
        );
      }
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

    // Seed before the socket opens: no event can arrive ahead of this, so the
    // link gate is always decided against a real "is anything imaging" answer.
    await seedSequenceState();

    connect();

    // The interval is the only periodic driver between milestones now that
    // telemetry no longer prompts, so it deliberately fires even when the
    // socket is down — a silently dead event stream is exactly when a status
    // check matters most (safety-interrupt-recovery handles a down N.I.N.A.).
    let intervalId: ReturnType<typeof setInterval> | null = null;
    if (intervalCheckMinutes > 0) {
      intervalId = setInterval(() => {
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
      if (immediateTimer) {
        clearTimeout(immediateTimer);
        immediateTimer = null;
      }
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
      cleanupSocket();
      log(`[${instanceId}] disposed`);
    };

    activeCleanup = cleanup;
    return cleanup;
  },
});
