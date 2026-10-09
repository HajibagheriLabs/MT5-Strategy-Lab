import { createContext, useContext } from 'react'

export type Theme = 'light' | 'dark'
export type Preference = Theme | 'system'

export const ThemeContext = createContext<{
  theme: Theme
  preference: Preference
  setPreference: (preference: Preference) => void
} | null>(null)

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme needs a ThemeProvider above it.')
  return context
}

/** Reads a token's value as currently resolved, for code that cannot use CSS (charts). */
export function tokenValue(name: string, element: Element = document.documentElement): string {
  return getComputedStyle(element).getPropertyValue(name).trim()
}
