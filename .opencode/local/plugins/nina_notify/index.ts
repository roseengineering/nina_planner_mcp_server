import { Plugin } from "@opencode/plugin";
import * as fs from "node:fs";

type Logs = string | null;

let pluginLogs: Logs = null;

// Wall-clock LOCAL time carrying its UTC offset — e.g.
// `2026-11-05T09:39:10.981-05:00`. Deliberately not UTC, and deliberately not
// bare local either; this is the same shape `to_local_display()` gives every
// `get_events` / `get_logs` timestamp and imaging-metadata `date`, so a wake
// quoted from `get_events` and the line that triggered it read as the same
// clock.
//
// Why local rather than UTC: this log is read next to `PROGRESS.md` and next
// to the wall clock at the telescope, and with the telegram-bot bridge the
// observing session and the session a prompt lands in are frequently
// *different* opencode sessions — UTC would have to be mentally converted
// every time (14:39Z while the observer reads 09:39).
//
// Why the offset is kept anyway: it is what makes a local stamp *unambiguous*
// — a bare local time cannot say which side of a DST boundary it fell on
// (this file's own `PROGRESS.md` headers read `-04:00` in October and
// `-05:00` in November), and it lets any line be lined up against a `…Z`
// stamp by arithmetic, or against a `get_events` event from a host in another
// zone. Milliseconds are kept: two sessions waking inside the same second is
// exactly the confusion this timestamp exists to resolve.
function localTime(at: Date = new Date()): string {
  const pad = (n: number, width = 2) => String(n).padStart(width, "0");
  // Minutes east of UTC; `Date.getTimezoneOffset()` reports the inverse.
  const offsetMin = -at.getTimezoneOffset();
  const abs = Math.abs(offsetMin);
  const offset = `${offsetMin < 0 ? "-" : "+"}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}` +
    `.${pad(at.getMilliseconds(), 3)}${offset}`
  );
}

