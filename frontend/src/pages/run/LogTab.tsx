import { useMemo, useState } from 'react'
import { api, ApiError } from '../../api/client'
import { useResource } from '../../api/hooks'
import type { LogSource } from '../../api/types'
import { Button } from '../../ui/Button'
import { LogView } from '../../ui/LogView'
import { Notice } from '../../ui/Notice'
import { SegmentedControl } from '../../ui/SegmentedControl'
import { Skeleton } from '../../ui/Skeleton'
import { useEffect } from 'react'

const NAMES: Record<string, string> = {
  agent: 'Tester agent',
  tester: 'Tester',
  terminal: 'Terminal',
  strategy: 'Strategy output',
}
const ORDER = ['strategy', 'agent', 'tester', 'terminal']

export function LogTab({ runId }: { runId: string }) {
  const sources = useResource<LogSource[]>(`/runs/${encodeURIComponent(runId)}/logs`)
  const available = useMemo(
    () => [...(sources.data ?? [])].sort((a, b) => ORDER.indexOf(a.source) - ORDER.indexOf(b.source)),
    [sources.data],
  )
  const [chosen, setChosen] = useState<string | null>(null)
  const source = chosen ?? available[0]?.source ?? null
  const [text, setText] = useState<{ source: string; body: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!source) return
    let live = true
    api
      .text(`/runs/${encodeURIComponent(runId)}/logs/${source}`)
      .then((body) => {
        if (live) setText({ source, body })
      })
      .catch((reason: unknown) => {
        if (live) setError(reason instanceof ApiError ? reason.message : String(reason))
      })
    return () => {
      live = false
    }
  }, [runId, source])

  const lines = useMemo(
    () => (text && text.source === source ? text.body.split(/\r?\n/).filter((line) => line.length).map((line) => ({ line })) : []),
    [text, source],
  )

  if (sources.error) {
    return (
      <Notice tone="danger" title="The logs could not be listed" action={<Button size="sm" onClick={sources.reload}>Try again</Button>}>
        {sources.error.message}
      </Notice>
    )
  }
  if (!sources.data) return <Skeleton variant="block" height="20rem" />
  if (!available.length) return <p className="muted">This run left no log.</p>

  return (
    <div className="log-tab">
      {available.length > 1 ? (
        <SegmentedControl
          label="Log"
          hideLabel
          size="sm"
          value={source ?? ''}
          onChange={setChosen}
          options={available.map((s) => ({ value: s.source, label: NAMES[s.source] ?? s.source }))}
        />
      ) : null}
      {error ? (
        <Notice tone="danger" title="The log could not be read">
          {error}
        </Notice>
      ) : text?.source === source ? (
        <LogView lines={lines} height="30rem" label={`${NAMES[source ?? ''] ?? source} log`} empty="This log is empty." />
      ) : (
        <Skeleton variant="block" height="30rem" />
      )}
    </div>
  )
}
