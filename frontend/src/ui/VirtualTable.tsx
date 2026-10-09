import { CaretDown, CaretUp } from '@phosphor-icons/react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import type { Column } from './Table'
import type { Sort } from './sort'
import './Table.css'

type Props<Row> = {
  columns: Column<Row>[]
  rows: Row[]
  rowKey: (row: Row) => string
  caption: string
  sort?: Sort | null
  onSort?: (sort: Sort) => void
  selectedKey?: string | null
  onRowSelect?: (row: Row) => void
  height: string
  empty?: ReactNode
}

const ROW_PX = 28

/** A table that renders only the rows in view, for tables that run to thousands of rows. The
 * header stays put, and a row chosen elsewhere (on the chart) is scrolled into view. */
export function VirtualTable<Row>({
  columns,
  rows,
  rowKey,
  caption,
  sort,
  onSort,
  selectedKey,
  onRowSelect,
  height,
  empty,
}: Props<Row>) {
  const frame = useRef<HTMLDivElement>(null)
  // The virtualiser keeps its own state between renders, which the React compiler cannot
  // follow; nothing here relies on the compiler.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => frame.current,
    estimateSize: () => ROW_PX,
    overscan: 12,
  })
  const items = virtualizer.getVirtualItems()
  const padTop = items.length ? items[0].start : 0
  const padBottom = items.length ? virtualizer.getTotalSize() - items[items.length - 1].end : 0

  useEffect(() => {
    if (!selectedKey) return
    const index = rows.findIndex((row) => rowKey(row) === selectedKey)
    if (index >= 0) virtualizer.scrollToIndex(index, { align: 'auto' })
  }, [selectedKey, rows, rowKey, virtualizer])

  const align = (column: Column<Row>) => column.align ?? (column.numeric ? 'right' : 'left')

  const onKey = (event: KeyboardEvent<HTMLTableRowElement>, index: number) => {
    const next = event.key === 'ArrowDown' ? index + 1 : event.key === 'ArrowUp' ? index - 1 : null
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onRowSelect?.(rows[index])
    } else if (next !== null && next >= 0 && next < rows.length) {
      event.preventDefault()
      onRowSelect?.(rows[next])
      virtualizer.scrollToIndex(next, { align: 'auto' })
      requestAnimationFrame(() => {
        frame.current?.querySelector<HTMLElement>(`[data-index="${next}"]`)?.focus()
      })
    }
  }

  return (
    <div ref={frame} className="table-frame table--compact" style={{ height }}>
      <table className="table" aria-rowcount={rows.length + 1}>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sort?.key === column.key
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={active ? (sort?.direction === 'asc' ? 'ascending' : 'descending') : undefined}
                  className={`table__th table__cell--${align(column)}`}
                  style={column.width ? { width: column.width } : undefined}
                >
                  {column.sortValue && onSort ? (
                    <button
                      type="button"
                      className="table__sort"
                      onClick={() =>
                        onSort({ key: column.key, direction: active && sort?.direction === 'desc' ? 'asc' : 'desc' })
                      }
                    >
                      <span>{column.header}</span>
                      {active ? (
                        sort?.direction === 'asc' ? <CaretUp size={12} aria-hidden /> : <CaretDown size={12} aria-hidden />
                      ) : (
                        <CaretDown size={12} aria-hidden className="table__sort-hint" />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {padTop > 0 ? (
            <tr aria-hidden>
              <td colSpan={columns.length} style={{ height: padTop, padding: 0, border: 0 }} />
            </tr>
          ) : null}
          {items.map((item) => {
            const row = rows[item.index]
            const key = rowKey(row)
            const selected = key === selectedKey
            return (
              <tr
                key={key}
                data-index={item.index}
                aria-rowindex={item.index + 2}
                className={`table__row ${onRowSelect ? 'table__row--interactive' : ''} ${selected ? 'table__row--selected' : ''}`}
                tabIndex={onRowSelect ? (selected || (!selectedKey && item.index === 0) ? 0 : -1) : undefined}
                aria-selected={onRowSelect ? selected : undefined}
                onClick={onRowSelect ? () => onRowSelect(row) : undefined}
                onKeyDown={onRowSelect ? (event) => onKey(event, item.index) : undefined}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`table__td table__cell--${align(column)} ${column.numeric ? 'num' : ''}`}
                    title={column.title?.(row)}
                  >
                    {column.render ? column.render(row) : String((row as Record<string, unknown>)[column.key] ?? '')}
                  </td>
                ))}
              </tr>
            )
          })}
          {padBottom > 0 ? (
            <tr aria-hidden>
              <td colSpan={columns.length} style={{ height: padBottom, padding: 0, border: 0 }} />
            </tr>
          ) : null}
        </tbody>
      </table>
      {rows.length === 0 && empty ? <div className="table__empty">{empty}</div> : null}
    </div>
  )
}
