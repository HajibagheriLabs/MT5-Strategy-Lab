import './Skeleton.css'

type Props = {
  width?: string
  height?: string
  /** Text lines are as tall as a line of text; blocks fill their box (charts). */
  variant?: 'text' | 'block'
}

export function Skeleton({ width = '100%', height, variant = 'text' }: Props) {
  return (
    <span
      className={`skeleton skeleton--${variant}`}
      style={{ width, height }}
      aria-hidden
    />
  )
}

/** Several text lines, for a paragraph or a log that is loading. */
export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <span className="skeleton-lines" aria-hidden>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} width={index === lines - 1 ? '60%' : '100%'} />
      ))}
    </span>
  )
}
