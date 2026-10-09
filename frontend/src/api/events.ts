import { useEffect, useRef } from 'react'
import type { RunEvent } from './types'

const FINAL = new Set(['done', 'failed', 'cancelled', 'deleted'])

/** Follows the server's event stream. The browser reconnects by itself after a drop and sends
 * the last event number, so nothing that the server still holds is missed. A run's own stream
 * is closed once the run has ended, or the browser would keep reconnecting to it. */
function useEventStream(path: string | null, onEvent: (event: RunEvent) => void, closeWhenFinished: boolean) {
  const handler = useRef(onEvent)
  useEffect(() => {
    handler.current = onEvent
  })

  useEffect(() => {
    if (path === null) return
    const source = new EventSource(`/api${path}`)
    const listen = (kind: 'state' | 'log') => (message: MessageEvent<string>) => {
      const data = JSON.parse(message.data) as Record<string, unknown>
      const seq = message.lastEventId ? Number(message.lastEventId) : null
      handler.current({ ...data, kind, seq } as RunEvent)
      if (closeWhenFinished && kind === 'state' && FINAL.has(String(data.status))) source.close()
    }
    const onState = listen('state')
    const onLog = listen('log')
    source.addEventListener('state', onState)
    source.addEventListener('log', onLog)
    return () => {
      source.removeEventListener('state', onState)
      source.removeEventListener('log', onLog)
      source.close()
    }
  }, [path, closeWhenFinished])
}

export function useRunEvents(runId: string | null, onEvent: (event: RunEvent) => void) {
  useEventStream(runId ? `/runs/${encodeURIComponent(runId)}/events` : null, onEvent, true)
}

export function useAllEvents(onEvent: (event: RunEvent) => void, enabled = true) {
  useEventStream(enabled ? '/events' : null, onEvent, false)
}
