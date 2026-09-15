import type { PluginInput, PluginModule } from "@opencode-ai/plugin";
declare const process: { env: Record<string, string | undefined> };

const NINA_INTERVAL_MINUTES = +(process.env.NINA_INTERVAL_MINUTES || 10)
const NINA_ENDPOINT = process.env.NINA_ENDPOINT || "localhost:1888"
const DEBUG = process.env.DEBUG
const EVENT_AGENT = "event"
const CHECK_AGENT = "check"

const plugin: PluginModule = {
  id: 'nina-plug',
  server: async (ctx: PluginInput) => {
    async function triggerIntervention(reason: string, agent: string) {
      const sessions = await ctx.client.session.list().catch(() => null)
      if (!sessions?.data?.length) return
      const session = sessions.data[0]
      const { data: agents } = await ctx.client.app.agents()
      const exists = agents.some((a: any) => a.name === agent)
      if (DEBUG) console.error(`exists ${exists}`)

      const payload = {
        path: { id: session.id },
        body: {
          ...(exists && { agent: agent }),
          parts: [{ 
              type: "text", 
              text: `[TRIGGER: ${reason}] Autonomous intervention invoked. Action required due to: ${reason}` 
          }],
        },
      }
      if (DEBUG) console.error(payload)
      await ctx.client.session.prompt(payload)
    }

    let eventBatch: string[] = []
    let debounceTimer: ReturnType<typeof setTimeout> | null = null

    function flushBatch() {
      if (DEBUG) console.error(`nina-plugin: eventBatch ${eventBatch}`)
      if (eventBatch.length === 0) return
      const payload = "NINA events: [" + eventBatch.join(",") + "]"
      eventBatch = []
      triggerIntervention(payload, EVENT_AGENT)
    }

    function pushEvent(response: string) {
      if (DEBUG) console.error(`nina-plugin: push event ${response}`)
      eventBatch.push(response)
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(flushBatch, 1_000)
    }

    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let closed = false

    function connect() {
      if (closed) return
      const url = `ws://${NINA_ENDPOINT}/v2/socket`
      if (DEBUG) console.error(`nina-plugin: connecting to ${url}`)
      ws = new WebSocket(url)

      ws.onopen = () => {
        ws!.send("SUB /socket")
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data as string)
          if (msg.Response) {
            pushEvent(JSON.stringify(msg.Response))
          }
        } catch {
        }
      }

      ws.onclose = () => {
        if (!closed) {
          reconnectTimer = setTimeout(connect, 5_000)
        }
      }

      ws.onerror = () => {
        if (DEBUG) console.error('nina-plugin: ws error')
        ws?.close()
      }
    }

    connect()

    const intervalId = setInterval(() => {
      if (DEBUG) console.error('nina-plugin: interval check')
      triggerIntervention(
        "Interval check. Query the observatory sequencer status via the tool sequence_status and check equipment status via the tool get_site_equipment. Decide what to do next — do another sequence or take no action.",
        CHECK_AGENT
      )
    }, NINA_INTERVAL_MINUTES * 60_000)

    return {
      dispose: async () => {
        closed = true
        clearInterval(intervalId)
        if (debounceTimer) clearTimeout(debounceTimer)
        if (reconnectTimer) clearTimeout(reconnectTimer)
        ws?.close()
      }
    }
  },
}

export default plugin
