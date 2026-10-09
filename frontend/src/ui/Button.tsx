import { CircleNotch } from '@phosphor-icons/react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import './Button.css'

export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger'

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  icon?: ReactNode
  /** Shows a working state; the button keeps its width and cannot be pressed again. */
  busy?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  busy = false,
  disabled,
  children,
  className,
  type = 'button',
  ...rest
}: Props) {
  const iconOnly = children === undefined || children === null
  const classes = [
    'button',
    `button--${variant}`,
    `button--${size}`,
    iconOnly ? 'button--icon' : '',
    busy ? 'button--busy' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? (
        <CircleNotch className="button__spinner" size={size === 'sm' ? 14 : 16} aria-hidden />
      ) : (
        icon
      )}
      {iconOnly ? null : <span className="button__label">{children}</span>}
    </button>
  )
}
