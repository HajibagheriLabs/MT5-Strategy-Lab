import { CheckCircle, CircleNotch, PlugsConnected, WarningCircle, XCircle } from '@phosphor-icons/react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import type { ApiError } from '../api/client'
import { useHealth } from '../api/hooks'
import type { Health } from '../api/types'
import { Dialog } from '../ui/Dialog'
import { Skeleton } from '../ui/Skeleton'
import './TerminalStatus.css'

type Look = { tone: 'neutral' | 'active' | 'warning' | 'danger'; icon: ReactNode; label: string; detail: ReactNode }

function describe(health: Health | undefined, error: ApiError | undefined): Look | null {
  if (error) {
    return {
      tone: 'danger',
      icon: <PlugsConnected size={16} aria-hidden />,
      label: 'Server not answering',
      detail: error.message,
    }
  }
  if (!health) return null
  const terminal = health.terminal
  if (health.status === 'unavailable') {
    return {
      tone: 'danger',
      icon: <XCircle size={16} aria-hidden />,
      label: 'Terminal not found',
      detail: health.message ?? 'MetaTrader 5 was not found. Set its path in local.toml.',
    }
  }
  if (health.status === 'attention') {
    return {
      tone: 'warning',
      icon: <WarningCircle size={16} aria-hidden />,
      label: 'Terminal open elsewhere',
      detail: health.message,
    }
  }
  if (terminal.busy_with_run) {
    const waiting = health.queue.queued
    return {
      tone: 'active',
      icon: <CircleNotch size={16} aria-hidden className="terminal-status__spin" />,
      label: waiting ? `Running, ${waiting} waiting` : 'Running a backtest',
      detail: `The terminal is working on run ${terminal.busy_with_run}.`,
    }
  }
  return {
    tone: 'neutral',
    icon: <CheckCircle size={16} aria-hidden />,
    label: 'Terminal ready',
    detail: 'The dedicated terminal is closed and ready to be started for the next run.',
  }
}

type ViewProps = { health: Health | undefined; error: ApiError | undefined }

/** The indicator and its details dialog, from a health answer; the shell feeds it live. */
export function TerminalStatusView({ health, error }: ViewProps) {
  const [open, setOpen] = useState(false)
  const look = describe(health, error)
  if (!look) {
    return (
      <span className="terminal-status terminal-status--loading" aria-label="Checking the terminal">
        <Skeleton width="7.5rem" />
      </span>
    )
  }
  const terminal = health?.terminal
  return (
    <>
      <button
        type="button"
        className={`terminal-status terminal-status--${look.tone}`}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`${look.label}. Show terminal details`}
      >
        {look.icon}
        <span className="terminal-status__label">{look.label}</span>
        {terminal?.server ? <span className="terminal-status__server faint">{terminal.server}</span> : null}
      </button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Terminal" width="md">
        <div className="terminal-details">
          <p className={`terminal-details__lead terminal-details__lead--${look.tone}`}>
            {look.icon}
            <span>{look.label}</span>
          </p>
          {look.detail ? <p className="muted">{look.detail}</p> : null}
          {terminal?.found ? (
            <dl className="terminal-details__list">
              <dt>Install</dt>
              <dd className="num">{terminal.install_dir}</dd>
              <dt>Data folder</dt>
              <dd className="num">{terminal.data_dir}</dd>
              <dt>Portable</dt>
              <dd>{terminal.portable ? 'Yes' : 'No'}</dd>
              <dt>Account server</dt>
              <dd>{terminal.server ?? 'Not set'}</dd>
              <dt>Settings from</dt>
              <dd className="num">{terminal.source}</dd>
              <dt>Running copies</dt>
              <dd className="num">{terminal.running_pids.length ? `PID ${terminal.running_pids.join(', ')}` : 'None'}</dd>
              <dt>Queue</dt>
              <dd>{health ? `${health.queue.queued} waiting` : ''}</dd>
            </dl>
          ) : null}
          {terminal?.notes.length ? (
            <ul className="terminal-details__notes">
              {terminal.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </Dialog>
    </>
  )
}

export function TerminalStatus() {
  const { data, error } = useHealth()
  return <TerminalStatusView health={data} error={error} />
}
