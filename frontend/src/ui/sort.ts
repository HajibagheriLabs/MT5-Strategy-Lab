export type SortDirection = 'asc' | 'desc'
export type Sort = { key: string; direction: SortDirection }

type Sortable<Row> = { key: string; sortValue?: (row: Row) => string | number | null | undefined }

/** Rows in the order of `sort`; values that are missing go last either way. */
export function sortRows<Row>(rows: Row[], columns: Sortable<Row>[], sort: Sort | null | undefined): Row[] {
  const column = sort ? columns.find((c) => c.key === sort.key) : undefined
  if (!sort || !column?.sortValue) return rows
  const value = column.sortValue
  const factor = sort.direction === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = value(a)
    const y = value(b)
    if (x === y) return 0
    if (x === null || x === undefined) return 1
    if (y === null || y === undefined) return -1
    return (x < y ? -1 : 1) * factor
  })
}
