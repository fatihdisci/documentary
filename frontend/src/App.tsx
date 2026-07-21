/**
 * Application shell.
 *
 * Left sidebar for navigation, top bar for project identity and save status,
 * routed centre pane. Edits autosave, so switching sections never loses work.
 */

import { useEffect, useState } from 'react'
import { Diagnostics } from '@/components/Diagnostics'
import { ProjectsPage } from '@/routes/ProjectsPage'
import { ContentPage } from '@/routes/ContentPage'
import { ScenesPage } from '@/routes/ScenesPage'
import { AudioPage } from '@/routes/AudioPage'
import { StylePage } from '@/routes/StylePage'
import { ExportPage } from '@/routes/ExportPage'
import { SettingsPage } from '@/routes/SettingsPage'
import { useProjectStore, flushPendingSave } from '@/store/project'
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

const NAV: { id: Route; label: string; icon: string; needsProject: boolean; milestone: string }[] = [
  { id: 'projects', label: 'Projects', icon: '▤', needsProject: false, milestone: '' },
  { id: 'content', label: 'Content', icon: '✎', needsProject: true, milestone: '' },
  { id: 'scenes', label: 'Scenes', icon: '▦', needsProject: true, milestone: '' },
  { id: 'audio', label: 'Audio', icon: '♪', needsProject: true, milestone: '' },
  { id: 'style', label: 'Style', icon: '◐', needsProject: true, milestone: '' },
  { id: 'export', label: 'Export', icon: '↑', needsProject: true, milestone: '' },
  { id: 'settings', label: 'Settings', icon: '⚙', needsProject: false, milestone: '' },
  { id: 'diagnostics', label: 'Diagnostics', icon: '✚', needsProject: false, milestone: '' },
]

const SAVE_LABEL: Record<string, string> = {
  idle: '',
  dirty: 'Unsaved changes',
  saving: 'Saving…',
  saved: 'All changes saved',
  error: 'Save failed — will retry',
}

function ComingSoon({ label, milestone }: { label: string; milestone: string }) {
  return (
    <div className="page">
      <h1>{label}</h1>
      <p className="page-subtitle">This section is built in {milestone}.</p>
    </div>
  )
}

export default function App() {
  const { theme, toggle } = useThemeStore()
  const { project, saveStatus, openProject, closeProject, save } = useProjectStore()
  const [route, setRoute] = useState<Route>('projects')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  // Never lose an in-flight edit when the window closes.
  useEffect(() => {
    function onBeforeUnload(event: BeforeUnloadEvent) {
      if (saveStatus === 'dirty' || saveStatus === 'saving' || saveStatus === 'error') {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [saveStatus])

  async function navigate(next: Route) {
    await flushPendingSave()
    setRoute(next)
  }

  async function handleOpen(slug: string) {
    await openProject(slug)
    setRoute('content')
  }

  const current = NAV.find((item) => item.id === route)

  function renderRoute() {
    if (current?.needsProject && !project) {
      return (
        <div className="page">
          <h1>{current.label}</h1>
          <p className="page-subtitle">Open a project from the Projects tab first.</p>
        </div>
      )
    }
    switch (route) {
      case 'projects':
        return <ProjectsPage onOpen={(slug) => void handleOpen(slug)} />
      case 'content':
        return <ContentPage />
      case 'scenes':
        return <ScenesPage />
      case 'audio':
        return <AudioPage />
      case 'style':
        return <StylePage />
      case 'export':
        return <ExportPage />
      case 'settings':
        return <SettingsPage />
      case 'diagnostics':
        return <Diagnostics />
      default:
        return <ComingSoon label={current?.label ?? ''} milestone={current?.milestone ?? ''} />
    }
  }

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
              className={`nav-item ${route === item.id ? 'active' : ''} ${
                item.needsProject && !project ? 'nav-disabled' : ''
              }`}
              onClick={() => void navigate(item.id)}
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
          <span className="topbar-title">
            {project ? (
              <>
                {project.name}
                {project.animal.scientificName && (
                  <em className="topbar-sci"> · {project.animal.scientificName}</em>
                )}
              </>
            ) : (
              'No project open'
            )}
          </span>
          <div className="topbar-actions">
            {project && (
              <>
                <span className={`save-status save-${saveStatus}`}>{SAVE_LABEL[saveStatus]}</span>
                <button onClick={() => void save()} disabled={saveStatus === 'saving'}>
                  Save
                </button>
                <button
                  onClick={() => {
                    void flushPendingSave().then(() => {
                      closeProject()
                      setRoute('projects')
                    })
                  }}
                >
                  Close
                </button>
              </>
            )}
            <button onClick={toggle} title="Toggle light / dark theme" aria-label="Toggle theme">
              {theme === 'dark' ? '☀' : '☾'}
            </button>
          </div>
        </header>

        <main className="content">{renderRoute()}</main>
      </div>
    </div>
  )
}
