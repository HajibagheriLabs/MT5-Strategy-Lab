import type { ReactNode } from 'react'
import './PageHeader.css'

type Props = {
  title: ReactNode
  /** One line under the title; the page itself carries the detail. */
  description?: ReactNode
  actions?: ReactNode
  /** A small line above the title that places the page (a link back, a parent name). */
  context?: ReactNode
}

export function PageHeader({ title, description, actions, context }: Props) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        {context ? <div className="page-header__context">{context}</div> : null}
        <h1 className="page-header__title">{title}</h1>
        {description ? <p className="page-header__description">{description}</p> : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </header>
  )
}

/** A titled part of a page, separated from the one above by a rule. */
export function Section({ title, actions, children, id }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="page-section" aria-labelledby={title && id ? `${id}-title` : undefined}>
      {title || actions ? (
        <div className="page-section__head">
          {title ? (
            <h2 className="page-section__title" id={id ? `${id}-title` : undefined}>
              {title}
            </h2>
          ) : null}
          {actions ? <div className="page-section__actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}
