/**
 * Live job progress over SSE, with a polling fallback.
 *
 * Extracted from the pattern the render store established: the stream is the
 * source of truth while a job runs, and if it drops — a reload, a sleeping
 * laptop, a proxy timing out — polling takes over so a job in progress is never
 * lost from the UI.
 *
 * Generic over the event and job shapes so the Shorts store can reuse it
 * without sharing state with the long-render store.
 */

export interface JobStreamOptions<TEvent, TJob> {
  /** SSE endpoint for this job. */
  url: string
  /** Fetches the current job record; used by the polling fallback. */
  poll: () => Promise<TJob>
  onEvent: (event: TEvent) => void
  onPoll: (job: TJob) => void
  /** Called once the job reaches a terminal state, whichever path saw it. */
  onFinish: () => void
  isTerminalEvent: (event: TEvent) => boolean
  isTerminalJob: (job: TJob) => boolean
  pollIntervalMs?: number
}

export interface JobStream {
  close: () => void
}

const DEFAULT_POLL_MS = 2000

export function attachJobStream<TEvent, TJob>(
  options: JobStreamOptions<TEvent, TJob>,
): JobStream {
  let source: EventSource | null = null
  let timer: ReturnType<typeof setInterval> | null = null
  let closed = false

  function close() {
    closed = true
    source?.close()
    source = null
    if (timer) clearInterval(timer)
    timer = null
  }

  function finish() {
    close()
    options.onFinish()
  }

  function startPolling() {
    if (closed || timer) return
    timer = setInterval(() => {
      void options
        .poll()
        .then((job) => {
          options.onPoll(job)
          if (options.isTerminalJob(job)) finish()
        })
        .catch(() => undefined)
    }, options.pollIntervalMs ?? DEFAULT_POLL_MS)
  }

  source = new EventSource(options.url)

  source.onmessage = (message) => {
    const event = JSON.parse(message.data as string) as TEvent
    options.onEvent(event)
    if (options.isTerminalEvent(event)) finish()
  }

  source.onerror = () => {
    // Don't give up on the job just because the stream did.
    source?.close()
    source = null
    startPolling()
  }

  return { close }
}
