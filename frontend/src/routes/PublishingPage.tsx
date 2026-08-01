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
import { TikTokPanel } from '@/components/publishing/TikTokPanel'
import { YouTubePanel } from '@/components/publishing/YouTubePanel'
import { MAX_TITLE_CHARS, composeCaption } from '@/api/publishing-types'
import type { SocialPlatform } from '@/api/publishing-types'
import { useProjectStore } from '@/store/project'
import { flushPendingDraftSave, usePublishingStore } from '@/store/publishing'
import { formatDateTime } from '@/lib/format'
import './PublishingPage.css'

const SAVE_LABEL: Record<string, string> = {
  idle: '',
  dirty: 'Kaydedilmedi',
  saving: 'Kaydediliyor…',
  saved: 'Kaydedildi',
  error: 'Kaydedilemedi — tekrar denenecek',
}

/** Per-platform "publish anyway" ticks. One file, independent decisions. */
type OverrideMap = Partial<Record<SocialPlatform, boolean>>

export function PublishingPage() {
  const { project } = useProjectStore()
  const {
    media, selectedMediaId, draft, selectedMedia, sourceChanged, sourceChangedReason,
    duplicateOf, duplicates, connection, tiktok, history, job, event,
    loading, busy, saveStatus, error,
    loadConnection, loadPlatformConnections, connectYoutube, loadMedia, selectMedia,
    editDraft, refillFromProject, attachThumbnail, attachCaption, publish,
    publishToPlatform, cancel, retry, detach, reattachIfRunning,
    loadHistory, refreshHistoryEntry, clearError,
  } = usePublishingStore()

  const [showAll, setShowAll] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [allowDuplicate, setAllowDuplicate] = useState(false)
  const [socialOverrides, setSocialOverrides] = useState<OverrideMap>({})
  const [confirmingPlatform, setConfirmingPlatform] = useState<SocialPlatform | null>(null)
  const slug = project?.slug ?? null

  useEffect(() => {
    if (!slug) return
    void loadConnection(false)
    void loadPlatformConnections()
    void loadMedia(slug)
    void loadHistory(slug)
    void reattachIfRunning(slug)
    return () => {
      // Leaving the panel must not cost the user the last thing they typed:
      // the request goes out before `detach` cancels the debounce behind it.
      void flushPendingDraftSave()
      detach()
    }
  }, [
    slug, loadConnection, loadPlatformConnections, loadMedia, loadHistory,
    reattachIfRunning, detach,
  ])

  // A different file means a different draft, so every duplicate override must
  // be re-decided rather than carried over.
  useEffect(() => {
    setAllowDuplicate(false)
    setSocialOverrides({})
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
  const selectedPlan = project.shortsPlan?.shorts.find(
    (item) => item.id === selectedMedia?.contentPlanId,
  )
  const blockedReason = (() => {
    if (!draft || !selectedMedia) return 'Önce bir video seçin.'
    if (sourceChanged) return 'Seçili dosya değişmiş; bilgileri gözden geçirin.'
    if (!draft.youtube.title.trim()) return 'Başlık boş olamaz.'
    if (draft.youtube.title.length > MAX_TITLE_CHARS) return 'Başlık çok uzun.'
    if (scheduled && !draft.youtube.publishAtLocal) return 'Planlanan tarihi seçin.'
    if (duplicateOf && !allowDuplicate) return 'Bu dosya daha önce yüklenmiş.'
    return null
  })()

  /** What stops *any* platform, before its own connection is considered. */
  const sharedBlockedReason = (() => {
    if (!draft || !selectedMedia) return 'Önce bir video seçin.'
    if (sourceChanged) return 'Seçili dosya değişmiş; bilgileri gözden geçirin.'
    return null
  })()

  function platformProps(platform: SocialPlatform) {
    if (!draft || !slug) return null
    const duplicate = duplicates[platform]
    const override = socialOverrides[platform] === true
    return {
      draft,
      busy,
      onEdit: editDraft,
      job,
      event: event && job?.platform === platform ? event : null,
      duplicateOf: duplicate,
      allowDuplicate: override,
      onAllowDuplicate: (value: boolean) =>
        setSocialOverrides((current) => ({ ...current, [platform]: value })),
      onPublish: () => setConfirmingPlatform(platform),
      onCancel: () => void cancel(),
      onRetry: (jobId: string) => void retry(jobId),
      sharedBlockedReason:
        duplicate && !override
          ? 'Bu dosya bu platforma daha önce yüklenmiş.'
          : sharedBlockedReason,
    }
  }

  const tiktokProps = platformProps('tiktok')

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
        <>
          {selectedPlan && (
            <section className="card planned-publish-card">
              <strong>JSON’daki Shorts planı uygulandı</strong>
              <p>
                {selectedPlan.sections
                  .map((section) =>
                    section.kind === 'scene'
                      ? `${section.number}. sahne`
                      : section.kind === 'intro'
                        ? 'Intro'
                        : 'Outro',
                  )
                  .join(' → ')}
                {selectedPlan.purpose ? ` — ${selectedPlan.purpose}` : ''}
              </p>
              {selectedPlan.youtube.alternativeTitles.length > 0 && (
                <p className="hint">
                  Alternatif başlıklar: {selectedPlan.youtube.alternativeTitles.join(' · ')}
                </p>
              )}
              {selectedPlan.youtube.pinnedComment && (
                <p className="hint">
                  <strong>Sabit yorum:</strong> {selectedPlan.youtube.pinnedComment}
                </p>
              )}
              {draft.youtube.description.includes('FULL_VIDEO_URL') && (
                <p className="connection-problem">
                  ⚠ Uzun video henüz YouTube yayın geçmişinde bulunamadı. Yayınlamadan önce
                  FULL_VIDEO_URL yer tutucusunu gerçek bağlantıyla değiştirin.
                </p>
              )}
            </section>
          )}
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
        </>
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

        {tiktokProps && <TikTokPanel {...tiktokProps} tiktok={tiktok} />}
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

      {confirmingPlatform && draft && selectedMedia && (
        <ConfirmDialog
          title={`${PLATFORM_LABEL[confirmingPlatform]} üzerinde yayınla`}
          confirmLabel="Yayınla"
          body={
            <div className="confirm-summary">
              <dl>
                <dt>Dosya</dt>
                <dd>{selectedMedia.filename}</dd>
                <dt>Hesap</dt>
                <dd>{platformDestination(confirmingPlatform)}</dd>
                <dt>Metin</dt>
                <dd className="confirm-caption">
                  {composeCaption(
                    draft[confirmingPlatform].caption,
                    draft[confirmingPlatform].hashtags,
                  ) || '(boş)'}
                </dd>
                <dt>Gizlilik</dt>
                <dd>{draft.tiktok.privacy}</dd>
              </dl>
              {tiktok?.auditRequired && (
                <p className="confirm-warning">
                  ⚠ Uygulama TikTok denetiminden geçmediği için gönderiyi yalnızca siz
                  görebileceksiniz.
                </p>
              )}
              {socialOverrides[confirmingPlatform] && duplicates[confirmingPlatform] && (
                <p className="confirm-warning">
                  ⚠ Bu dosya bu platforma daha önce yüklenmişti. Onaylarsanız ikinci bir
                  gönderi oluşur.
                </p>
              )}
            </div>
          }
          onCancel={() => setConfirmingPlatform(null)}
          onConfirm={() => {
            const platform = confirmingPlatform
            setConfirmingPlatform(null)
            void publishToPlatform(slug, platform, socialOverrides[platform] === true)
          }}
        />
      )}
    </div>
  )

  function platformDestination(_platform: SocialPlatform): string {
    return tiktok?.displayName ?? 'bağlı TikTok hesabı'
  }
}

const PLATFORM_LABEL: Record<SocialPlatform, string> = {
  tiktok: 'TikTok',
}
