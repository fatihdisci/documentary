import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { useProjectStore } from '@/store/project'
import { ErrorBox } from '@/components/ErrorBox'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import './ProjectsPage.css'

interface Props {
  onOpen: (slug: string) => void
}

export function ProjectsPage({ onOpen }: Props) {
  const { projects, loading, error, loadProjects, createProject, clearError, setError } =
    useProjectStore()
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    const name = newName.trim()
    if (!name) return
    setCreating(true)
    const slug = await createProject(name)
    setCreating(false)
    if (slug) {
      setNewName('')
      onOpen(slug)
    }
  }

  async function withReload(action: () => Promise<unknown>) {
    try {
      await action()
      await loadProjects()
    } catch (err) {
      setError(err)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Projeler</h1>
          <p className="page-subtitle">
            Her video ayrı bir projedir. Her proje bilgisayarınızda kendi klasöründe durur.
          </p>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} onRetry={() => void loadProjects()} />}

      <form className="new-project" onSubmit={(e) => void handleCreate(e)}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="Yeni proje adı — örneğin Dodo Kuşu"
          aria-label="Yeni proje adı"
        />
        <button type="submit" className="primary" disabled={!newName.trim() || creating}>
          {creating ? 'Oluşturuluyor…' : 'Proje oluştur'}
        </button>
      </form>

      {loading && projects.length === 0 && <p className="muted">Projeler yükleniyor…</p>}

      {!loading && projects.length === 0 && (
        <div className="empty">
          <h2>Henüz proje yok</h2>
          <p>
            Yukarıdan bir proje oluşturun. Sonra Metinler sekmesinden hazır örnek dosyayı
            yükleyebilir ya da kendi metinlerinizi yazabilirsiniz.
          </p>
        </div>
      )}

      <ul className="project-list">
        {projects.map((project) => (
          <li key={project.slug} className={project.archived ? 'archived' : ''}>
            <button className="project-open" onClick={() => onOpen(project.slug)}>
              {project.thumbnailUrl ? (
                <img src={project.thumbnailUrl} alt="" className="project-thumb" />
              ) : (
                <span className="project-thumb placeholder" aria-hidden="true" />
              )}
              <span className="project-info">
                <span className="project-name">
                  {project.name}
                  {project.archived && <span className="tag">Arşivde</span>}
                </span>
                <span className="project-meta">
                  {project.sceneCount} sahne
                  {project.commonName && ` · ${project.commonName}`} · son değişiklik{' '}
                  {new Date(project.updatedAt).toLocaleString()}
                </span>
              </span>
            </button>

            <div className="project-actions">
              {project.archived ? (
                <button onClick={() => void withReload(() => api.unarchiveProject(project.slug))}>
                  Arşivden çıkar
                </button>
              ) : (
                <>
                  <button
                    onClick={() =>
                      void withReload(() =>
                        api.duplicateProject(project.slug, `${project.name} kopya`),
                      )
                    }
                  >
                    Kopyala
                  </button>
                  <button onClick={() => void withReload(() => api.archiveProject(project.slug))}>
                    Arşivle
                  </button>
                </>
              )}
              <a className="button-link" href={`/api/projects/${project.slug}/bundle`} download>
                Yedeğini indir
              </a>
              <button className="danger" onClick={() => setPendingDelete(project.slug)}>
                Sil
              </button>
            </div>
          </li>
        ))}
      </ul>

      {pendingDelete && (
        <ConfirmDialog
          title="Bu proje kalıcı olarak silinsin mi?"
          body={
            <>
              <p>
                <strong>{pendingDelete}</strong> projesi ve içindeki her şey — görseller, sesler,
                oluşturulan videolar — bilgisayarınızdan silinir. Bu işlem geri alınamaz.
              </p>
              <p className="muted">
                Sadece listeden kaybolsun istiyorsanız <strong>Arşivle</strong> deyin.
              </p>
            </>
          }
          confirmLabel="Kalıcı olarak sil"
          destructive
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => {
            const slug = pendingDelete
            setPendingDelete(null)
            void withReload(() => api.deleteProject(slug))
          }}
        />
      )}
    </div>
  )
}