function log(message: string) {
  if (!pluginLogs) return;
  const line = `[${localTime()}] ${message}`;
  try {
    fs.appendFileSync(pluginLogs, `${line}\n`);
  } catch {
    // If configured path failed (e.g. Windows path on POSIX), try /tmp fallback
    if (process.platform !== "win32") {
      try {
        fs.appendFileSync("/tmp/nina-notify-debug.log", `${line}\n`);
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
//
// A fourth lane exists: `RECOVERY:` wakes are injected once per dispose/init
// cycle by reconcileGap() when N.I.N.A. events landed while this plugin was
// evicted (see "Downtime reconciliation" below) — they carry the same
// IMMEDIATE_EVENT classification as live wakes, forced past the dedupe.
// Verified 2026-11-06: a backdated 6h25m mark produced exactly one wake
// enumerating the run-end events missed that morning, then went quiet.
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
// No N.I.N.A. event for this long while a sequence claims to be running =
// suspicious. Frame cadence is ~14-17s, and N.I.N.A. goes quiet without ever
// closing the socket when the network drops (the 2026-11-04/05 night missed
// the run-ending SEQUENCE-FINISHED with no `onclose` logged anywhere), so
// `nina_notify_health` flags it rather than trusting an open socket.
const SOCKET_STALE_MS = 10 * 60_000;
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
        : -1;
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

    // Pinned target: written ONLY by the `nina_notify_arm` tool, and it wins
    // over the automatic last-prompt fallback above. The automatic arm drifted
    // to stale sessions in the field — opencode re-runs prompt admission when
    // an old session is opened/attached/resumed (hook fires with no new
    // message in that session), and each fire silently re-targeted the wakes
    // to whatever historical session was last touched. While pinned, other
    // sessions' prompts may no longer re-arm; they are logged and ignored.
    const savedPin = await ctx.storage.get("pinnedSessionID");
    let pinnedSessionID =
      typeof savedPin === "string" && savedPin ? savedPin : undefined;
    if (pinnedSessionID) {
      log(`[${instanceId}] restored pinned session: ${pinnedSessionID}`);
    }

    const promptHook = await ctx.session.hook("prompt", async (event) => {
      if (pinnedSessionID && event.sessionID !== pinnedSessionID) {
        log(
          `[${instanceId}] re-arm ignored (pinned to ${pinnedSessionID}): attempted by ${event.sessionID}`,
        );
        return;
      }
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
    const startedAt = Date.now();
    let lastWakeAt = startedAt;
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

    // Where a wake goes. `pinnedSessionID` — written only by the
    // `nina_notify_arm` tool — always wins; `targetSessionID` is the
    // automatic last-prompt fallback used before anything is pinned.
    // Storage is re-read on EVERY send because several opencode processes
    // (the bot-facing server, the desktop service, standalone TUIs) each
    // load this plugin, each holds its own websocket to N.I.N.A., and each
    // caches these ids in memory from `setup()` — a pin made in one process
    // must be visible to all, and a long-lived process must never keep
    // sending to a startup-time stale id.
    async function resolveTarget(): Promise<string | undefined> {
      try {
        const pin = await ctx.storage.get("pinnedSessionID");
        if (typeof pin === "string" && pin && pin !== pinnedSessionID) {
          pinnedSessionID = pin;
          log(`[${instanceId}] pinned session picked up from storage: ${pin}`);
        }
        const auto = await ctx.storage.get("targetSessionID");
        if (typeof auto === "string" && auto && auto !== targetSessionID) {
          targetSessionID = auto;
          log(`[${instanceId}] target session picked up from storage: ${auto}`);
        }
      } catch (err) {
        log(`[${instanceId}] storage refresh error: ${err}`);
      }
      return pinnedSessionID ?? targetSessionID;
    }

    async function sendIntervention(events: unknown = null) {
      const since = Math.max(60, Math.round((Date.now() - lastWakeAt) / 1000));
      const body =
        events == null
          ? "Routine interval check. No new immediate N.I.N.A. events."
          : `New N.I.N.A. events.\n\n\`\`\`jsonl\n${toJSONL(events)}\n\`\`\``;
      await deliverPrompt(`${body}${contextHint(since)}`);
    }

    // One delivery: resolve target → dedupe → log → inject. `force` skips
    // the 30s dedupe — an operator action (arm confirmation, explicit test
    // wake) must never be swallowed by a prompt that happens to look
    // identical to the last one.
    async function deliverPrompt(text: string, opts: { force?: boolean } = {}) {
      const target = await resolveTarget();
      if (!target) {
        log(`[${instanceId}] no target session yet; skipping injection`);
        return;
      }

      const now = Date.now();

      if (!opts.force) {
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
      }

      lastInjectedText = text;
      lastInjectedTime = now;

      // The receiving session is logged with the event, not just when it is
      // first set: with the timestamp this makes each wake line self-contained
      // — (when) it fired and (which session) it landed in — which is the only
      // way to line this file up against a `PROGRESS.md` written by a
      // different session.
      log(`[${instanceId}] intervention -> session=${target}: ${text}`);
      try {
        await ctx.session.prompt({ sessionID: target as any, text });
      } catch (err) {
        log(`[${instanceId}] prompt failed (session=${target}): ${err}`);
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

    // Direct (non-event) deliveries — arm confirmations and test wakes —
    // serialize on the same chain as event wakes so two of them can never
    // interleave their storage reads/writes.
    function enqueueDelivery(
      text: string,
      opts: { force?: boolean } = {},
    ): Promise<void> {
      promptChain = promptChain
        .then(() => deliverPrompt(text, opts))
        .catch((err) => {
          log(`[${instanceId}] delivery chain error: ${err}`);
        });
      return promptChain;
    }

    // --- Downtime reconciliation --------------------------------------------
    // OpenCode's server garbage-collects idle project "locations", and
    // evicting a location disposes this plugin (observed 2026-11-05/06: the
    // 02:42 local eviction swallowed the run's RUN-FINISHED — no wake, no
    // wrap-up). The plugin cannot prevent eviction, so it keeps a persisted
    // high-water mark of the last N.I.N.A. event it saw, and on the next init
    // reads N.I.N.A.'s own event-history for the gap: one consolidated
    // forced RECOVERY wake when anything wake-worthy was missed, `gap clean`
    // and silence when not.
    const HIGHWATER_KEY = "lastEventAt";
    // At most one storage write per 30s from the hot path; `cleanup()` and
    // the gap reconcile write unconditionally (force).
    const HIGHWATER_THROTTLE_MS = 30_000;
    // Below this, an init is an ordinary fast restart (plugin-file watcher,
    // config save): no fetch, no wake.
    const GAP_MIN_MS = 60_000;
    const GAP_FETCH_TIMEOUT_MS = 5_000;
    const GAP_RETRY_DELAY_MS = 30_000;
    // How many missed events the RECOVERY text enumerates before truncating.
    const RECOVERY_LIST_CAP = 20;
    // When the last persisted write happened (throttle bookkeeping).
    let highWaterWrittenAt = 0;
    // One gap reconciliation per instance; the fetch already retries once.
    let gapReconciled = false;

    // Persist the high-water mark. Never throws: this runs from the websocket
    // hot path and from cleanup(), and a storage hiccup must not take the
    // event stream down (same rule as `failed to persist session ID`). Also
    // refuses to write a zero/no-traffic value, so an instance that saw
    // nothing can never clobber its predecessor's mark.
    async function persistHighWater(at: number, force = false): Promise<void> {
      try {
        if (at <= 0) return;
        const now = Date.now();
        if (!force && now - highWaterWrittenAt < HIGHWATER_THROTTLE_MS) return;
        highWaterWrittenAt = now;
        await ctx.storage.set(HIGHWATER_KEY, new Date(at).toISOString());
      } catch (err) {
        log(`[${instanceId}] failed to persist high-water mark: ${err}`);
      }
    }

    function formatDuration(ms: number): string {
      const total = Math.max(0, Math.round(ms / 1000));
      if (total < 60) return `${total}s`;
      const minutes = Math.round(total / 60);
      if (minutes < 60) return `${minutes}m`;
      const hours = Math.floor(minutes / 60);
      const rest = minutes % 60;
      return rest ? `${hours}h${rest}m` : `${hours}h`;
    }

    // GET /v2/api/event-history — the same endpoint nina_planner's
    // `get_events` reads (client.api_get("/event-history") under
    // http://<endpoint>/v2/api). Two attempts (one retry after ~30s), then
    // null: the caller leaves the mark unadvanced so the NEXT init can retry
    // the whole gap, and `setup()` never sees a throw.
    async function fetchEventHistory(): Promise<any[] | null> {
      const url = `http://${ninaEndpoint}/v2/api/event-history`;
      for (let attempt = 1; attempt <= 2; attempt++) {
        try {
          const res = await fetch(url, {
            signal: AbortSignal.timeout(GAP_FETCH_TIMEOUT_MS),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const payload: any = await res.json();
          const rows: any = Array.isArray(payload?.Response)
            ? payload.Response
            : Array.isArray(payload)
              ? payload
              : null;
          if (!Array.isArray(rows)) {
            throw new Error("unexpected event-history payload shape");
          }
          return rows;
        } catch (err) {
          log(
            `[${instanceId}] event-history fetch failed (attempt ${attempt}/2): ${err}`,
          );
          if (attempt < 2) {
            await new Promise((resolve) =>
              setTimeout(resolve, GAP_RETRY_DELAY_MS),
            );
          }
        }
      }
      return null;
    }

    // Reconcile the downtime between this instance's predecessor and now.
    // Runs once, from the first successful socket open, after the sequence
    // state seed; never throws (the caller fences it too).
    async function reconcileGap(): Promise<void> {
      if (gapReconciled) return;
      gapReconciled = true;

      let markIso: unknown;
      try {
        markIso = await ctx.storage.get(HIGHWATER_KEY);
      } catch (err) {
        log(
          `[${instanceId}] gap check storage read failed (${err}); mark left untouched`,
        );
        return;
      }
      const markMs = typeof markIso === "string" ? Date.parse(markIso) : NaN;
      const now = Date.now();

      // First run (or unreadable value): start the clock, check nothing.
      if (!Number.isFinite(markMs)) {
        log(`[${instanceId}] no high-water mark; gap check skipped`);
        await persistHighWater(now, true);
        return;
      }

      // Ordinary fast restart: nothing worth reconciling.
      if (now - markMs < GAP_MIN_MS) {
        log(
          `[${instanceId}] gap clean (fast restart, ${formatDuration(now - markMs)} since last event)`,
        );
        await persistHighWater(now, true);
        return;
      }

      log(
        `[${instanceId}] gap detected: ${localTime(new Date(markMs))} → ${localTime(new Date(now))} (${formatDuration(now - markMs)})`,
      );

      const rows = await fetchEventHistory();
      if (!rows) {
        // Do NOT advance the mark: a later init gets to retry the whole gap
        // instead of it being lost silently.
        log(
          `[${instanceId}] gap reconcile given up (event-history unavailable); mark stays at ${localTime(new Date(markMs))} for the next init`,
        );
        return;
      }

      // Parse and order the history. Records are {Time, Event, ...} on the
      // wire — the same shape `get_events` converts — and N.I.N.A.'s clock
      // wears its own UTC offset, so every stamp is parsed to an epoch first.
      const parsed: { name: string; ts: number; record: any }[] = [];
      for (const row of rows) {
        const name = eventText(row);
        const ts = typeof row?.Time === "string" ? Date.parse(row.Time) : NaN;
        if (!name || !Number.isFinite(ts)) continue;
        parsed.push({ name, ts, record: row });
      }
      parsed.sort((a, b) => a.ts - b.ts);

      const missed = parsed.filter((e) => e.ts > markMs);
      const duration = now - markMs;

      // Claim the gap BEFORE waking: a second opencode process loading this
      // plugin right behind us then reads a fresh mark and stays quiet, so
      // one gap produces one RECOVERY wake in the common case.
      await persistHighWater(now, true);

      // History that does not reach back to the mark means N.I.N.A. itself
      // restarted (or trimmed) inside the gap: what we cannot see, we cannot
      // call quiet — the wake says so instead of claiming `gap clean`.
      const uncovered = parsed.length === 0 || parsed[0].ts - markMs > 60_000;

      // The SAME wake-worthy predicate the live path uses — IMMEDIATE_EVENT
      // with the ROUTINE_OVERRIDE / LINK_EVENT demotions — replayed against a
      // LOCAL sequence-running flag, so replaying history can never rewind
      // live state and a live wake can never interleave with this scan.
      let gapRunning = sequenceRunning;
      const wakeworthy: typeof parsed = [];
      for (const e of missed) {
        if (/^(RUN|SEQUENCE)-STARTING/.test(e.name)) gapRunning = true;
        else if (/^(RUN|SEQUENCE)-FINISHED/.test(e.name)) gapRunning = false;
        const housekeeping =
          ROUTINE_OVERRIDE.test(e.name) ||
          (LINK_EVENT.test(e.name) && !gapRunning);
        if (IMMEDIATE_EVENT.test(e.name) && !housekeeping) wakeworthy.push(e);
      }

      // Run endings and safety changes lead the list — they are what the
      // wrap-up and the stow decision hang on.
      const rank = (name: string) =>
        /RUN-FINISHED|SEQUENCE-FINISHED/.test(name)
          ? 0
          : /SAFETY|SAFE|UNSAFE|WEATHER|DOME|ROOF/.test(name)
            ? 1
            : 2;
      wakeworthy.sort((a, b) => rank(a.name) - rank(b.name) || a.ts - b.ts);

      if (wakeworthy.length === 0 && !uncovered) {
        log(
          `[${instanceId}] gap clean (${formatDuration(duration)}, ${missed.length} events read)`,
        );
        return;
      }

      const shown = wakeworthy.slice(0, RECOVERY_LIST_CAP);
      const describe = (e: (typeof parsed)[number]) => {
        const safe = e.record?.IsSafe;
        const detail = typeof safe === "boolean" ? ` (IsSafe=${safe})` : "";
        return `${e.name}${detail} at ${localTime(new Date(e.ts))}`;
      };
      const list =
        shown.map(describe).join("; ") +
        (wakeworthy.length > shown.length
          ? `; +${wakeworthy.length - shown.length} more`
          : "");
      const text =
        `RECOVERY: nina_notify was down ${localTime(new Date(markMs))} → ${localTime(new Date(now))} (${formatDuration(duration)}). ` +
        (wakeworthy.length > 0
          ? `Missed wake-worthy events: ${list}.`
          : `Event-history does not reach back to the gap start, so coverage of the gap could not be verified.`) +
        (uncovered && wakeworthy.length > 0
          ? ` Event-history does not reach back to the gap start, so the list may be incomplete.`
          : "") +
        ` Check sequence state and run night-loop wrap-up if the run ended in the gap.` +
        contextHint(Math.max(60, Math.round(duration / 1000)));
      log(
        `[${instanceId}] gap recovery: ${wakeworthy.length} wake-worthy missed over ${formatDuration(duration)}; injecting RECOVERY wake`,
      );
      await enqueueDelivery(text, { force: true });
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
    // Health bookkeeping for `nina_notify_health`: the 2026-11-04/05 night
    // lost ~2/3 of frame events and the run-ending milestone with NO
    // onclose/onerror logged (silent drop), so "socket exists" alone proves
    // nothing — last actual traffic does.
    let wsOpenedAt = 0;
    let wsErrors = 0;
    let lastEventAt = 0;
    let lastEventName = "";
    // The setup-time sequence seed can fail while N.I.N.A.'s API is down
    // (the socket retries on its own), so the first successful open re-seeds
    // and then reconciles the predecessor's downtime — exactly once.
    let startupReconciled = false;

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
        wsOpenedAt = Date.now();
        wsErrors = 0;
        log(`[${instanceId}] ws onopen`);
        socket.send("SUB /socket");
        if (!startupReconciled) {
          startupReconciled = true;
          // Re-seed first: both the link-event gate and the gap scan below
          // decide against `sequenceRunning`, which is stuck at its safe
          // default if the setup-time seed failed.
          seedSequenceState()
            .catch(() => undefined)
            .then(() => reconcileGap())
            .catch((err) =>
              log(`[${instanceId}] gap reconcile failed: ${err}`),
            );
        }
      };

      socket.onmessage = (event) => {
        if (closed || ws !== socket) return;
        let msg: any;
        try {
          msg = JSON.parse(String(event.data));
        } catch {
          return;
        }
        lastEventAt = Date.now();
        lastEventName = eventText(msg?.Response) || lastEventName;
        if (msg?.Response) {
          const response =
            typeof msg.Response === "string"
              ? { Event: msg.Response }
              : msg.Response;
          log(
            `[${instanceId}] ws onmessage ${JSON.stringify(response, null, 2)}`,
          );
          // Throttled high-water persist: this is the value the next init's
          // gap check reads after an eviction disposes us.
          persistHighWater(lastEventAt).catch((err) =>
            log(`[${instanceId}] high-water persist failed: ${err}`),
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
        wsErrors++;
        log(`[${instanceId}] ws error`);
        socket.close();
      };
    }

    // Seed before the socket opens: no event can arrive ahead of this, so the
    // link gate is always decided against a real "is anything imaging" answer.
    await seedSequenceState();

    connect();

    // --- Operator tools ------------------------------------------------------
    // Arming used to be an invisible side effect of "whichever session was
    // prompted last", which drifted to stale sessions whenever an old session
    // was opened (replayed prompt admission re-fired the hook). These three
    // tools make it explicit and verifiable:
    //   nina_notify_arm    pin THIS (or a given) session + send a confirmation
    //                      through the real wake-delivery path
    //   nina_notify_health  what is pinned, is the event socket actually
    //                      alive. Named "health" after a bare "status" kept
    //                      resolving to the nina-planner `get_nina_status` /
    //                      `get_site_equipment_status` probes instead of this
    //                      pipeline — a noun reads as a report, whereas a
    //                      verb-shaped name ("live", "ready", "sync") invites
    //                      the model to assume the tool *does* something.
    //   nina_notify_test   fire one wake through the exact path real wakes
    //                      take, changing NO pin — kept separate from `arm`
    //                      (folding it in as `test:true` was reverted
    //                      2026-11-05) so a re-verification can never be one
    //                      forgotten flag away from re-targeting the wakes.
    // Read and write stay separate: `health` never wakes anyone, so the
    // once-per-session health check can be called as often as it is useful.
    async function buildStatus() {
      const target = await resolveTarget();
      let title = "";
      if (target) {
        try {
          const info = await ctx.session.get({ sessionID: target as any });
          title = (info as { title?: string }).title ?? "";
        } catch {
          // A vanished session still answers "what is pinned" by id alone.
        }
      }
      const now = Date.now();
      const WS_STATES = ["CONNECTING", "OPEN", "CLOSING", "CLOSED"];
      const quietSince = lastEventAt || wsOpenedAt || startedAt;
      const quietForSec = Math.round((now - quietSince) / 1000);
      const staleSocket = sequenceRunning && now - quietSince > SOCKET_STALE_MS;
      return {
        pinnedSessionID: pinnedSessionID ?? null,
        autoFallbackSessionID: targetSessionID ?? null,
        effectiveTarget: target ?? null,
        effectiveTargetTitle: title,
        eventSocket: {
          url: `ws://${ninaEndpoint}/v2/socket`,
          state: ws
            ? (WS_STATES[ws.readyState] ?? String(ws.readyState))
            : "NONE",
          errorsSinceLastOpen: wsErrors,
          lastOpenAt: wsOpenedAt ? new Date(wsOpenedAt).toISOString() : null,
          lastEventAt: lastEventAt ? new Date(lastEventAt).toISOString() : null,
          lastEventName: lastEventName || null,
          secondsSinceLastTraffic: quietForSec,
          staleWhileSequenceRunning: staleSocket,
        },
        sequenceRunning,
        intervalCheckMinutes,
        qualityWatchdog,
        watchdog: { kind: currentKind || "unknown", hfrStreak, starsStreak },
        lastWakeAt: new Date(lastWakeAt).toISOString(),
        instanceStartedAt: new Date(startedAt).toISOString(),
        staleSocketWarning: staleSocket
          ? `no N.I.N.A. event for ${quietForSec}s while a sequence is running — the event socket has likely dropped silently; reconnect happens on close only, so verify with a get_events call or restart the opencode server`
          : null,
      };
    }

    let toolReg: { dispose(): Promise<void> } | null = null;
    try {
      if (typeof ctx.tool?.transform === "function") {
        toolReg = await ctx.tool.transform((editor) => {
          editor.namespace({
            name: "nina_notify",
            description:
              "Arm, inspect, and test the nina-notify wake pipeline that prompts the observation controller on N.I.N.A. milestones, faults, and safety changes.",
          });

          editor.add({
            name: "arm",
            description:
              "Pin the observation-controller session as nina-notify's wake target and prove delivery. Call this as the FIRST action of every observatory session, and again after creating a new controller session. Pins the calling session unless sessionID is given; once pinned, no other session's prompts can re-target the wakes. Sends a confirmation through the real wake-delivery path — seeing it appear proves end-to-end delivery. Answers the nina-notify wake pipeline, not N.I.N.A. API liveness — that is get_nina_status / get_site_equipment_status. (To re-prove delivery WITHOUT re-pinning, use nina_notify_test.)",
            input: {
              type: "object",
              properties: {
                sessionID: {
                  type: "string",
                  description:
                    "Session id to pin; defaults to the calling session.",
                },
              },
              additionalProperties: false,
            },
            options: { namespace: "nina_notify", codemode: true },
            execute: async (input, context) => {
              const sessionID =
                (input as { sessionID?: string }).sessionID ||
                (context as { sessionID?: string }).sessionID;
              if (!sessionID) {
                return { content: "arm failed: no session id available" };
              }
              let title = "";
              try {
                const info = await ctx.session.get({
                  sessionID: sessionID as any,
                });
                title = (info as { title?: string }).title ?? "";
              } catch (err) {
                return {
                  content: `arm failed: session ${sessionID} not found (${err})`,
                };
              }
              pinnedSessionID = sessionID;
              targetSessionID = sessionID;
              try {
                await ctx.storage.set("pinnedSessionID", sessionID);
                await ctx.storage.set("targetSessionID", sessionID);
              } catch (err) {
                log(`[${instanceId}] arm: failed to persist pin: ${err}`);
                return {
                  content: `arm failed: could not persist pin (${err})`,
                };
              }
              log(
                `[${instanceId}] ARM: pinned session=${sessionID} title="${title}" (by ${(context as { sessionID?: string }).sessionID ?? "unknown"})`,
              );
              const confirm =
                `✅ nina-notify armed → session=${sessionID}${title ? ` "${title}"` : ""}.\n\n` +
                `This confirmation travelled the same delivery path as real wakes — if you can read it, intervention delivery works end-to-end.`;
              await enqueueDelivery(confirm, { force: true });
              return {
                content: `pinned session=${sessionID} "${title}"; confirmation wake sent — verify it appeared in this session (and in Telegram).`,
              };
            },
          });

          editor.add({
            name: "health",
            description:
              "Health of the nina-notify wake pipeline: what it is pinned to, whether the N.I.N.A. event socket is actually receiving (not just open), and wake history — ask this to answer 'is everything healthy / am I synced?'. NOT N.I.N.A. API liveness or equipment state: for those use get_nina_status / get_site_equipment_status from nina-planner. Read-only: it never wakes anyone and arms nothing.",
            input: {
              type: "object",
              properties: {},
              additionalProperties: false,
            },
            options: { namespace: "nina_notify", codemode: true },
            execute: async () => ({
              content: JSON.stringify(await buildStatus(), null, 2),
            }),
          });

          editor.add({
            name: "test",
            description:
              "Fire one test wake through the exact path real N.I.N.A. wakes take (dedupe bypassed) and return socket health. Run once after arming, before leaving the observatory unattended. Changes NO pin — it re-proves delivery only, so it can never re-target the wakes the way calling arm again would. For N.I.N.A. API liveness/equipment use get_nina_status / get_site_equipment_status instead.",
            input: {
              type: "object",
              properties: {},
              additionalProperties: false,
            },
            options: { namespace: "nina_notify", codemode: true },
            execute: async () => {
              const status = await buildStatus();
              log(
                `[${instanceId}] TEST wake requested (target=${status.effectiveTarget ?? "none"})`,
              );
              const text =
                `🧪 nina-notify TEST WAKE — armed → session=${status.effectiveTarget ?? "NONE"}. ` +
                `No action required; this only proves the delivery path.`;
              await enqueueDelivery(text, { force: true });
              return { content: JSON.stringify(status, null, 2) };
            },
          });
        });
        log(
          `[${instanceId}] tools registered: nina_notify_arm, nina_notify_health, nina_notify_test`,
        );
      } else {
        log(
          `[${instanceId}] ctx.tool.transform unavailable; tools not registered`,
        );
      }
    } catch (err) {
      // Tools are additive: a plugin API mismatch must never take down the
      // event pipeline itself.
      log(`[${instanceId}] tool registration failed: ${err}`);
    }

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
      if (toolReg) {
        try {
          await toolReg.dispose();
        } catch {
          /* registration may already be gone with the plugin */
        }
        toolReg = null;
      }
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
      // Last act before disposal: freeze the mark where this instance last
      // saw N.I.N.A., so the next init's gap check measures exactly this
      // instance's downtime. No-op when this instance saw no traffic (its
      // predecessor's mark — written by live events or the reconcile — must
      // not be clobbered by a zero).
      await persistHighWater(lastEventAt, true);
      log(`[${instanceId}] disposed`);
    };

    activeCleanup = cleanup;
    return cleanup;
  },
});
