import { Check, Minus } from '@phosphor-icons/react'
import { useEffect, useRef } from 'react'
import type { InputHTMLAttributes, ReactNode } from 'react'
import './Checkbox.css'

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label: ReactNode
  hideLabel?: boolean
  /** Some, not all, of what this box stands for is chosen (a header box over rows). */
  indeterminate?: boolean
}

export function Checkbox({ label, hideLabel, indeterminate = false, className, ...rest }: Props) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate
  }, [indeterminate])
  return (
    <label className={`checkbox ${className ?? ''}`}>
      <input ref={ref} type="checkbox" className="checkbox__input" {...rest} />
      <span className="checkbox__box" aria-hidden>
        {indeterminate ? <Minus size={12} weight="bold" /> : <Check size={12} weight="bold" />}
      </span>
      <span className={hideLabel ? 'visually-hidden' : 'checkbox__label'}>{label}</span>
    </label>
  )
}
