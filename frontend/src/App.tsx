import { lazy, Suspense } from 'react'
import type { ReactNode } from 'react'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'
import { NewRun } from './pages/NewRun'
import { NotFound } from './pages/NotFound'
import { Runs } from './pages/Runs'
import { Strategies } from './pages/Strategies'
import { Shell } from './shell/Shell'
import { SkeletonLines } from './ui/Skeleton'

// Pages with charts load the chart library only when first opened.
const RunPage = lazy(() => import('./pages/run/RunPage').then((m) => ({ default: m.RunPage })))
const Compare = lazy(() => import('./pages/Compare').then((m) => ({ default: m.Compare })))
const Kit = lazy(() => import('./pages/Kit').then((m) => ({ default: m.Kit })))

function Later({ children }: { children: ReactNode }) {
  return <Suspense fallback={<SkeletonLines lines={6} />}>{children}</Suspense>
}

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/runs" replace /> },
      { path: 'strategies', element: <Strategies /> },
      { path: 'strategies/:hash', element: <Strategies /> },
      { path: 'new', element: <NewRun /> },
      { path: 'runs', element: <Runs /> },
      { path: 'runs/:id', element: <Later><RunPage /></Later> },
      { path: 'compare', element: <Later><Compare /></Later> },
      { path: 'kit', element: <Later><Kit /></Later> },
      { path: '*', element: <NotFound /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
