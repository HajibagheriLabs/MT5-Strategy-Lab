import { X } from '@phosphor-icons/react'
import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { Button } from './Button'
import './Dialog.css'

type Props = {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  /** Buttons, right-aligned at the bottom; put the safe choice first. */
  actions?: ReactNode
  width?: 'sm' | 'md' | 'lg'
}

/** The native dialog element in modal mode: the page behind is inert, focus stays inside, and
 * Escape closes it. Focus returns to whatever opened it. */
export function Dialog({ open, onClose, title, children, actions, width = 'md' }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className={`dialog dialog--${width}`}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog element itself.
        if (event.target === ref.current) onClose()
      }}
    >
      <div className="dialog__frame">
        <header className="dialog__header">
          <h2 id={titleId} className="dialog__title">
            {title}
          </h2>
          <Button variant="quiet" size="sm" icon={<X size={14} />} aria-label="Close" onClick={onClose} />
        </header>
        <div className="dialog__body">{children}</div>
        {actions ? <footer className="dialog__actions">{actions}</footer> : null}
      </div>
    </dialog>
  )
}
