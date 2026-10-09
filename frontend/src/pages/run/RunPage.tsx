import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router'
import { useRunEvents } from '../../api/events'
import { useResource } from '../../api/hooks'
import { FINISHED } from '../../api/types'
import type { RunDetail, RunEvent, RunStatus } from '../../api/types'
import { Button } from '../../ui/Button'
import { EmptyState, Notice } from '../../ui/Notice'
import { PageHeader } from '../../ui/PageHeader'
import { SkeletonLines } from '../../ui/Skeleton'
import type { LogLine } from '../../ui/LogView'
import { RunProgress } from './RunProgress'
import { RunResult } from './RunResult'
import './run.css'

const MAX_LIVE_LINES = 20_000

export function RunPage() {
  const { id = '' } = useParams()
  const detail = useResource<RunDetail>(`/runs/${encodeURIComponent(id)}`)
  const [live, setLive] = useState<{ status: RunStatus | null; lines: LogLine[] }>({ status: null, lines: [] })
  const { reload } = detail
  const run = detail.data?.run
  const following = run !== undefined && !FINISHED.has(run.status)

  const onEvent = useCallback(
    (event: RunEvent) => {
      if (event.kind === 'log') {
        setLive((current) => ({
          ...current,
          lines: [...current.lines.slice(-MAX_LIVE_LINES), { source: event.source, line: event.line }],
        }))
        return
      }
      if (event.status === 'deleted') return
      setLive((current) => ({ ...current, status: event.status as RunStatus }))
      // Stage timings, results and messages come with the record, so it is fetched again.
      if (!event.snapshot) reload()
    },
    [reload],
  )
  useRunEvents(following ? id : null, onEvent)

  if (detail.loading) {
    return (
      <>
        <PageHeader title="Run" context={<Link to="/runs">Runs</Link>} />
        <SkeletonLines lines={8} />
      </>
    )
  }
  if (detail.error?.status === 404) {
    return (
      <>
        <PageHeader title="Run not found" context={<Link to="/runs">Runs</Link>} />
        <EmptyState title="There is no run with this id" action={<Link to="/runs">All runs</Link>}>
          It may have been deleted.
        </EmptyState>
      </>
    )
  }
  if (detail.error || !detail.data || !run) {
    return (
      <>
        <PageHeader title="Run" context={<Link to="/runs">Runs</Link>} />
        <Notice tone="danger" title="This run could not be loaded" action={<Button size="sm" onClick={reload}>Try again</Button>}>
          {detail.error?.message}
        </Notice>
      </>
    )
  }
  if (!FINISHED.has(run.status)) {
    return <RunProgress detail={detail.data} lines={live.lines} />
  }
  return <RunResult detail={detail.data} onChanged={reload} />
}
