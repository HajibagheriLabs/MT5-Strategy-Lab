import { CheckCircle, CircleNotch, Clock, FileMagnifyingGlass, Gear, MinusCircle, XCircle } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import type { RunStatus } from '../api/types'
import './StatusBadge.css'

const LOOK: Record<RunStatus, { label: string; icon: ReactNode; tone: 'neutral' | 'active' | 'danger' }> = {
  queued: { label: 'Queued', icon: <Clock size={14} aria-hidden />, tone: 'neutral' },
  compiling: { label: 'Compiling', icon: <Gear size={14} aria-hidden />, tone: 'active' },
  running: { label: 'Running', icon: <CircleNotch size={14} aria-hidden className="status__spin" />, tone: 'active' },
  parsing: { label: 'Reading results', icon: <FileMagnifyingGlass size={14} aria-hidden />, tone: 'active' },
  done: { label: 'Done', icon: <CheckCircle size={14} aria-hidden />, tone: 'neutral' },
  failed: { label: 'Failed', icon: <XCircle size={14} aria-hidden />, tone: 'danger' },
  cancelled: { label: 'Cancelled', icon: <MinusCircle size={14} aria-hidden />, tone: 'neutral' },
}

export function StatusBadge({ status }: { status: RunStatus }) {
  const look = LOOK[status]
  return (
    <span className={`status status--${look.tone}`}>
      {look.icon}
      <span>{look.label}</span>
    </span>
  )
}
