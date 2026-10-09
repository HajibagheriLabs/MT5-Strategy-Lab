import { CheckCircle, Info, WarningCircle, X } from '@phosphor-icons/react'
import { useCallback, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ToastContext } from './toast-context'
import type { ToastInput, ToastTone } from './toast-context'
import './Toast.css'

type Toast = ToastInput & { id: number }

const ICONS: Record<ToastTone, ReactNode> = {
  info: <Info size={16} aria-hidden />,
  done: <CheckCircle size={16} aria-hidden />,
  error: <WarningCircle size={16} aria-hidden />,
}
const LIFETIME_MS = 5000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const next = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (input: ToastInput) => {
      const id = next.current++
      setToasts((current) => [...current.slice(-3), { ...input, id }])
      // Errors stay until dismissed: they usually say what to do next.
      if ((input.tone ?? 'info') !== 'error') window.setTimeout(() => dismiss(id), LIFETIME_MS)
    },
    [dismiss],
  )

  const value = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications">
        <ol className="toasts__list" aria-live="polite">
          {toasts.map((toast) => {
            const tone = toast.tone ?? 'info'
            return (
              <li key={toast.id} className={`toast toast--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
                <span className="toast__icon">{ICONS[tone]}</span>
                <div className="toast__text">
                  <p className="toast__title">{toast.title}</p>
                  {toast.message ? <p className="toast__message">{toast.message}</p> : null}
                </div>
                <button type="button" className="toast__close" aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
                  <X size={14} aria-hidden />
                </button>
              </li>
            )
          })}
        </ol>
      </div>
    </ToastContext.Provider>
  )
}
