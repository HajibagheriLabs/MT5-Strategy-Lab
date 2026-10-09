import { Desktop, Moon, Sun } from '@phosphor-icons/react'
import { NavLink, Outlet } from 'react-router'
import { useTheme } from '../theme-context'
import type { Preference } from '../theme-context'
import { SegmentedControl } from '../ui/SegmentedControl'
import { TerminalStatus } from './TerminalStatus'
import './Shell.css'

const SECTIONS = [
  { to: '/strategies', label: 'Strategies' },
  { to: '/new', label: 'New run' },
  { to: '/runs', label: 'Runs' },
  { to: '/compare', label: 'Compare' },
]

function ThemeSwitch() {
  const { preference, setPreference } = useTheme()
  return (
    <SegmentedControl<Preference>
      label="Theme"
      hideLabel
      size="sm"
      value={preference}
      onChange={setPreference}
      options={[
        { value: 'system', label: <Desktop size={14} aria-hidden />, ariaLabel: 'Follow the system theme' },
        { value: 'light', label: <Sun size={14} aria-hidden />, ariaLabel: 'Light theme' },
        { value: 'dark', label: <Moon size={14} aria-hidden />, ariaLabel: 'Dark theme' },
      ]}
    />
  )
}

export function Shell() {
  return (
    <div className="shell">
      <a href="#main" className="shell__skip">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink to="/runs" className="topbar__mark" aria-label="StrategyLab, runs">
          Strategy<span>Lab</span>
        </NavLink>
        <nav className="topbar__nav" aria-label="Sections">
          {SECTIONS.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              className={({ isActive }) => `topbar__link ${isActive ? 'topbar__link--on' : ''}`}
            >
              {section.label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar__end">
          <TerminalStatus />
          <ThemeSwitch />
        </div>
      </header>
      <main id="main" className="shell__main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}
