/**
 * The Publish panel.
 *
 * Four steps down the page: pick a file, fill in the metadata, choose a
 * platform, and see what has already gone out. Everything typed here belongs to
 * the selected file — it is a publishing draft, and it deliberately never writes
 * back into the project's own metadata.
 */

import { useEffect, useState } from 'react'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ErrorBox } from '@/components/ErrorBox'
import { MediaPicker } from '@/components/publishing/MediaPicker'
import { MetadataEditor } from '@/components/publishing/MetadataEditor'
import { PublishHistory } from '@/components/publishing/PublishHistory'
import {
  FacebookPanel,
  InstagramPanel,
  TikTokPanel,
} from '@/components/publishing/SocialPanels'
import { YouTubePanel } from '@/components/publishing/YouTubePanel'
import { MAX_TITLE_CHARS } from '@/api/publishing-types'
import { useProjectStore } from '@/store/project'
import { usePublishingStore } from '@/store/publishing'
import { formatDateTime } from '@/lib/format'
import './PublishingPage.css'

const SAVE_LABEL: Record<string, string> = {
  idle: '',
  dirty: 'Kaydedilmedi',
  saving: 'Kaydediliyor…',
  saved: 'Kaydedildi',
  error: 'Kaydedilemedi — tekrar denenecek',
}

