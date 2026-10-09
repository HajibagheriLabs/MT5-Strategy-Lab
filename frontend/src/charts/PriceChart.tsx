import { CandlestickSeries, createChart, createSeriesMarkers } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, ISeriesMarkersPluginApi, SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Deal } from '../api/types'
import { useTheme } from '../theme-context'
import { Button } from '../ui/Button'
import { Notice } from '../ui/Notice'
import { Skeleton } from '../ui/Skeleton'
import { formatNumber } from '../ui/format'
import { baseOptions, chartColors, serverSeconds } from './chartTheme'
import './charts.css'

type Bar = { time: number; open: number; high: number; low: number; close: number }
type BarsAnswer = { timeframe: string; bars: Bar[]; first_index: number; total: number }

type Props = {
  runId: string
  deals: Deal[]
  digits: number
  selected: number | null
  onSelect: (ticket: number) => void
  height?: number
}

const PAGE = 600

/** Index of the last bar opening at or before `time`, so a deal lands on the bar it happened in. */
function barAt(bars: Bar[], time: number): Bar | undefined {
  let low = 0
  let high = bars.length - 1
  let found: Bar | undefined
  while (low <= high) {
    const mid = (low + high) >> 1
    if (bars[mid].time <= time) {
      found = bars[mid]
      low = mid + 1
    } else {
      high = mid - 1
    }
  }
  return found
}

export function PriceChart({ runId, deals, digits, selected, onSelect, height = 360 }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const loading = useRef(false)
  const [bars, setBars] = useState<Bar[]>([])
  const [first, setFirst] = useState(0)
  const [error, setError] = useState<ApiError | null>(null)
  const [attempt, setAttempt] = useState(0)
  const { theme } = useTheme()

  const trades = useMemo(() => deals.filter((d) => d.type === 'buy' || d.type === 'sell'), [deals])
  const selectedDeal = trades.find((d) => d.ticket === selected) ?? null
  const focus = selectedDeal ? serverSeconds(selectedDeal.server_time) : null

  // Load the window around the chosen deal, or the end of the run when nothing is chosen.
  useEffect(() => {
    let live = true
    const query = focus !== null ? `around=${focus}&count=${PAGE}` : `count=${PAGE}`
    if (focus !== null && bars.length && focus >= bars[0].time && focus <= bars[bars.length - 1].time) return
    api
      .get<BarsAnswer>(`/runs/${encodeURIComponent(runId)}/bars?${query}`)
      .then((answer) => {
        if (!live) return
        setBars(answer.bars)
        setFirst(answer.first_index)
        setError(null)
      })
      .catch((reason: unknown) => {
        if (live) setError(reason instanceof ApiError ? reason : new ApiError(0, String(reason)))
      })
    return () => {
      live = false
    }
    // Reloading only when the focus leaves what is loaded is the point; bars are read, not watched.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, focus, attempt])

  useEffect(() => {
    if (!host.current) return
    const c = chartColors()
    const created = createChart(host.current, baseOptions())
    const candles = created.addSeries(CandlestickSeries, {
      upColor: c.bg,
      downColor: c.fg3,
      borderUpColor: c.fg2,
      borderDownColor: c.fg3,
      wickUpColor: c.fg2,
      wickDownColor: c.fg3,
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: digits, minMove: 1 / 10 ** digits },
    })
    chart.current = created
    series.current = candles
    markers.current = createSeriesMarkers(candles, [])
    created.subscribeClick((param) => {
      const id = param.hoveredObjectId
      if (typeof id === 'string' && id.startsWith('deal-')) onSelect(Number(id.slice(5)))
    })
    return () => {
      created.remove()
      chart.current = null
      series.current = null
      markers.current = null
    }
    // onSelect is stable enough for a click handler; recreating the chart on each render is not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, digits])

  // Older bars arrive when the view nears the left edge of what is loaded.
  useEffect(() => {
    const created = chart.current
    if (!created || !bars.length) return
    const onRange = (range: { from: number; to: number } | null) => {
      if (!range || range.from > 20 || first === 0 || loading.current) return
      loading.current = true
      api
        .get<BarsAnswer>(`/runs/${encodeURIComponent(runId)}/bars?before=${bars[0].time}&count=${PAGE}`)
        .then((answer) => {
          if (!answer.bars.length) return
          const shift = answer.bars.length
          setBars((current) => [...answer.bars, ...current])
          setFirst(answer.first_index)
          requestAnimationFrame(() => {
            created.timeScale().setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift })
          })
        })
        .catch(() => undefined)
        .finally(() => {
          loading.current = false
        })
    }
    created.timeScale().subscribeVisibleLogicalRangeChange(onRange)
    return () => created.timeScale().unsubscribeVisibleLogicalRangeChange(onRange)
  }, [bars, first, runId, theme])

  useEffect(() => {
    const candles = series.current
    const created = chart.current
    if (!candles || !created) return
    candles.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })))
    const c = chartColors()
    const list: SeriesMarker<Time>[] = []
    const span = bars.length > 1 ? bars[bars.length - 1].time - bars[bars.length - 2].time : 60
    for (const deal of trades) {
      const when = serverSeconds(deal.server_time)
      const bar = barAt(bars, when)
      // Deals outside the loaded bars get their markers when those bars load.
      if (!bar || when >= bars[bars.length - 1].time + span) continue
      const isSelected = deal.ticket === selected
      const opening = deal.entry === 'in'
      const result = deal.profit + deal.swap + deal.commission
      list.push({
        id: `deal-${deal.ticket}`,
        time: bar.time as UTCTimestamp,
        position: deal.type === 'buy' ? 'belowBar' : 'aboveBar',
        shape: opening ? (deal.type === 'buy' ? 'arrowUp' : 'arrowDown') : 'circle',
        color: opening ? c.accent : result >= 0 ? c.profit : c.loss,
        size: isSelected ? 2 : 1,
        text: isSelected
          ? opening
            ? `${deal.type} ${deal.volume ?? ''}`
            : `${result >= 0 ? '+' : ''}${formatNumber(result, 2)}`
          : undefined,
      })
    }
    list.sort((a, b) => Number(a.time) - Number(b.time))
    markers.current?.setMarkers(list)
    if (focus !== null) {
      const bar = barAt(bars, focus)
      const index = bar ? bars.indexOf(bar) : -1
      if (index >= 0) created.timeScale().setVisibleLogicalRange({ from: index - 60, to: index + 60 })
    }
  }, [bars, trades, selected, focus, theme])

  if (error) {
    return (
      <div className="chart chart--message" style={{ height }}>
        <Notice
          tone={error.status === 409 ? 'info' : 'danger'}
          title="The price chart is not available"
          action={<Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Try again</Button>}
        >
          {error.message}
        </Notice>
      </div>
    )
  }
  return (
    <div className="chart-wrap" style={{ height }}>
      {bars.length === 0 ? (
        <div className="chart-wrap__loading">
          <Skeleton variant="block" />
        </div>
      ) : null}
      <div ref={host} className="chart" style={{ height }} role="img" aria-label="Price chart with the run's entries and exits" />
    </div>
  )
}
