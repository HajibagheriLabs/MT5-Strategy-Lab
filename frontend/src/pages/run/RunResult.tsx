import { ArrowSquareOut, ArrowsClockwise, Scales, Trash } from '@phosphor-icons/react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, ApiError } from '../../api/client'
import { useResource } from '../../api/hooks'
import type { Deal, ResultSummary, RunDetail, Series, Symbols } from '../../api/types'
import { EquityChart } from '../../charts/EquityChart'
import { PriceChart } from '../../charts/PriceChart'
import { describeSettings, fidelityBody, formatMetric, HEADLINE, NEXT_STEP } from '../../runs/catalog'
import { Button } from '../../ui/Button'
import { Dialog } from '../../ui/Dialog'
import { EngineBadge } from '../../ui/EngineBadge'
import { formatDateTime, formatDuration } from '../../ui/format'
import { Notice } from '../../ui/Notice'
import { PageHeader, Section } from '../../ui/PageHeader'
import { Skeleton } from '../../ui/Skeleton'
import { StatusBadge } from '../../ui/StatusBadge'
import { Tabs } from '../../ui/Tabs'
import { useToast } from '../../ui/toast-context'
import { DealsTab } from './DealsTab'
import { LogTab } from './LogTab'
import { MetricsTab } from './MetricsTab'

type TabName = 'deals' | 'metrics' | 'log' | 'inputs' | 'report'

