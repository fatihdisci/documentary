/**
 * Application shell.
 *
 * Left sidebar for navigation, top bar for project identity and save status,
 * routed centre pane. Edits autosave, so switching sections never loses work.
 */

import { useEffect, useState } from 'react'
import { Diagnostics } from '@/components/Diagnostics'
import { ProjectsPage } from '@/routes/ProjectsPage'
import { SimpleModePage } from '@/routes/SimpleModePage'
import { ContentPage } from '@/routes/ContentPage'
import { ScenesPage } from '@/routes/ScenesPage'
import { AudioPage } from '@/routes/AudioPage'
import { MusicPage } from '@/routes/MusicPage'
import { StylePage } from '@/routes/StylePage'
import { ExportPage } from '@/routes/ExportPage'
import { ShortsPage } from '@/routes/ShortsPage'
import { SettingsPage } from '@/routes/SettingsPage'
import { useProjectStore, flushPendingSave } from '@/store/project'
import { useThemeStore } from '@/store/theme'
import './App.css'

type Route =
  | 'projects'
  | 'guided'
  | 'content'
  | 'scenes'
  | 'audio'
  | 'music'
  | 'style'
  | 'export'
  | 'shorts'
  | 'settings'
  | 'diagnostics'

const NAV: { id: Route; label: string; icon: string; needsProject: boolean; milestone: string }[] = [
  { id: 'projects', label: 'Projeler', icon: '▤', needsProject: false, milestone: '' },
  { id: 'guided', label: 'Kolay kurulum', icon: '✦', needsProject: true, milestone: '' },
  { id: 'content', label: 'Metinler', icon: '✎', needsProject: true, milestone: '' },
  { id: 'scenes', label: 'Sahneler', icon: '▦', needsProject: true, milestone: '' },
  { id: 'audio', label: 'Seslendirme', icon: '♪', needsProject: true, milestone: '' },
  { id: 'music', label: 'Müzik', icon: '♬', needsProject: true, milestone: '' },
  { id: 'style', label: 'Görünüm', icon: '◐', needsProject: true, milestone: '' },
  { id: 'export', label: 'Videoyu oluştur', icon: '↑', needsProject: true, milestone: '' },
  { id: 'shorts', label: 'Kısa video', icon: '▯', needsProject: true, milestone: '' },
  { id: 'settings', label: 'Ayarlar', icon: '⚙', needsProject: false, milestone: '' },
  { id: 'diagnostics', label: 'Sistem kontrolü', icon: '✚', needsProject: false, milestone: '' },
]

const SAVE_LABEL: Record<string, string> = {
  idle: '',
  dirty: 'Kaydedilmedi',
  saving: 'Kaydediliyor…',
  saved: 'Kaydedildi',
  error: 'Kaydedilemedi — tekrar denenecek',
}

function ComingSoon({ label, milestone }: { label: string; milestone: string }) {
  return (
    <div className="page">
      <h1>{label}</h1>
      <p className="page-subtitle">Bu bölüm henüz hazır değil ({milestone}).</p>
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
    setRoute('guided')
  }

  const current = NAV.find((item) => item.id === route)

  function renderRoute() {
    if (current?.needsProject && !project) {
      return (
        <div className="page">
          <h1>{current.label}</h1>
          <p className="page-subtitle">Önce Projeler sekmesinden bir proje açın.</p>
        </div>
      )
    }
    switch (route) {
      case 'projects':
        return <ProjectsPage onOpen={(slug) => void handleOpen(slug)} />
      case 'guided':
        return <SimpleModePage goTo={(tab) => void navigate(tab as Route)} />
      case 'content':
        return <ContentPage />
      case 'scenes':
        return <ScenesPage />
      case 'audio':
        return <AudioPage />
      case 'music':
        return <MusicPage />
      case 'style':
        return <StylePage />
      case 'export':
        return <ExportPage />
      case 'shorts':
        return <ShortsPage />
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
          <span className="brand-name">Belgesel Video Stüdyosu</span>
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
              'Açık proje yok'
            )}
          </span>
          <div className="topbar-actions">
            {project && (
              <>
                <span className={`save-status save-${saveStatus}`}>{SAVE_LABEL[saveStatus]}</span>
                <button onClick={() => void save()} disabled={saveStatus === 'saving'}>
                  Kaydet
                </button>
                <button
                  onClick={() => {
                    void flushPendingSave().then(() => {
                      closeProject()
                      setRoute('projects')
                    })
                  }}
                >
                  Kapat
                </button>
              </>
            )}
            <button onClick={toggle} title="Açık / koyu tema" aria-label="Temayı değiştir">
              {theme === 'dark' ? '☀' : '☾'}
            </button>
          </div>
        </header>

        <main className="content">{renderRoute()}</main>
      </div>
    </div>
  )
}
