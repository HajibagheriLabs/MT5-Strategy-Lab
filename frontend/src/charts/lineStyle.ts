import { LineStyle } from 'lightweight-charts'

export type LineLook = { color: 'accent' | 'fg' | 'fg2'; dash: LineStyle; css: 'solid' | 'dashed' | 'dotted' }

const LOOKS: LineLook[] = [
  { color: 'accent', dash: LineStyle.Solid, css: 'solid' },
  { color: 'fg', dash: LineStyle.Solid, css: 'solid' },
  { color: 'fg2', dash: LineStyle.Dashed, css: 'dashed' },
  { color: 'fg2', dash: LineStyle.Dotted, css: 'dotted' },
]

/** Compared runs are told apart by colour and by dash, so they stay distinct without colour
 * vision. The first run is the reference, in the accent. */
export function lineStyle(index: number): LineLook {
  return LOOKS[index % LOOKS.length]
}
