/**
 * Application shell.
 *
 * Left sidebar for navigation, top bar for project-level actions, and a routed
 * centre pane. Routes fill in across the milestones; Diagnostics is live now.
 */

import { useEffect, useState } from 'react'
import { Diagnostics } from '@/components/Diagnostics'
import { useThemeStore } from '@/store/theme'
import './App.css'

type Route =
  | 'projects'
  | 'content'
  | 'scenes'
  | 'audio'
  | 'style'
  | 'export'
  | 'settings'
  | 'diagnostics'

const NAV: { id: Route; label: string; icon: string; milestone: string }[] = [
  { id: 'projects', label: 'Projects', icon: '▤', milestone: 'Milestone 2' },
  { id: 'content', label: 'Content', icon: '✎', milestone: 'Milestone 2' },
  { id: 'scenes', label: 'Scenes', icon: '▦', milestone: 'Milestone 2' },
  { id: 'audio', label: 'Audio', icon: '♪', milestone: 'Milestone 3' },
  { id: 'style', label: 'Style', icon: '◐', milestone: 'Milestone 4' },
  { id: 'export', label: 'Export', icon: '↑', milestone: 'Milestone 6' },
  { id: 'settings', label: 'Settings', icon: '⚙', milestone: 'Milestone 2' },
  { id: 'diagnostics', label: 'Diagnostics', icon: '✚', milestone: '' },
]

/** Placeholder for routes whose milestone has not landed yet. */
function ComingSoon({ label, milestone }: { label: string; milestone: string }) {
  return (
    <div className="page">
      <h1>{label}</h1>
      <p className="page-subtitle">
        This section is built in {milestone}. Diagnostics is available now and confirms the
        environment can render.
      </p>
    </div>
  )
}

export default function App() {
  const { theme, toggle } = useThemeStore()
  const [route, setRoute] = useState<Route>('diagnostics')
  const current = NAV.find((item) => item.id === route)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">EVB</span>
          <span className="brand-name">Extinct Video Builder</span>
        </div>
        <nav>
          {NAV.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${route === item.id ? 'active' : ''}`}
              onClick={() => setRoute(item.id)}
              aria-current={route === item.id ? 'page' : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="main">
        <header className="topbar">
          <span className="topbar-title">No project open</span>
          <div className="topbar-actions">
            <button onClick={toggle} title="Toggle light / dark theme" aria-label="Toggle theme">
              {theme === 'dark' ? '☀' : '☾'}
            </button>
          </div>
        </header>

        <main className="content">
          {route === 'diagnostics' ? (
            <Diagnostics />
          ) : (
            <ComingSoon label={current?.label ?? ''} milestone={current?.milestone ?? ''} />
          )}
        </main>
      </div>
    </div>
  )
}
