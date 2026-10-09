import { AreaSeries, createChart, LineSeries, LineStyle } from 'lightweight-charts'
import type { IChartApi, UTCTimestamp } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { SeriesPoint } from '../api/types'
import { useTheme } from '../theme-context'
import { baseOptions, chartColors, serverSeconds, withAlpha } from './chartTheme'
import './charts.css'

type Props = { points: SeriesPoint[]; height?: number; label: string }

/** Several deals can close in one second; the chart needs one value per moment, the last. */
function unique<T extends { time: UTCTimestamp }>(items: T[]): T[] {
  const out: T[] = []
  for (const item of items) {
    if (out.length && out[out.length - 1].time === item.time) out[out.length - 1] = item
    else out.push(item)
  }
  return out
}

/** Balance (and equity when the engine recorded it) above, drawdown from the running peak in
 * percent below, on one time axis. */
export function EquityChart({ points, height = 320, label }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const { theme } = useTheme()

  useEffect(() => {
    if (!host.current) return
    const c = chartColors()
    const chart: IChartApi = createChart(host.current, baseOptions())
    const balance = chart.addSeries(LineSeries, {
      color: c.fg,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'Balance',
    })
    const times = points.map((p) => serverSeconds(p.server_time) as UTCTimestamp)
    balance.setData(unique(points.map((p, i) => ({ time: times[i], value: p.balance }))))
    if (points.some((p) => p.equity !== null)) {
      const equity = chart.addSeries(LineSeries, {
        color: c.accent,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        title: 'Equity',
      })
      equity.setData(unique(points.filter((p) => p.equity !== null).map((p) => ({ time: serverSeconds(p.server_time) as UTCTimestamp, value: p.equity as number }))))
    }
    const drawdown = chart.addSeries(
      AreaSeries,
      {
        lineColor: c.loss,
        topColor: withAlpha(c.loss, 0.04),
        bottomColor: withAlpha(c.loss, 0.28),
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        title: 'Drawdown %',
        priceFormat: { type: 'custom', formatter: (v: number) => `${v.toFixed(2)}%` },
        invertFilledArea: true,
      },
      1,
    )
    drawdown.setData(unique(points.map((p, i) => ({ time: times[i], value: -p.drawdown_pct }))))
    const panes = chart.panes()
    panes[0]?.setStretchFactor(3)
    panes[1]?.setStretchFactor(1)
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [points, theme])

  return <div ref={host} className="chart" style={{ height }} role="img" aria-label={label} />
}