function Headline({ metrics }: { metrics: Record<string, number | null> }) {
  return (
    <dl className="headline">
      {HEADLINE.map((info) => {
        const value = metrics[info.key]
        const tone = info.kind === 'money' && value ? (value > 0 ? 'headline__value--profit' : 'headline__value--loss') : ''
        return (
          <div key={info.key} className="headline__item">
            <dt className="headline__label">{info.label}</dt>
            <dd className={`headline__value num ${tone}`}>
              {formatMetric(info, value)}
              {info.key === 'balance_dd_maximal' && metrics.balance_dd_maximal_pct !== undefined && metrics.balance_dd_maximal_pct !== null ? (
                <span className="headline__sub">{metrics.balance_dd_maximal_pct.toFixed(2)}%</span>
              ) : null}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

export function RunResult({ detail, onChanged }: { detail: RunDetail; onChanged: () => void }) {
  const { run } = detail
  const navigate = useNavigate()
  const toast = useToast()
  const done = run.status === 'done'
  const tester = run.engine === 'mt5_tester'
  const summary = useResource<ResultSummary>(done ? `/runs/${encodeURIComponent(run.id)}/result` : null)
  const series = useResource<Series>(done ? `/runs/${encodeURIComponent(run.id)}/equity?max_points=2000` : null)
  const deals = useResource<{ deals: Deal[]; total: number }>(done ? `/runs/${encodeURIComponent(run.id)}/deals` : null)
  const symbols = useResource<Symbols>('/symbols')
  const [tab, setTab] = useState<TabName>(done ? 'deals' : 'log')
  const [selected, setSelected] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const digits = symbols.data?.symbols.find((s) => s.name === run.settings.symbol)?.digits ?? 5
  const zeroTrades = run.outcome === 'zero_trades'
  const nextStep = run.outcome ? NEXT_STEP[run.outcome] : undefined
  const total = Object.values(run.stage_seconds).reduce((sum, value) => sum + value, 0)

  const remove = async () => {
    setDeleting(true)
    try {
      await api.delete(`/runs/${encodeURIComponent(run.id)}`)
      toast.push({ title: 'Run deleted' })
      navigate('/runs')
    } catch (reason) {
      setDeleting(false)
      setConfirmDelete(false)
      toast.push({ tone: 'error', title: 'The run was not deleted', message: reason instanceof ApiError ? reason.message : String(reason) })
      onChanged()
    }
  }

  const tabs: { value: TabName; label: string; count?: number }[] = [
    ...(done ? [{ value: 'deals' as const, label: 'Deals', count: deals.data?.total }, { value: 'metrics' as const, label: 'Metrics' }] : []),
    { value: 'log', label: 'Log' },
    { value: 'inputs', label: 'Settings' },
    ...(tester && run.artefacts.report ? [{ value: 'report' as const, label: 'MetaTrader report' }] : []),
  ]

  return (
    <div className="run">
      <PageHeader
        context={
          <>
            <Link to="/runs">Runs</Link> <span className="num faint">{run.id}</span>
          </>
        }
        title={run.strategy_name}
        description={describeSettings(run.settings)}
        actions={
          <>
            <Button variant="primary" icon={<ArrowsClockwise size={16} />} onClick={() => navigate(`/new?from=${run.id}`)}>
              Run again with changes
            </Button>
            {done ? (
              <Button icon={<Scales size={16} />} onClick={() => navigate(`/compare?runs=${run.id}`)}>
                Compare
              </Button>
            ) : null}
            <Button variant="quiet" icon={<Trash size={16} />} aria-label="Delete run" onClick={() => setConfirmDelete(true)} />
          </>
        }
      />
      <div className="run__badges">
        <EngineBadge engine={run.engine} />
        <StatusBadge status={run.status} />
        <span className="faint num run__timing">
          {run.finished_at ? `Finished ${formatDateTime(run.finished_at, false)}` : ''}
          {total ? `, took ${formatDuration(total)}` : ''}
        </span>
        {run.rerun_of ? (
          <span className="faint">
            Rerun of <Link to={`/runs/${run.rerun_of}`} className="num">{run.rerun_of}</Link>
          </span>
        ) : null}
      </div>

      {run.status === 'failed' ? (
        <Notice tone="danger" title="The run failed">
          <p>{run.message}</p>
          {nextStep ? <p>{nextStep}</p> : null}
        </Notice>
      ) : run.status === 'cancelled' ? (
        <Notice title="The run was cancelled">
          <p>{run.message}</p>
        </Notice>
      ) : zeroTrades ? (
        <Notice tone="warning" title="The test ran but made no trades">
          <p>{nextStep}</p>
        </Notice>
      ) : null}

      {done && run.fidelity ? (
        tester ? (
          <p className="run__fidelity">{run.fidelity}</p>
        ) : (
          <Notice tone="warning" title="Simulated by StrategyLab, not the Strategy Tester">
            {fidelityBody(run.fidelity)}
          </Notice>
        )
      ) : null}

      {done ? (
        <>
          <Section title="Result">
            <Headline metrics={run.metrics} />
          </Section>
          <Section title="Balance and drawdown">
            {series.error ? (
              <Notice tone="danger" title="The balance series could not be loaded">{series.error.message}</Notice>
            ) : series.data ? (
              series.data.points.length ? (
                <EquityChart points={series.data.points} label={`Balance of run ${run.id}`} />
              ) : (
                <p className="muted">No balance changes to draw.</p>
              )
            ) : (
              <div style={{ height: 320 }}>
                <Skeleton variant="block" />
              </div>
            )}
            {series.data?.downsampled ? (
              <p className="faint run__note">
                Drawn from {series.data.points.length} of {series.data.total} points; every peak and the deepest drawdown are kept.
              </p>
            ) : null}
          </Section>
          <Section title="Price">
            {deals.data ? (
              <PriceChart runId={run.id} deals={deals.data.deals} digits={digits} selected={selected} onSelect={setSelected} />
            ) : (
              <div style={{ height: 360 }}>
                <Skeleton variant="block" />
              </div>
            )}
            <p className="faint run__note">Arrows mark entries; circles mark exits, coloured by the trade's result. Choose a deal below to move the chart to it.</p>
          </Section>
        </>
      ) : null}

      <Section>
        <Tabs label="Run details" tabs={tabs} value={tab} onChange={setTab}>
          {tab === 'deals' ? (
            <DealsTab deals={deals} digits={digits} selected={selected} onSelect={setSelected} currency={run.settings.currency} />
          ) : tab === 'metrics' ? (
            <MetricsTab metrics={run.metrics} reported={summary.data?.reported ?? {}} tester={tester} />
          ) : tab === 'log' ? (
            <LogTab runId={run.id} />
          ) : tab === 'inputs' ? (
            <div className="settings-tab">
              <dl className="settings-list">
                <dt>Symbol</dt>
                <dd className="num">{run.settings.symbol}</dd>
                <dt>Timeframe</dt>
                <dd className="num">{run.settings.timeframe}</dd>
                <dt>Period</dt>
                <dd className="num">{run.settings.date_from} to {run.settings.date_to} (end not included)</dd>
                <dt>Deposit</dt>
                <dd className="num">{run.settings.deposit} {run.settings.currency}, leverage 1:{run.settings.leverage}</dd>
                {summary.data?.meta.server ? (
                  <>
                    <dt>Server</dt>
                    <dd className="num">{summary.data.meta.server}</dd>
                  </>
                ) : null}
                {summary.data?.meta.server_utc_offsets_h.length ? (
                  <>
                    <dt>Server time</dt>
                    <dd className="num">UTC{summary.data.meta.server_utc_offsets_h.map((h) => (h >= 0 ? `+${h}` : `${h}`)).join(' and UTC')}</dd>
                  </>
                ) : null}
                {summary.data?.meta.terminal_build ? (
                  <>
                    <dt>Terminal build</dt>
                    <dd className="num">{summary.data.meta.terminal_build}</dd>
                  </>
                ) : null}
              </dl>
              <h3 className="settings-tab__title">Inputs changed from their defaults</h3>
              {Object.keys(run.settings.parameters).length ? (
                <dl className="settings-list">
                  {Object.entries(run.settings.parameters).map(([name, value]) => (
                    <div key={name} className="settings-list__pair">
                      <dt className="num">{name}</dt>
                      <dd className="num">{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="muted">None: the strategy ran with its defaults.</p>
              )}
              {summary.data?.meta.notes.length ? (
                <>
                  <h3 className="settings-tab__title">Notes from the engine</h3>
                  <ul className="settings-tab__notes">
                    {summary.data.meta.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                </>
              ) : null}
            </div>
          ) : (
            <div className="report-tab">
              <p className="faint">
                The report exactly as the Strategy Tester wrote it.{' '}
                <a href={`/api/runs/${encodeURIComponent(run.id)}/report/`} target="_blank" rel="noreferrer">
                  Open it on its own <ArrowSquareOut size={12} aria-hidden />
                </a>
              </p>
              <iframe
                className="report-tab__frame"
                title="MetaTrader report"
                src={`/api/runs/${encodeURIComponent(run.id)}/report/`}
                sandbox=""
              />
            </div>
          )}
        </Tabs>
      </Section>

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this run?"
        width="sm"
        actions={
          <>
            <Button onClick={() => setConfirmDelete(false)}>Keep it</Button>
            <Button variant="danger" busy={deleting} onClick={remove}>
              Delete run
            </Button>
          </>
        }
      >
        Its deals, report and logs are removed from this machine. The strategy stays.
      </Dialog>
    </div>
  )
}
