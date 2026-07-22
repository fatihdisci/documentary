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
  const libraryRef = useRef<HTMLDivElement>(null)

  const slug = project?.slug ?? null

  // Play the previews at the configured music level. A dB value <= 0 maps
  // cleanly onto an <audio> element's linear 0..1 volume, so no Web Audio graph
  // is needed — just gain = 10^(dB/20). This is what lets the user hear how loud
  // the track will actually sit before rendering.
  const musicVolumeDb = project?.audio.musicVolumeDb ?? -30
  const previewGain = Math.min(1, Math.pow(10, musicVolumeDb / 20))

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

  // Apply the level to every preview element, live as the slider moves and
  // whenever the track list changes (new <audio> nodes mount).
  useEffect(() => {
    libraryRef.current?.querySelectorAll('audio').forEach((el) => {
      el.volume = previewGain
    })
  }, [previewGain, tracks])

  if (!project || !slug) {
    return (
      <div className="page">
        <h1>Müzik</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
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
          <h1>Müzik</h1>
          <p className="page-subtitle">
            Videonun arka plan müziği. Konuşma varken müzik kendiliğinden kısılır.
          </p>
        </div>
        <div className="header-actions">
          <button className="primary" onClick={() => fileInput.current?.click()} disabled={busy}>
            {busy ? 'Çalışıyor…' : 'Müzik yükle'}
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
        aria-label="Müzik dosyası yükle"
        onChange={(e) => {
          if (e.target.files?.length) void uploadFiles(e.target.files)
          e.target.value = ''
        }}
      />

      <section className="card">
        <div className="music-source-row">
          <button
            className={source === 'none' ? 'primary' : ''}
            onClick={useNoMusic}
            aria-pressed={source === 'none'}
          >
            Müzik yok
          </button>
          <span className="hint">
            {source === 'none'
              ? 'Arka planda müzik çalmayacak.'
              : source === 'generated-ambient'
                ? 'Uygulamanın ürettiği basit fon müziği kullanılıyor.'
                : selected
                  ? `“${selected}” kullanılıyor.`
                  : 'Aşağıdan bir parça seçin.'}
          </span>
        </div>
      </section>

      {tracks.length > 0 && (
        <section className="card">
          <h2>Müzik seviyesi</h2>
          <div className="music-level-row">
            <input
              type="range"
              min={-60}
              max={0}
              step={0.5}
              value={musicVolumeDb}
              onChange={(e) => edit((d) => void (d.audio.musicVolumeDb = Number(e.target.value)))}
              aria-label="Müzik seviyesi (desibel)"
            />
            <input
              type="number"
              min={-60}
              max={0}
              step={0.5}
              value={musicVolumeDb}
              onChange={(e) => edit((d) => void (d.audio.musicVolumeDb = Number(e.target.value)))}
              aria-label="Müzik seviyesi (desibel)"
            />
            <span className="music-level-db">{musicVolumeDb} dB</span>
          </div>
          <p className="hint">
            Aşağıdaki örnekler tam bu seviyede çalar; müziğin videoda ne kadar yüksek olacağını
            böyle duyarsınız. Konuşma sırasında ayrıca otomatik olarak kısılır.
          </p>
        </section>
      )}

      <section className="card" ref={libraryRef}>
        <h2>Müzik listesi</h2>
        {tracks.length === 0 ? (
          <p className="muted">
            Henüz parça yok. <code>.mp3</code>, <code>.wav</code>, <code>.m4a</code>,{' '}
            <code>.aac</code>, <code>.ogg</code> veya <code>.flac</code> dosyası yükleyin.
          </p>
        ) : (
          <table className="music-table">
            <thead>
              <tr>
                <th />
                <th>Parça</th>
                <th>Boyut</th>
                <th>Dinle</th>
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
                        <span className="tag tag-ok" title="Videoda bu parça kullanılıyor">
                          ● kullanımda
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
                        onPlay={(e) => {
                          e.currentTarget.volume = previewGain
                        }}
                        onLoadedData={(e) => {
                          e.currentTarget.volume = previewGain
                        }}
                      />
                    </td>
                    <td className="track-actions">
                      <button
                        onClick={() => selectTrack(t.filename)}
                        disabled={busy || isSelected}
                        title="Bu parçayı videoda kullan"
                      >
                        {isSelected ? 'Kullanımda' : 'Kullan'}
                      </button>
                      <button
                        className="danger"
                        onClick={() => setPendingDelete(t.filename)}
                        disabled={busy}
                      >
                        Sil
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
          title="Bu parça silinsin mi?"
          body={
            <>
              <code>{pendingDelete}</code> bu projenin müzik klasöründen silinir. Bu işlem geri
              alınamaz.
            </>
          }
          confirmLabel="Sil"
          destructive
          onConfirm={() => void confirmDelete(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}
