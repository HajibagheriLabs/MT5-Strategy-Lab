import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'
import { Kit } from './pages/Kit'
import { Placeholder } from './pages/Placeholder'
import { Shell } from './shell/Shell'

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/runs" replace /> },
      {
        path: 'strategies',
        element: <Placeholder title="Strategies" description="Uploaded strategies, their inputs and their runs." />,
      },
      {
        path: 'new',
        element: <Placeholder title="New run" description="Choose a strategy, a symbol and a period, and start a backtest." />,
      },
      { path: 'runs', element: <Placeholder title="Runs" description="Every backtest, newest first." /> },
      { path: 'compare', element: <Placeholder title="Compare" description="Two to four runs side by side." /> },
      { path: 'kit', element: <Kit /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
