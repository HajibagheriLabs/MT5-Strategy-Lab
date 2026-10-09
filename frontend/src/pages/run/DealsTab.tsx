import { useMemo, useState } from 'react'
import type { Resource } from '../../api/hooks'
import type { Deal } from '../../api/types'
import { Button } from '../../ui/Button'
import { TextInput } from '../../ui/Field'
import { Figure } from '../../ui/Figure'
import { formatDateTime, formatNumber } from '../../ui/format'
import { Notice } from '../../ui/Notice'
import { SegmentedControl } from '../../ui/SegmentedControl'
import { Skeleton } from '../../ui/Skeleton'
import type { Column } from '../../ui/Table'
import { sortRows } from '../../ui/sort'
import type { Sort } from '../../ui/sort'
import { VirtualTable } from '../../ui/VirtualTable'

type Show = 'all' | 'in' | 'out' | 'won' | 'lost'

function result(deal: Deal) {
  return deal.profit + deal.swap + deal.commission
}

type Props = {
  deals: Resource<{ deals: Deal[]; total: number }>
  digits: number
  currency: string
  selected: number | null
  onSelect: (ticket: number) => void
}

export function DealsTab({ deals, digits, currency, selected, onSelect }: Props) {
  const [show, setShow] = useState<Show>('all')
  const [text, setText] = useState('')
  const [sort, setSort] = useState<Sort | null>({ key: 'time', direction: 'asc' })

  const columns: Column<Deal>[] = useMemo(
    () => [
      { key: 'time', header: 'Time (server)', sortValue: (d) => d.server_time, render: (d) => <span className="num">{formatDateTime(d.server_time)}</span>, width: '11.5rem' },
      { key: 'ticket', header: 'Deal', numeric: true, sortValue: (d) => d.ticket, render: (d) => d.ticket },
      { key: 'type', header: 'Type', sortValue: (d) => d.type, render: (d) => d.type },
      { key: 'entry', header: 'Direction', sortValue: (d) => d.entry ?? '', render: (d) => d.entry ?? '' },
      { key: 'volume', header: 'Volume', numeric: true, sortValue: (d) => d.volume, render: (d) => (d.volume === null ? '' : formatNumber(d.volume, 2)) },
      { key: 'price', header: 'Price', numeric: true, sortValue: (d) => d.price, render: (d) => (d.price === null ? '' : formatNumber(d.price, digits)) },
      { key: 'commission', header: 'Commission', numeric: true, sortValue: (d) => d.commission, render: (d) => formatNumber(d.commission, 2) },
      { key: 'swap', header: 'Swap', numeric: true, sortValue: (d) => d.swap, render: (d) => formatNumber(d.swap, 2) },
      { key: 'profit', header: 'Profit', numeric: true, sortValue: (d) => d.profit, render: (d) => (d.type === 'balance' ? formatNumber(d.profit, 2) : <Figure kind="money" value={d.entry === 'in' ? null : result(d)} />) },
      { key: 'balance', header: 'Balance', numeric: true, sortValue: (d) => d.balance, render: (d) => (d.balance === null ? '' : formatNumber(d.balance, 2)) },
      { key: 'comment', header: 'Comment', sortValue: (d) => d.comment, render: (d) => d.comment, title: (d) => d.comment },
    ],
    [digits],
  )

  const rows = useMemo(() => {
    const all = deals.data?.deals ?? []
    const needle = text.trim().toLowerCase()
    const kept = all.filter((d) => {
      if (show === 'in' && d.entry !== 'in') return false
      if (show === 'out' && !(d.entry === 'out' || d.entry === 'in/out' || d.entry === 'out by')) return false
      if (show === 'won' && !(d.entry !== 'in' && d.type !== 'balance' && result(d) > 0)) return false
      if (show === 'lost' && !(d.entry !== 'in' && d.type !== 'balance' && result(d) < 0)) return false
      if (needle && !`${d.ticket} ${d.type} ${d.entry ?? ''} ${d.comment} ${d.server_time}`.toLowerCase().includes(needle)) return false
      return true
    })
    return sortRows(kept, columns, sort)
  }, [deals.data, show, text, sort, columns])

  if (deals.error) {
    return (
      <Notice tone="danger" title="The deals could not be loaded" action={<Button size="sm" onClick={deals.reload}>Try again</Button>}>
        {deals.error.message}
      </Notice>
    )
  }
  if (!deals.data) return <Skeleton variant="block" height="20rem" />

  return (
    <div className="deals-tab">
      <div className="deals-tab__tools">
        <SegmentedControl<Show>
          label="Show"
          hideLabel
          size="sm"
          value={show}
          onChange={setShow}
          options={[
            { value: 'all', label: 'All' },
            { value: 'in', label: 'Entries' },
            { value: 'out', label: 'Exits' },
            { value: 'won', label: 'Won' },
            { value: 'lost', label: 'Lost' },
          ]}
        />
        <TextInput label="Find deals" hideLabel placeholder="Deal, type or comment" value={text} onChange={(e) => setText(e.target.value)} />
        <span className="faint num deals-tab__count">
          {rows.length === deals.data.total ? `${rows.length} deals` : `${rows.length} of ${deals.data.total} deals`}, money in {currency}
        </span>
      </div>
      <VirtualTable
        caption="Deals"
        columns={columns}
        rows={rows}
        rowKey={(d) => String(d.ticket)}
        sort={sort}
        onSort={setSort}
        selectedKey={selected === null ? null : String(selected)}
        onRowSelect={(d) => onSelect(d.ticket)}
        height="26rem"
        empty={<p className="muted">No deal matches. Clear the filter to see them all.</p>}
      />
    </div>
  )
}
