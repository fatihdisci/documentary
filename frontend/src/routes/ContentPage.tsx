import { useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { ImportReport } from '@/api/project-types'
import { useProjectStore } from '@/store/project'
import { ErrorBox } from '@/components/ErrorBox'
import './ContentPage.css'

export function ContentPage() {
  const { project, edit, openProject } = useProjectStore()
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [replaceScenes, setReplaceScenes] = useState(true)
  const fileInput = useRef<HTMLInputElement>(null)

  if (!project) {
    return (
      <div className="page">
        <h1>Content</h1>
        <p className="page-subtitle">Open a project first.</p>
      </div>
    )
  }

  async function importFile(file: File) {
    if (!project) return
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      const result = await api.importContentFile(project.slug, file, replaceScenes, true)
      setReport(result.report)
      await openProject(project.slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  async function downloadExample() {
    try {
      const example = await api.contentExample()
      const blob = new Blob([JSON.stringify(example, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'example-content-package.json'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(describeError(err))
    }
  }

  async function exportContent() {
    if (!project) return
    try {
      const content = await api.exportContent(project.slug)
      const blob = new Blob([JSON.stringify(content, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${project.slug}-content.json`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(describeError(err))
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Content</h1>
          <p className="page-subtitle">
            Import a content package to fill in every scene at once, or edit the fields by hand.
          </p>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <section className="card">
        <h2>Import a content package</h2>
        <p className="muted">
          A JSON file with narration, titles, image prompts and framing hints. Importing never
          changes your video, style or audio settings.
        </p>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={replaceScenes}
            onChange={(e) => setReplaceScenes(e.target.checked)}
          />
          Replace existing scenes
          <span className="hint">
            {replaceScenes
              ? 'Scenes are rebuilt from the package. Per-scene tuning is lost.'
              : 'Existing scenes are updated in place, keeping audio and manual durations.'}
          </span>
        </label>

        <div className="row">
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            aria-label="Content package JSON"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void importFile(file)
              e.target.value = ''
            }}
          />
          <button onClick={() => void downloadExample()} disabled={busy}>
            Download example template
          </button>
          <button onClick={() => void exportContent()} disabled={busy}>
            Export this project's content
          </button>
        </div>

        {busy && <p className="muted">Importing…</p>}

        {report && (
          <div className="import-report">
            <h3>Import complete</h3>
            <ul>
              {report.scenesCreated > 0 && <li>{report.scenesCreated} scenes created</li>}
              {report.scenesUpdated > 0 && <li>{report.scenesUpdated} scenes updated</li>}
              <li>{report.imagesMapped} images mapped to scenes</li>
            </ul>
            {report.warnings.length > 0 && (
              <div className="warnings">
                {report.warnings.map((warning) => (
                  <p key={warning}>⚠ {warning}</p>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="card">
        <h2>Video details</h2>
        <div className="field-grid">
          <label>
            Animal name
            <input
              value={project.animal.commonName}
              onChange={(e) => edit((d) => void (d.animal.commonName = e.target.value))}
            />
          </label>
          <label>
            Scientific name
            <input
              value={project.animal.scientificName}
              onChange={(e) => edit((d) => void (d.animal.scientificName = e.target.value))}
            />
          </label>
          <label className="span-2">
            Video title
            <input
              value={project.metadata.videoTitle}
              onChange={(e) => edit((d) => void (d.metadata.videoTitle = e.target.value))}
            />
          </label>
          <label className="span-2">
            YouTube description
            <textarea
              rows={8}
              value={project.metadata.description}
              onChange={(e) => edit((d) => void (d.metadata.description = e.target.value))}
            />
          </label>
          <label>
            Thumbnail text
            <input
              value={project.metadata.thumbnailText}
              onChange={(e) => edit((d) => void (d.metadata.thumbnailText = e.target.value))}
            />
          </label>
          <label>
            Thumbnail prompt
            <input
              value={project.metadata.thumbnailPrompt}
              onChange={(e) => edit((d) => void (d.metadata.thumbnailPrompt = e.target.value))}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <h2>Intro narration</h2>
        <textarea
          rows={4}
          value={project.intro.narration}
          onChange={(e) => edit((d) => void (d.intro.narration = e.target.value))}
          placeholder="Spoken over the opening shot."
        />
        <h2>Outro narration</h2>
        <textarea
          rows={4}
          value={project.outro.narration}
          onChange={(e) => edit((d) => void (d.outro.narration = e.target.value))}
          placeholder="Closing message, subscribe prompt, next episode teaser."
        />
      </section>
    </div>
  )
}
