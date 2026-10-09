import { CaretDown, CaretUp } from '@phosphor-icons/react'
import type { KeyboardEvent, ReactNode } from 'react'
import { Skeleton } from './Skeleton'
import type { Sort } from './sort'
import './Table.css'

export type { Sort, SortDirection } from './sort'

export type Column<Row> = {
  key: string
  header: ReactNode
  /** Cell content; defaults to the row's field of the same key. */
  render?: (row: Row) => ReactNode
  /** Value to sort by; a column without one cannot be sorted. */
  sortValue?: (row: Row) => string | number | null | undefined
  align?: 'left' | 'right' | 'center'
  /** Numbers: monospace, tabular, right-aligned. */
  numeric?: boolean
  width?: string
  /** Full text shown on hover when the cell is truncated. */
  title?: (row: Row) => string
}

type Props<Row> = {
  columns: Column<Row>[]
  rows: Row[]
  rowKey: (row: Row) => string
  caption?: string
  sort?: Sort | null
  onSort?: (sort: Sort) => void
  selectedKeys?: ReadonlySet<string>
  /** Called when a row is chosen by click, Enter or Space. */
  onRowSelect?: (row: Row) => void
  loading?: boolean
  empty?: ReactNode
  density?: 'compact' | 'normal'
  /** Height of the scroll frame; the header stays in view inside it. */
  maxHeight?: string
}

export function Table<Row>({
  columns,
  rows,
  rowKey,
  caption,
  sort,
  onSort,
  selectedKeys,
  onRowSelect,
  loading,
  empty,
  density = 'normal',
  maxHeight,
}: Props<Row>) {
  const align = (column: Column<Row>) => column.align ?? (column.numeric ? 'right' : 'left')

  const header = (column: Column<Row>) => {
    const active = sort?.key === column.key
    const ariaSort = active ? (sort?.direction === 'asc' ? 'ascending' : 'descending') : undefined
    const content =
      column.sortValue && onSort ? (
        <button
          type="button"
          className="table__sort"
          onClick={() =>
            onSort({
              key: column.key,
              direction: active && sort?.direction === 'desc' ? 'asc' : 'desc',
            })
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
      )
    return (
      <th
        key={column.key}
        scope="col"
        aria-sort={ariaSort}
        className={`table__th table__cell--${align(column)}`}
        style={column.width ? { width: column.width } : undefined}
      >
        {content}
      </th>
    )
  }

  const onKey = (event: KeyboardEvent<HTMLTableRowElement>, row: Row) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onRowSelect?.(row)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const sibling =
        event.key === 'ArrowDown'
          ? event.currentTarget.nextElementSibling
          : event.currentTarget.previousElementSibling
      if (sibling instanceof HTMLElement) sibling.focus()
    }
  }

  return (
    <div className={`table-frame table--${density}`} style={maxHeight ? { maxHeight } : undefined}>
      <table className="table">
        {caption ? <caption className="visually-hidden">{caption}</caption> : null}
        <thead>
          <tr>{columns.map(header)}</tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 6 }, (_, index) => (
                <tr key={`loading-${index}`} className="table__row" aria-hidden>
                  {columns.map((column) => (
                    <td key={column.key} className={`table__td table__cell--${align(column)}`}>
                      <Skeleton width={column.numeric ? '4rem' : `${50 + ((index * 17) % 40)}%`} />
                    </td>
                  ))}
                </tr>
              ))
            : rows.map((row) => {
                const key = rowKey(row)
                const selected = selectedKeys?.has(key) ?? false
                return (
                  <tr
                    key={key}
                    className={`table__row ${onRowSelect ? 'table__row--interactive' : ''} ${selected ? 'table__row--selected' : ''}`}
                    tabIndex={onRowSelect ? 0 : undefined}
                    aria-selected={onRowSelect ? selected : undefined}
                    onClick={onRowSelect ? () => onRowSelect(row) : undefined}
                    onKeyDown={onRowSelect ? (event) => onKey(event, row) : undefined}
                  >
                    {columns.map((column) => {
                      const content = column.render
                        ? column.render(row)
                        : String((row as Record<string, unknown>)[column.key] ?? '')
                      return (
                        <td
                          key={column.key}
                          className={`table__td table__cell--${align(column)} ${column.numeric ? 'num' : ''}`}
                          title={column.title?.(row)}
                        >
                          {content}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
        </tbody>
      </table>
      {!loading && rows.length === 0 && empty ? <div className="table__empty">{empty}</div> : null}
    </div>
  )
}
