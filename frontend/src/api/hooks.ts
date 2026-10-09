import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from './client'
import type { Health } from './types'

export type Resource<T> = {
  data: T | undefined
  error: ApiError | undefined
  loading: boolean
  reload: () => void
}

type Answer<T> = { key: string; data?: T; error?: ApiError }

/** GET a path and keep the answer; `null` skips the request. A reload keeps the old data on
 * screen until the new answer arrives, so tables do not flash. */
export function useResource<T>(path: string | null, refreshMs?: number): Resource<T> {
  const [answer, setAnswer] = useState<Answer<T>>({ key: '' })
  const [last, setLast] = useState<{ path: string | null; data?: T }>({ path: null })
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((value) => value + 1), [])
  const key = `${path}#${tick}`

  useEffect(() => {
    if (path === null) return
    let live = true
    api
      .get<T>(path)
      .then((data) => {
        if (!live) return
        setAnswer({ key, data })
        setLast({ path, data })
      })
      .catch((reason: unknown) => {
        if (live) setAnswer({ key, error: reason instanceof ApiError ? reason : new ApiError(0, String(reason)) })
      })
    return () => {
      live = false
    }
  }, [path, key])

  useEffect(() => {
    if (!refreshMs || path === null) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, refreshMs)
    return () => window.clearInterval(timer)
  }, [refreshMs, path, reload])

  // The last good answer for this path stays on screen while a reload is in flight.
  const data = answer.key === key && answer.data !== undefined ? answer.data : last.path === path ? last.data : undefined
  const error = answer.key === key ? answer.error : undefined
  const settled = answer.key === key
  return { data, error, loading: path !== null && !settled && data === undefined, reload }
}

const HEALTH_MS = 5000

/** The health endpoint, polled while the page is visible and again whenever it becomes so. */
export function useHealth(): Resource<Health> {
  const resource = useResource<Health>('/health', HEALTH_MS)
  const { reload } = resource
  const last = useRef(0)
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - last.current > 1000) {
        last.current = Date.now()
        reload()
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [reload])
  return resource
}
