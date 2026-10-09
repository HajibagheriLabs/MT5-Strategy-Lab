import { Link } from 'react-router'
import { EmptyState } from '../ui/Notice'
import { PageHeader } from '../ui/PageHeader'

export function NotFound() {
  return (
    <>
      <PageHeader title="Nothing here" />
      <EmptyState title="This address does not lead anywhere in StrategyLab" action={<Link to="/runs">Go to runs</Link>} />
    </>
  )
}
