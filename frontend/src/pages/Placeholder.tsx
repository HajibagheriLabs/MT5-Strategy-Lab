import { PageHeader } from '../ui/PageHeader'

/** Stands in for a section whose screen is not built yet. */
export function Placeholder({ title, description }: { title: string; description: string }) {
  return <PageHeader title={title} description={description} />
}
