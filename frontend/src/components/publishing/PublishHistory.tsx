/**
 * Everything from this project that reached a platform.
 *
 * Written the moment a video got an id, so a video that uploaded but then failed
 * its thumbnail step is still listed here with its link — the whole point of the
 * record is that nothing that exists on the channel is ever invisible here.
 */

import type { AssetStatus, PublishHistoryEntry } from '@/api/publishing-types'
import { formatDateTime } from '@/lib/format'

interface Props {
  history: PublishHistoryEntry[]
  busy: boolean
  onRefresh: (entryId: string) => void
}

const ASSET_LABEL: Record<AssetStatus, string> = {
  skipped: '—',
  pending: 'bekliyor',
  uploaded: '✓',
  failed: '✕',
}

const PRIVACY_LABEL: Record<string, string> = {
  private: 'Gizli',
  unlisted: 'Liste dışı',
  public: 'Herkese açık',
}

export function PublishHistory({ history, busy, onRefresh }: Props) {
  if (history.length === 0) return null

  return (
    <section className="card">
      <h2>Yayın geçmişi</h2>
      <table className="history-table publish-history">
        <thead>
          <tr>
            <th>Başlık</th>
            <th>Dosya</th>
            <th>Yayın zamanı</th>
            <th>Gizlilik</th>
            <th>Kapak</th>
            <th>Altyazı</th>
            <th>Durum</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {history.map((entry) => (
            <tr key={entry.entryId}>
              <td>
                <a href={entry.videoUrl} target="_blank" rel="noreferrer">
                  {entry.title || entry.videoId}
                </a>
              </td>
              <td className="history-file">{entry.filename}</td>
              <td className="muted">
                {formatDateTime(entry.actualPublishAt ?? entry.requestedPublishAt ?? entry.uploadedAt)}
                {entry.requestedPublishAt && !entry.actualPublishAt && ' (planlandı)'}
              </td>
              <td>{PRIVACY_LABEL[entry.privacyStatus] ?? entry.privacyStatus}</td>
              <td>{ASSET_LABEL[entry.thumbnailStatus]}</td>
              <td>{ASSET_LABEL[entry.captionStatus]}</td>
              <td className="muted">{entry.processingStatus ?? entry.uploadStatus ?? '—'}</td>
              <td className="history-actions">
                <button type="button" disabled={busy} onClick={() => onRefresh(entry.entryId)}>
                  Durumu yenile
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
