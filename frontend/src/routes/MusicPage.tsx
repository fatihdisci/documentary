/**
 * Music library.
 *
 * Uploaded background tracks live in the project's `music/` folder. This is the
 * dedicated screen for managing them: upload, audition, delete, and pick the
 * one the render uses. Selecting a track sets `music.source = 'uploaded'` and
 * points `music.file` at it; the finer mix controls (level, ducking, fades)
 * stay on the Audio page, which is where they belong next to the narration.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { MusicTrack } from '@/api/project-types'
import { useProjectStore } from '@/store/project'
import { ErrorBox } from '@/components/ErrorBox'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import './MusicPage.css'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

export function MusicPage() {
  const { project, edit, save } = useProjectStore()
  const [tracks, setTracks] = useState<MusicTrack[]>([])
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const slug = project?.slug ?? null

  const refresh = useCallback(async () => {
    if (!slug) return
    try {
      setTracks(await api.listMusic(slug))
    } catch (err) {
      setError(describeError(err))
    }
  }, [slug])

  useEffect(() => {
    void refresh()
  }, [refresh])

  if (!project || !slug) {
    return (
      <div className="page">
        <h1>Music</h1>
        <p className="page-subtitle">Open a project first.</p>
      </div>
    )
  }

  const source = project.music.source
  const selected = project.music.file

  async function uploadFiles(files: FileList | File[]) {
    setBusy(true)
    setError(null)
    try {
      let lastName = ''
      for (const file of Array.from(files)) {
        lastName = (await api.uploadMusic(slug!, file)).filename
      }
      await refresh()
      // A first upload with nothing selected yet becomes the active track.
      if (lastName && (source !== 'uploaded' || !selected)) {
        selectTrack(lastName)
      }
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  function selectTrack(filename: string) {
    edit((d) => {
      d.music.source = 'uploaded'
      d.music.file = filename
    })
  }

  function useNoMusic() {
    edit((d) => {
      d.music.source = 'none'
      d.music.file = null
    })
  }

  async function confirmDelete(filename: string) {
    setBusy(true)
    setError(null)
    try {
      await api.deleteMusic(slug!, filename)
      // The backend clears the reference server-side; mirror it locally so the
      // UI doesn't keep pointing at a track that no longer exists.
      if (selected === filename) {
        edit((d) => {
          if (d.music.source === 'uploaded') d.music.source = 'none'
          d.music.file = null
        })
        await save()
      }
      await refresh()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
      setPendingDelete(null)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Music</h1>
          <p className="page-subtitle">
            Background tracks for this project. Levels, ducking and fades live on the Audio page.
          </p>
        </div>
        <div className="header-actions">
          <button className="primary" onClick={() => fileInput.current?.click()} disabled={busy}>
            {busy ? 'Working…' : 'Upload track'}
          </button>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <input
        ref={fileInput}
        type="file"
        accept="audio/wav,audio/mpeg,audio/mp4,audio/ogg,audio/flac,.wav,.mp3,.m4a,.aac,.ogg,.flac"
        hidden
        multiple
        aria-label="Upload music track"
        onChange={(e) => {
          if (e.target.files?.length) void uploadFiles(e.target.files)
          e.target.value = ''
        }}
      />

      <section className="card">
        <div className="music-source-row">
          <span className={`music-mode ${source === 'none' ? 'active' : ''}`}>
            <button
              className={source === 'none' ? 'primary' : ''}
              onClick={useNoMusic}
              disabled={source === 'none'}
            >
              No music
            </button>
          </span>
          <span className="hint">
            {source === 'none'
              ? 'No background music will be mixed in.'
              : source === 'generated-ambient'
                ? 'Using the basic generated ambient bed (set on the Audio page).'
                : selected
                  ? `Using “${selected}”.`
                  : 'Pick a track below to use it in the render.'}
          </span>
        </div>
      </section>

      <section className="card">
        <h2>Library</h2>
        {tracks.length === 0 ? (
          <p className="muted">
            No tracks yet. Upload a <code>.wav</code>, <code>.mp3</code>, <code>.m4a</code>,{' '}
            <code>.aac</code>, <code>.ogg</code> or <code>.flac</code> file to get started.
          </p>
        ) : (
          <table className="music-table">
            <thead>
              <tr>
                <th />
                <th>Track</th>
                <th>Size</th>
                <th>Preview</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tracks.map((t) => {
                const isSelected = source === 'uploaded' && selected === t.filename
                return (
                  <tr key={t.filename} className={isSelected ? 'selected' : ''}>
                    <td>
                      {isSelected ? (
                        <span className="tag tag-ok" title="Used in the render">
                          ● in use
                        </span>
                      ) : null}
                    </td>
                    <td className="track-name">{t.filename}</td>
                    <td>{formatBytes(t.sizeBytes)}</td>
                    <td>
                      <audio
                        controls
                        preload="none"
                        src={`/api/projects/${slug}/media/music/${encodeURIComponent(t.filename)}`}
                      />
                    </td>
                    <td className="track-actions">
                      <button
                        onClick={() => selectTrack(t.filename)}
                        disabled={busy || isSelected}
                        title="Use this track in the render"
                      >
                        {isSelected ? 'In use' : 'Use'}
                      </button>
                      <button
                        className="danger"
                        onClick={() => setPendingDelete(t.filename)}
                        disabled={busy}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this track?"
          body={
            <>
              <code>{pendingDelete}</code> will be removed from this project's music folder. This
              cannot be undone.
            </>
          }
          confirmLabel="Delete"
          destructive
          onConfirm={() => void confirmDelete(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}
