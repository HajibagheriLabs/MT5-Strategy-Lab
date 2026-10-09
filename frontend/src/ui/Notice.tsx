import { Info, WarningCircle, XCircle } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import './Notice.css'

type Props = {
  tone?: 'info' | 'warning' | 'danger'
  title: ReactNode
  children?: ReactNode
  /** One action that resolves the situation. */
  action?: ReactNode
}

const ICON = {
  info: <Info size={16} aria-hidden />,
  warning: <WarningCircle size={16} aria-hidden />,
  danger: <XCircle size={16} aria-hidden />,
}

/** An inline message that belongs to a page or a section: what happened and what to do. */
export function Notice({ tone = 'info', title, children, action }: Props) {
  return (
    <div className={`notice notice--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="notice__icon">{ICON[tone]}</span>
      <div className="notice__text">
        <p className="notice__title">{title}</p>
        {children ? <div className="notice__body">{children}</div> : null}
      </div>
      {action ? <div className="notice__action">{action}</div> : null}
    </div>
  )
}

type EmptyProps = { title: ReactNode; children?: ReactNode; action?: ReactNode; icon?: ReactNode }

/** What fills an empty table or page: what will be here, and the one action that fills it. */
export function EmptyState({ title, children, action, icon }: EmptyProps) {
  return (
    <div className="empty">
      {icon ? <span className="empty__icon">{icon}</span> : null}
      <p className="empty__title">{title}</p>
      {children ? <div className="empty__body">{children}</div> : null}
      {action ? <div className="empty__action">{action}</div> : null}
    </div>
  )
}
