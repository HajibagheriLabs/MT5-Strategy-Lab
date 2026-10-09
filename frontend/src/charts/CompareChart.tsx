import { createChart, LineSeries } from 'lightweight-charts'
import type { UTCTimestamp } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { SeriesPoint } from '../api/types'
import { useTheme } from '../theme-context'
import { baseOptions, chartColors, serverSeconds } from './chartTheme'
import { lineStyle } from './lineStyle'
import './charts.css'

export type CompareLine = { id: string; label: string; points: SeriesPoint[]; deposit: number }

/** Profit since the start of each run, so runs with different deposits share one scale. */
export function CompareChart({ lines, height = 340 }: { lines: CompareLine[]; height?: number }) {
  const host = useRef<HTMLDivElement>(null)
  const { theme } = useTheme()

  useEffect(() => {
    if (!host.current) return
    const c = chartColors()
    const chart = createChart(host.current, baseOptions({ spansDays: true }))
    lines.forEach((line, index) => {
      const style = lineStyle(index)
      const series = chart.addSeries(LineSeries, {
        color: style.color === 'accent' ? c.accent : style.color === 'fg' ? c.fg : c.fg2,
        lineStyle: style.dash,
        lineWidth: 2,
        priceLineVisible: false,
        title: line.label,
      })
      const data: { time: UTCTimestamp; value: number }[] = []
      for (const point of line.points) {
        const time = serverSeconds(point.server_time) as UTCTimestamp
        const value = point.balance - line.deposit
        if (data.length && data[data.length - 1].time === time) data[data.length - 1] = { time, value }
        else data.push({ time, value })
      }
      series.setData(data)
    })
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [lines, theme])

  return <div ref={host} className="chart" style={{ height }} role="img" aria-label="Profit since the start of each run" />
}
