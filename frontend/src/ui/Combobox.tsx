import { CaretDown, WarningCircle } from '@phosphor-icons/react'
import { useId, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import './Combobox.css'

export type ComboOption = {
  value: string
  label: string
  /** A second line: what it is, what range it covers. */
  detail?: ReactNode
  /** Extra text the search matches (a description). */
  keywords?: string
}

type Props = {
  label: ReactNode
  options: ComboOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  help?: ReactNode
  error?: ReactNode
  empty?: ReactNode
  disabled?: boolean
}

/** A text box that filters a list (WAI-ARIA combobox): type to narrow, arrows to move, Enter to
 * choose, Escape to close. Only listed values can be chosen. */
export function Combobox({ label, options, value, onChange, placeholder, help, error, empty, disabled }: Props) {
  const id = useId()
  const listId = `${id}-list`
  const noteId = `${id}-note`
  const [query, setQuery] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const chosen = options.find((option) => option.value === value)
  const text = query ?? chosen?.label ?? ''

  const shown = useMemo(() => {
    const needle = (query ?? '').trim().toLowerCase()
    if (!needle) return options
    return options.filter((o) => `${o.label} ${o.keywords ?? ''}`.toLowerCase().includes(needle))
  }, [options, query])

  const choose = (option: ComboOption) => {
    onChange(option.value)
    setQuery(null)
    setOpen(false)
  }

  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive((index) => Math.min(shown.length - 1, open ? index + 1 : 0))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => Math.max(0, index - 1))
    } else if (event.key === 'Enter' && open && shown[active]) {
      event.preventDefault()
      choose(shown[active])
    } else if (event.key === 'Escape') {
      setOpen(false)
      setQuery(null)
    }
  }

  const note = error ?? help
  return (
    <div className="field combobox">
      <label htmlFor={id} className="field__label">
        {label}
      </label>
      <div className={`control ${error ? 'control--invalid' : ''} ${disabled ? 'control--disabled' : ''}`}>
        <input
          ref={input}
          id={id}
          className="control__input"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && shown[active] ? `${id}-option-${active}` : undefined}
          aria-describedby={note ? noteId : undefined}
          aria-invalid={Boolean(error) || undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder={placeholder}
          disabled={disabled}
          value={text}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(0)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onBlur={() => {
            setOpen(false)
            setQuery(null)
          }}
          onKeyDown={onKey}
        />
        <CaretDown className="control__caret" size={14} aria-hidden />
      </div>
      {open ? (
        <ul id={listId} role="listbox" className="combobox__list" aria-label={typeof label === 'string' ? label : undefined}>
          {shown.length === 0 ? (
            <li className="combobox__empty">{empty ?? 'Nothing matches.'}</li>
          ) : (
            shown.map((option, index) => (
              <li
                key={option.value}
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={option.value === value}
                className={`combobox__option ${index === active ? 'combobox__option--active' : ''}`}
                // Choose on mouse down, before the input's blur closes the list.
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(option)
                }}
                onMouseEnter={() => setActive(index)}
              >
                <span className="combobox__label">{option.label}</span>
                {option.detail ? <span className="combobox__detail">{option.detail}</span> : null}
              </li>
            ))
          )}
        </ul>
      ) : null}
      {note ? (
        <p id={noteId} className={error ? 'field__error' : 'field__help'} role={error ? 'alert' : undefined}>
          {error ? <WarningCircle size={14} aria-hidden /> : null}
          <span>{note}</span>
        </p>
      ) : null}
    </div>
  )
}
