import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { TextInput } from './Field'
import './LogView.css'

export type LogLine = { source?: string; line: string }

type Props = {
  lines: LogLine[]
  height?: string
  /** Keep the newest line in view while lines arrive, until the reader scrolls up. */
  follow?: boolean
  empty?: ReactNode
  label: string
}

const LINE_PX = 18

/** MetaTrader journals carry a code, a level and a time before the message; the level marks
 * warnings (2) and errors (3). */
function level(line: string): 'error' | 'warning' | null {
  const match = /^\S+\t([0-3])\t/.exec(line)
  if (match?.[1] === '3') return 'error'
  if (match?.[1] === '2') return 'warning'
  if (/Traceback|Error:|error /.test(line)) return 'error'
  return null
}

export function LogView({ lines, height = '24rem', follow = false, empty, label }: Props) {
  const frame = useRef<HTMLDivElement>(null)
  const [filter, setFilter] = useState('')
  const [pinned, setPinned] = useState(true)
  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return needle ? lines.filter((l) => l.line.toLowerCase().includes(needle)) : lines
  }, [lines, filter])
  // The virtualiser keeps its own state between renders, which the React compiler cannot
  // follow; nothing here relies on the compiler.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: shown.length,
    getScrollElement: () => frame.current,
    estimateSize: () => LINE_PX,
    overscan: 30,
  })

  useEffect(() => {
    if (follow && pinned && shown.length) virtualizer.scrollToIndex(shown.length - 1, { align: 'end' })
  }, [follow, pinned, shown.length, virtualizer])

  return (
    <div className="log">
      <div className="log__tools">
        <TextInput label="Filter lines" hideLabel placeholder="Filter lines" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="faint num log__count">
          {filter ? `${shown.length} of ${lines.length}` : lines.length} lines
        </span>
      </div>
      <div
        ref={frame}
        className="log__frame"
        style={{ height }}
        role="log"
        aria-label={label}
        tabIndex={0}
        onScroll={(event) => {
          const el = event.currentTarget
          setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < LINE_PX * 2)
        }}
      >
        {shown.length === 0 ? (
          <div className="log__empty">{empty ?? 'Nothing logged yet.'}</div>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
            {virtualizer.getVirtualItems().map((item) => {
              const entry = shown[item.index]
              const tone = level(entry.line)
              return (
                <div
                  key={item.key}
                  className={`log__line ${tone ? `log__line--${tone}` : ''}`}
                  style={{ transform: `translateY(${item.start}px)` }}
                >
                  {entry.source ? <span className="log__source">{entry.source}</span> : null}
                  <span className="log__text">{entry.line.replace(/\t/g, '  ')}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
