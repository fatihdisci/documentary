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
          <h1>Projects</h1>
          <p className="page-subtitle">Each project is a folder on disk you can back up or move.</p>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} onRetry={() => void loadProjects()} />}

      <form className="new-project" onSubmit={(e) => void handleCreate(e)}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="New project name — e.g. Dodo"
          aria-label="New project name"
        />
        <button type="submit" className="primary" disabled={!newName.trim() || creating}>
          {creating ? 'Creating…' : 'Create project'}
        </button>
      </form>

      {loading && projects.length === 0 && <p className="muted">Loading projects…</p>}

      {!loading && projects.length === 0 && (
        <div className="empty">
          <h2>No projects yet</h2>
          <p>
            Create one above, then import a content package and drop in your images. The Dodo
            example package is available on the Content tab.
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
                  {project.archived && <span className="tag">Archived</span>}
                </span>
                <span className="project-meta">
                  {project.sceneCount} scene{project.sceneCount === 1 ? '' : 's'}
                  {project.commonName && ` · ${project.commonName}`} · updated{' '}
                  {new Date(project.updatedAt).toLocaleString()}
                </span>
              </span>
            </button>

            <div className="project-actions">
              {project.archived ? (
                <button onClick={() => void withReload(() => api.unarchiveProject(project.slug))}>
                  Unarchive
                </button>
              ) : (
                <>
                  <button
                    onClick={() =>
                      void withReload(() =>
                        api.duplicateProject(project.slug, `${project.name} copy`),
                      )
                    }
                  >
                    Duplicate
                  </button>
                  <button onClick={() => void withReload(() => api.archiveProject(project.slug))}>
                    Archive
                  </button>
                </>
              )}
              <a className="button-link" href={`/api/projects/${project.slug}/bundle`} download>
                Export bundle
              </a>
              <button className="danger" onClick={() => setPendingDelete(project.slug)}>
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this project permanently?"
          body={
            <>
              <p>
                This deletes <strong>{pendingDelete}</strong> and everything in it — images, audio,
                exports and project.json — from disk. It cannot be undone.
              </p>
              <p className="muted">
                If you only want it out of the way, use <strong>Archive</strong> instead.
              </p>
            </>
          }
          confirmLabel="Delete permanently"
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
