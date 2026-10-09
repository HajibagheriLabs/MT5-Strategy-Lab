import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ThemeContext } from './theme-context'
import type { Preference, Theme } from './theme-context'

const KEY = 'strategylab.theme'
const query = '(prefers-color-scheme: dark)'

function stored(): Preference {
  try {
    const value = window.localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

function systemTheme(): Theme {
  return window.matchMedia(query).matches ? 'dark' : 'light'
}

/** Follows the system unless the user picked a theme; the choice is remembered per browser. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<Preference>(stored)
  const [system, setSystem] = useState<Theme>(systemTheme)

  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setSystem(media.matches ? 'dark' : 'light')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const theme = preference === 'system' ? system : preference

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const value = useMemo(
    () => ({
      theme,
      preference,
      setPreference: (next: Preference) => {
        setPreference(next)
        try {
          if (next === 'system') window.localStorage.removeItem(KEY)
          else window.localStorage.setItem(KEY, next)
        } catch {
          // Storage can be unavailable; the choice then lasts for this visit.
        }
      },
    }),
    [theme, preference],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
