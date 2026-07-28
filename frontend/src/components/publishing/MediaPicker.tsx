/**
 * Choosing what to publish.
 *
 * Lists finished long videos and finished Shorts side by side. Only files that
 * really exist on disk reach this list; anything the backend flagged as not
 * worth publishing (a quick-check render, a file that changed since it was made)
 * is marked and hidden unless the user asks to see it.
 */

import type { MediaItem } from '@/api/publishing-types'
import { formatBytes, formatDateTime, formatDuration } from '@/lib/format'

interface Props {
  media: MediaItem[]
  selectedMediaId: string | null
  showAll: boolean
  onToggleShowAll: (value: boolean) => void
  onSelect: (mediaId: string) => void
}

const KIND_LABEL: Record<MediaItem['kind'], string> = {
  long: 'Uzun video',
  short: 'Kısa video',
}

export function MediaPicker({
  media, selectedMediaId, showAll, onToggleShowAll, onSelect,
}: Props) {
  const hidden = media.filter((item) => !item.recommended).length
  const visible = showAll ? media : media.filter((item) => item.recommended)

  return (
    <section className="card">
      <div className="publish-section-head">
        <h2>1. Dosya seçin</h2>
        {hidden > 0 && (
          <label className="checkbox inline">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(event) => onToggleShowAll(event.target.checked)}
            />
            Yayına uygun olmayanları da göster ({hidden})
          </label>
        )}
      </div>

      {visible.length === 0 ? (
        <p className="muted">
          Bu projede yayınlanabilecek bir video yok. Önce “Videoyu oluştur” sekmesinden uzun
          videoyu oluşturun ya da “Kısa video” sekmesinden bir kısa video hazırlayın.
        </p>
      ) : (
        <div className="media-grid">
          {visible.map((item) => (
            <button
              key={item.mediaId}
              type="button"
              className={`media-card ${item.mediaId === selectedMediaId ? 'selected' : ''} ${
                item.recommended ? '' : 'not-recommended'
              }`}
              aria-pressed={item.mediaId === selectedMediaId}
              onClick={() => onSelect(item.mediaId)}
            >
              <span className="media-kind">{KIND_LABEL[item.kind]}</span>
              <span className="media-name">{item.filename}</span>
              <span className="media-meta">
                {formatDateTime(item.createdAt)} · {formatDuration(item.durationSeconds)} ·{' '}
                {formatBytes(item.sizeBytes)}
              </span>
              <span className="media-meta">
                {item.width}×{item.height}
                {item.fps ? ` · ${item.fps} fps` : ''} · {item.projectName || item.projectSlug}
              </span>
              {item.captionFilename && (
                <span className="media-badge">Altyazı dosyası var</span>
              )}
              {item.hasDraft && <span className="media-badge">Yayın bilgileri hazır</span>}
              {item.publishedVideoId && (
                <span className="media-badge published">YouTube'a yüklenmiş</span>
              )}
              {item.note && <span className="media-note">⚠ {item.note}</span>}
              <span
                className="media-open"
                role="link"
                tabIndex={-1}
                onClick={(event) => {
                  event.stopPropagation()
                  window.open(item.url, '_blank', 'noopener')
                }}
              >
                Önizle
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