export function PublishingPage() {
  const { project } = useProjectStore()
  const {
    media, selectedMediaId, draft, selectedMedia, sourceChanged, sourceChangedReason,
    duplicateOf, connection, history, job, event, loading, busy, saveStatus, error,
    loadConnection, connectYoutube, loadMedia, selectMedia, editDraft, refillFromProject,
    attachThumbnail, attachCaption, publish, cancel, retry, detach, reattachIfRunning,
    loadHistory, refreshHistoryEntry, clearError,
  } = usePublishingStore()

  const [showAll, setShowAll] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [allowDuplicate, setAllowDuplicate] = useState(false)
  const slug = project?.slug ?? null

  useEffect(() => {
    if (!slug) return
    void loadConnection(false)
    void loadMedia(slug)
    void loadHistory(slug)
    void reattachIfRunning(slug)
    return () => detach()
  }, [slug, loadConnection, loadMedia, loadHistory, reattachIfRunning, detach])

  // A different file means a different draft, so the duplicate override must
  // not survive the switch.
  useEffect(() => {
    setAllowDuplicate(false)
  }, [selectedMediaId])

  if (!project || !slug) {
    return (
      <div className="page">
        <h1>Yayınla</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
      </div>
    )
  }

  const scheduled = draft?.youtube.publishMode === 'schedule'
  const blockedReason = (() => {
    if (!draft || !selectedMedia) return 'Önce bir video seçin.'
    if (sourceChanged) return 'Seçili dosya değişmiş; bilgileri gözden geçirin.'
    if (!draft.youtube.title.trim()) return 'Başlık boş olamaz.'
    if (draft.youtube.title.length > MAX_TITLE_CHARS) return 'Başlık çok uzun.'
    if (scheduled && !draft.youtube.publishAtLocal) return 'Planlanan tarihi seçin.'
    if (duplicateOf && !allowDuplicate) return 'Bu dosya daha önce yüklenmiş.'
    return null
  })()

  return (
    <div className="page publishing-page">
      <header className="page-header">
        <div>
          <h1>Yayınla</h1>
          <p className="page-subtitle">
            Hazır videolarınızı ve kısa videolarınızı buradan YouTube'a yükleyin. Bilgiler proje
            metadatasından gelir; yüklemeden önce hepsini değiştirebilirsiniz.
          </p>
        </div>
        <div className="header-actions">
          <span className={`save-status save-${saveStatus}`}>{SAVE_LABEL[saveStatus]}</span>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {loading && media.length === 0 && <p className="muted">Yükleniyor…</p>}

      <MediaPicker
        media={media}
        selectedMediaId={selectedMediaId}
        showAll={showAll}
        onToggleShowAll={setShowAll}
        onSelect={(mediaId) => void selectMedia(slug, mediaId)}
      />

      {sourceChanged && sourceChangedReason && (
        <div className="card warning-card">
          <strong>⚠ Kaynak dosya değişmiş</strong>
          <p>{sourceChangedReason}</p>
          <p className="hint">
            Yayın bilgileri başka bir dosya için hazırlanmıştı. Bilgileri gözden geçirip
            kaydedin; kaydettiğinizde taslak yeni dosyaya bağlanır.
          </p>
        </div>
      )}

      {duplicateOf && (
        <div className="card warning-card">
          <strong>Bu dosya daha önce YouTube'a yüklenmiş.</strong>
          <p>
            {duplicateOf.title} —{' '}
            <a href={duplicateOf.videoUrl} target="_blank" rel="noreferrer">
              {duplicateOf.videoUrl}
            </a>{' '}
            ({formatDateTime(duplicateOf.uploadedAt)})
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={allowDuplicate}
              onChange={(e) => setAllowDuplicate(e.target.checked)}
            />
            Yine de yeni video olarak yükle
            <span className="hint">
              Açarsanız kanalınızda ikinci, ayrı bir video oluşur. Onay penceresinde bir kez
              daha sorulur.
            </span>
          </label>
        </div>
      )}

      {draft && selectedMedia && (
        <MetadataEditor
          draft={draft}
          media={selectedMedia}
          slug={slug}
          busy={busy}
          onEdit={editDraft}
          onRefill={() => void refillFromProject(slug)}
          onThumbnailFile={(file) => void attachThumbnail(slug, file)}
          onCaptionFile={(file) => void attachCaption(slug, file)}
        />
      )}

      <section className="publish-platforms">
        <h2>3. Platform seçin</h2>
        <YouTubePanel
          connection={connection}
          draft={draft}
          media={selectedMedia}
          job={job}
          event={event}
          busy={busy}
          blockedReason={blockedReason}
          onConnect={() => void connectYoutube()}
          onPublish={() => setConfirming(true)}
          onCancel={() => void cancel()}
          onRetry={(jobId) => void retry(jobId)}
        />

        {draft && (
          <>
            <InstagramPanel draft={draft} busy={busy} onEdit={editDraft} />
            <FacebookPanel draft={draft} busy={busy} onEdit={editDraft} />
            <TikTokPanel draft={draft} busy={busy} onEdit={editDraft} />
          </>
        )}
      </section>

      <PublishHistory
        history={history}
        busy={busy}
        onRefresh={(entryId) => void refreshHistoryEntry(slug, entryId)}
      />

      {confirming && draft && selectedMedia && (
        <ConfirmDialog
          title={scheduled ? "YouTube'a yükle ve planla" : "YouTube'a yükle"}
          confirmLabel={scheduled ? 'Yükle ve planla' : 'Yükle'}
          body={
            <div className="confirm-summary">
              <dl>
                <dt>Dosya</dt>
                <dd>{selectedMedia.filename}</dd>
                <dt>Başlık</dt>
                <dd>{draft.youtube.title}</dd>
                <dt>Gizlilik</dt>
                <dd>
                  {scheduled
                    ? 'Planlandığı ana kadar gizli'
                    : draft.youtube.privacyStatus === 'private'
                      ? 'Gizli'
                      : draft.youtube.privacyStatus === 'unlisted'
                        ? 'Liste dışı'
                        : 'Herkese açık'}
                </dd>
                <dt>Yayın zamanı</dt>
                <dd>
                  {scheduled
                    ? `${draft.youtube.publishAtLocal?.replace('T', ' ')} (İstanbul saati)`
                    : 'Hemen'}
                </dd>
                <dt>Kapak görseli</dt>
                <dd>{draft.youtube.thumbnailFile ? 'Var' : 'Yok'}</dd>
                <dt>Altyazı (.srt)</dt>
                <dd>
                  {draft.youtube.uploadCaptions && draft.youtube.captionFile ? 'Var' : 'Yok'}
                </dd>
                <dt>Abone bildirimi</dt>
                <dd>{draft.youtube.notifySubscribers ? 'Açık' : 'Kapalı'}</dd>
              </dl>
              {allowDuplicate && duplicateOf && (
                <p className="confirm-warning">
                  ⚠ Bu dosya daha önce yüklenmişti. Onaylarsanız kanalınızda ikinci bir video
                  oluşacak.
                </p>
              )}
            </div>
          }
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false)
            void publish(slug, allowDuplicate)
          }}
        />
      )}
    </div>
  )
}
