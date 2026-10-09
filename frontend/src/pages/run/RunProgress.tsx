import { Check, CircleNotch, Stop } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, ApiError } from '../../api/client'
import type { RunDetail, RunStatus } from '../../api/types'
import { describeSettings } from '../../runs/catalog'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { EngineBadge } from '../../ui/EngineBadge'
import { formatDuration } from '../../ui/format'
import { LogView } from '../../ui/LogView'
import type { LogLine } from '../../ui/LogView'
import { PageHeader, Section } from '../../ui/PageHeader'
import { useToast } from '../../ui/toast-context'

const STAGES: { status: RunStatus; label: string; explain: string }[] = [
  { status: 'queued', label: 'Queued', explain: 'Waiting for the terminal; runs go one at a time.' },
  { status: 'compiling', label: 'Compiling', explain: 'MQL5 is compiled with MetaEditor; Python is checked.' },
  { status: 'running', label: 'Running', explain: 'The engine is working through the period.' },
  { status: 'parsing', label: 'Reading results', explain: 'Turning the report or the simulation into deals and metrics.' },
]

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

export function RunProgress({ detail, lines }: { detail: RunDetail; lines: LogLine[] }) {
  const { run, queue_position: position } = detail
  const now = useNow()
  const toast = useToast()
  const [confirm, setConfirm] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const current = STAGES.findIndex((stage) => stage.status === run.status)
  const since = Date.parse(run.started_at ?? run.created_at)
  const elapsed = Math.max(0, (now - since) / 1000)

  const cancel = async () => {
    setConfirm(false)
    setCancelling(true)
    try {
      await api.post(`/runs/${encodeURIComponent(run.id)}/cancel`)
      toast.push({ title: 'Cancelling', message: run.status === 'queued' ? 'The run is taken off the queue.' : 'The engine is being stopped.' })
    } catch (reason) {
      setCancelling(false)
      toast.push({ tone: 'error', title: 'The run could not be cancelled', message: reason instanceof ApiError ? reason.message : String(reason) })
    }
  }

  return (
    <div className="run">
      <PageHeader
        context={
          <>
            <Link to="/runs">Runs</Link> <span className="num faint">{run.id}</span>
          </>
        }
        title={run.strategy_name}
        description={describeSettings(run.settings)}
        actions={
          <Button variant="danger" icon={<Stop size={16} />} busy={cancelling} onClick={() => setConfirm(true)}>
            {cancelling ? 'Cancelling' : 'Cancel run'}
          </Button>
        }
      />
      <div className="run__badges">
        <EngineBadge engine={run.engine} />
      </div>

      <Section title="Progress">
        <ol className="stages" aria-label="Stages">
          {STAGES.map((stage, index) => {
            const state = index < current ? 'done' : index === current ? 'now' : 'later'
            const spent = run.stage_seconds[stage.status]
            return (
              <li key={stage.status} className={`stages__item stages__item--${state}`} aria-current={state === 'now' ? 'step' : undefined}>
                <span className="stages__mark" aria-hidden>
                  {state === 'done' ? <Check size={12} weight="bold" /> : state === 'now' ? <CircleNotch size={12} className="stages__spin" /> : null}
                </span>
                <span className="stages__label">{stage.label}</span>
                <span className="stages__time num faint">
                  {state === 'done' && spent !== undefined ? formatDuration(spent) : ''}
                </span>
                {state === 'now' ? <span className="stages__explain muted">{stage.explain}</span> : null}
              </li>
            )
          })}
        </ol>
        <p className="run__elapsed">
          <span className="muted">{run.status === 'queued' ? 'Waiting for' : 'Running for'}</span>{' '}
          <span className="num">{formatDuration(elapsed)}</span>
          {run.status === 'queued' && position ? (
            <span className="muted">
              . {position === 1 ? 'Next in line.' : `Number ${position} in line.`}
            </span>
          ) : null}
        </p>
      </Section>

      <Section title="Live log">
        <LogView
          lines={lines}
          follow
          height="28rem"
          label="Live log of this run"
          empty={run.status === 'queued' ? 'The log starts when the run does.' : 'Waiting for the first line.'}
        />
      </Section>

      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Cancel this run?"
        width="sm"
        actions={
          <>
            <Button onClick={() => setConfirm(false)}>Keep running</Button>
            <Button variant="danger" onClick={cancel}>
              Cancel run
            </Button>
          </>
        }
      >
        {run.status === 'queued'
          ? 'It has not started; it is simply taken off the queue.'
          : run.engine === 'mt5_tester'
            ? 'The terminal is asked to close, and stopped if it does not. Nothing of this run is kept but its log.'
            : 'The strategy is stopped. Nothing of this run is kept but its log.'}
      </Dialog>
    </div>
  )
}
