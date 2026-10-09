import { ColorType, CrosshairMode, LineStyle } from 'lightweight-charts'
import type { DeepPartial, ChartOptions } from 'lightweight-charts'
import { tokenValue } from '../theme-context'

/** Token colours as the chart library needs them, read at the moment of drawing so a theme
 * change redraws in the new theme. */
export function chartColors() {
  const t = (name: string) => tokenValue(name)
  return {
    bg: t('--surface'),
    fg: t('--fg'),
    fg2: t('--fg-2'),
    fg3: t('--fg-3'),
    line: t('--line'),
    lineStrong: t('--line-strong'),
    accent: t('--accent'),
    profit: t('--profit'),
    loss: t('--loss'),
  }
}

export function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16))
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export function baseOptions(): DeepPartial<ChartOptions> {
  const c = chartColors()
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: c.bg },
      textColor: c.fg2,
      fontFamily: tokenValue('--font-mono'),
      fontSize: 11,
      attributionLogo: false,
      panes: { separatorColor: c.lineStrong, separatorHoverColor: c.lineStrong },
    },
    grid: {
      vertLines: { color: c.line, style: LineStyle.Solid },
      horzLines: { color: c.line, style: LineStyle.Solid },
    },
    rightPriceScale: { borderColor: c.lineStrong },
    timeScale: { borderColor: c.lineStrong, timeVisible: true, secondsVisible: false },
    // The wheel scrolls the page, as everywhere else in it; drag pans and dragging an axis scales.
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: c.fg3, labelBackgroundColor: c.fg2, style: LineStyle.Dashed },
      horzLine: { color: c.fg3, labelBackgroundColor: c.fg2, style: LineStyle.Dashed },
    },
  }
}

/** Server time as the chart's time: seconds since 1970, read as UTC so labels show server time. */
export function serverSeconds(iso: string): number {
  const text = iso.length <= 19 ? `${iso}Z` : iso.replace(/([+-]\d\d:\d\d|Z)$/, '') + 'Z'
  return Math.floor(Date.parse(text) / 1000)
}
