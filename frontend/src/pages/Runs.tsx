import { Play, Scales, Trash } from '@phosphor-icons/react'
import { useCallback, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { api } from '../api/client'
import { useAllEvents } from '../api/events'
import { useResource } from '../api/hooks'
import { FINISHED } from '../api/types'
import type { Engine, Run, RunStatus } from '../api/types'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Dialog } from '../ui/Dialog'
import { EngineBadge } from '../ui/EngineBadge'
import { Select, TextInput } from '../ui/Field'
import { Figure } from '../ui/Figure'
import { formatDateTime } from '../ui/format'
import { EmptyState, Notice } from '../ui/Notice'
import { PageHeader } from '../ui/PageHeader'
import { StatusBadge } from '../ui/StatusBadge'
import { Table } from '../ui/Table'
import type { Column } from '../ui/Table'
import { sortRows } from '../ui/sort'
import type { Sort } from '../ui/sort'
import { useToast } from '../ui/toast-context'
import './Runs.css'

const STATUSES: RunStatus[] = ['queued', 'compiling', 'running', 'parsing', 'done', 'failed', 'cancelled']

export function Runs() {
  const navigate = useNavigate()
  const toast = useToast()
  const [query, setQuery] = useSearchParams()
  const status = query.get('status') ?? ''
  const engine = query.get('engine') ?? ''
  const text = query.get('q') ?? ''
  const { data, error, loading, reload } = useResource<Run[]>('/runs?limit=2000')
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [sort, setSort] = useState<Sort | null>({ key: 'created', direction: 'desc' })
  const [confirm, setConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useAllEvents(
    useCallback(
      (event) => {
        if (event.kind === 'state' && !event.snapshot) reload()
      },
      [reload],
    ),
  )

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(query)
    if (value) next.set(key, value)
    else next.delete(key)
    setQuery(next, { replace: true })
  }

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const columns: Column<Run>[] = [
    {
      key: 'pick',
      header: '',
      width: '2.5rem',
      render: (r) => (
        <span onClick={(event) => event.stopPropagation()}>
          <Checkbox label={`Select run ${r.id}`} hideLabel checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
        </span>
      ),
    },
    { key: 'status', header: 'Status', sortValue: (r) => STATUSES.indexOf(r.status), render: (r) => <StatusBadge status={r.status} />, width: '9.5rem' },
    { key: 'engine', header: 'Engine', sortValue: (r) => r.engine, render: (r) => <EngineBadge engine={r.engine} size="sm" />, width: '10rem' },
    { key: 'strategy', header: 'Strategy', sortValue: (r) => r.strategy_name, render: (r) => r.strategy_name, title: (r) => r.strategy_name },
    { key: 'market', header: 'Market', sortValue: (r) => `${r.settings.symbol} ${r.settings.timeframe}`, render: (r) => <span className="num">{`${r.settings.symbol} ${r.settings.timeframe}`}</span> },
    { key: 'period', header: 'Period', sortValue: (r) => r.settings.date_from, render: (r) => <span className="num">{`${r.settings.date_from} to ${r.settings.date_to}`}</span> },
    { key: 'trades', header: 'Trades', numeric: true, sortValue: (r) => r.trade_count, render: (r) => r.trade_count ?? '' },
    { key: 'net', header: 'Net profit', numeric: true, sortValue: (r) => r.metrics.net_profit, render: (r) => (r.status === 'done' ? <Figure kind="money" value={r.metrics.net_profit} /> : '') },
    { key: 'pf', header: 'Profit factor', numeric: true, sortValue: (r) => r.metrics.profit_factor, render: (r) => (r.status === 'done' ? <Figure value={r.metrics.profit_factor} /> : '') },
    { key: 'dd', header: 'Max DD %', numeric: true, sortValue: (r) => r.metrics.balance_dd_maximal_pct, render: (r) => (r.status === 'done' ? <Figure value={r.metrics.balance_dd_maximal_pct} /> : '') },
    { key: 'created', header: 'Started', sortValue: (r) => r.created_at, render: (r) => <span className="num faint">{formatDateTime(r.created_at, false)}</span> },
  ]

  const rows = useMemo(() => {
    const needle = text.trim().toLowerCase()
    const kept = (data ?? []).filter(
      (r) =>
        (!status || r.status === status) &&
        (!engine || r.engine === engine) &&
        (!needle || `${r.strategy_name} ${r.settings.symbol} ${r.settings.timeframe} ${r.id}`.toLowerCase().includes(needle)),
    )
    return sortRows(kept, columns, sort)
    // Columns are rebuilt every render for their checkboxes; sorting depends only on these.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, status, engine, text, sort])

  const picked = (data ?? []).filter((r) => selected.has(r.id))
  const comparable = picked.filter((r) => r.status === 'done')
  const deletable = picked.filter((r) => FINISHED.has(r.status))
  const allShown = rows.length > 0 && rows.every((r) => selected.has(r.id))

  const removeSelected = async () => {
    setDeleting(true)
    const results = await Promise.allSettled(deletable.map((r) => api.delete(`/runs/${encodeURIComponent(r.id)}`)))
    const failed = results.filter((r) => r.status === 'rejected').length
    setDeleting(false)
    setConfirm(false)
    setSelected(new Set())
    reload()
    toast.push(
      failed
        ? { tone: 'error', title: `${failed} of ${deletable.length} runs were not deleted`, message: 'Runs still in progress have to be cancelled first.' }
        : { tone: 'done', title: `${deletable.length} ${deletable.length === 1 ? 'run' : 'runs'} deleted` },
    )
  }

  const filtered = Boolean(status || engine || text)

  return (
    <div className="runs">
      <PageHeader
        title="Runs"
        description="Every backtest, newest first. Choose two to four finished runs to compare them."
        actions={
          <Button variant="primary" icon={<Play size={16} />} onClick={() => navigate('/new')}>
            New run
          </Button>
        }
      />
      <div className="runs__tools">
        <TextInput label="Find" hideLabel placeholder="Strategy, symbol or run id" value={text} onChange={(e) => setFilter('q', e.target.value)} />
        <Select
          label="Status"
          hideLabel
          value={status}
          onChange={(e) => setFilter('status', e.target.value)}
          options={[{ value: '', label: 'Any status' }, ...STATUSES.map((s) => ({ value: s, label: s[0].toUpperCase() + s.slice(1) }))]}
        />
        <Select
          label="Engine"
          hideLabel
          value={engine}
          onChange={(e) => setFilter('engine', e.target.value as Engine | '')}
          options={[
            { value: '', label: 'Any engine' },
            { value: 'mt5_tester', label: 'Strategy Tester' },
            { value: 'python_sim', label: 'Python simulator' },
          ]}
        />
        {filtered ? (
          <Button variant="quiet" size="sm" onClick={() => setQuery(new URLSearchParams(), { replace: true })}>
            Clear filters
          </Button>
        ) : null}
        <div className="runs__selection">
          <Checkbox
            label={allShown ? 'Clear selection' : 'Select all shown'}
            checked={allShown}
            indeterminate={!allShown && rows.some((r) => selected.has(r.id))}
            onChange={() => setSelected(allShown ? new Set() : new Set(rows.map((r) => r.id)))}
          />
          <span className="faint num">{selected.size ? `${selected.size} selected` : ''}</span>
          <Button
            size="sm"
            icon={<Scales size={14} />}
            disabled={comparable.length < 2 || comparable.length > 4}
            title={comparable.length > 4 ? 'Compare at most four runs' : 'Choose two to four finished runs'}
            onClick={() => navigate(`/compare?runs=${comparable.map((r) => r.id).join(',')}`)}
          >
            Compare
          </Button>
          <Button size="sm" variant="danger" icon={<Trash size={14} />} disabled={!deletable.length} onClick={() => setConfirm(true)}>
            Delete
          </Button>
        </div>
      </div>

      {error ? (
        <Notice tone="danger" title="Runs could not be loaded" action={<Button size="sm" onClick={reload}>Try again</Button>}>
          {error.message}
        </Notice>
      ) : (
        <Table
          caption="Runs"
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          sort={sort}
          onSort={setSort}
          selectedKeys={selected}
          onRowSelect={(r) => navigate(`/runs/${r.id}`)}
          loading={loading}
          maxHeight="calc(100dvh - 15rem)"
          empty={
            filtered ? (
              <EmptyState title="No run matches these filters" action={<Button size="sm" onClick={() => setQuery(new URLSearchParams(), { replace: true })}>Clear filters</Button>} />
            ) : (
              <EmptyState title="No runs yet" action={<Button variant="primary" size="sm" onClick={() => navigate('/new')}>New run</Button>}>
                Start a backtest and it appears here, with its state while it runs and its figures when it is done.
              </EmptyState>
            )
          }
        />
      )}

      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={`Delete ${deletable.length} ${deletable.length === 1 ? 'run' : 'runs'}?`}
        width="sm"
        actions={
          <>
            <Button onClick={() => setConfirm(false)}>Keep them</Button>
            <Button variant="danger" busy={deleting} onClick={removeSelected}>
              Delete
            </Button>
          </>
        }
      >
        Their deals, reports and logs are removed from this machine. Strategies stay.
        {picked.length > deletable.length ? ` ${picked.length - deletable.length} selected runs are still in progress and are left alone.` : ''}
      </Dialog>
    </div>
  )
}
