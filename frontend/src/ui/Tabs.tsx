import { useId, useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import './Tabs.css'

export type Tab<T extends string> = { value: T; label: ReactNode; count?: number }

type Props<T extends string> = {
  label: string
  tabs: Tab<T>[]
  value: T
  onChange: (value: T) => void
  children: ReactNode
}

/** WAI-ARIA tabs: arrow keys, Home and End move between tabs, and the panel follows. */
export function Tabs<T extends string>({ label, tabs, value, onChange, children }: Props<T>) {
  const id = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const current = Math.max(0, tabs.findIndex((tab) => tab.value === value))

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const last = tabs.length - 1
    const next =
      event.key === 'ArrowRight'
        ? current === last ? 0 : current + 1
        : event.key === 'ArrowLeft'
          ? current === 0 ? last : current - 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null
    if (next === null) return
    event.preventDefault()
    onChange(tabs[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label={label} onKeyDown={move}>
        {tabs.map((tab, index) => {
          const selected = index === current
          return (
            <button
              key={tab.value}
              ref={(node) => {
                refs.current[index] = node
              }}
              type="button"
              role="tab"
              id={`${id}-tab-${tab.value}`}
              aria-selected={selected}
              aria-controls={`${id}-panel`}
              tabIndex={selected ? 0 : -1}
              className={`tabs__tab ${selected ? 'tabs__tab--on' : ''}`}
              onClick={() => onChange(tab.value)}
            >
              {tab.label}
              {tab.count !== undefined ? <span className="tabs__count num">{tab.count}</span> : null}
            </button>
          )
        })}
      </div>
      <div
        className="tabs__panel"
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-tab-${tabs[current]?.value}`}
        tabIndex={0}
      >
        {children}
      </div>
    </div>
  )
}
