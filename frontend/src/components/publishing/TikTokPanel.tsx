/**
 * The TikTok card.
 *
 * It publishes for real, as its own backend job with its own duplicate
 * protection, so a failure here neither retries nor touches anything already
 * posted on YouTube.
 *
 * Three things the card is deliberate about:
 *
 * * **The account shown is the connected one.** Nothing typed on this page can
 *   redirect a post; the destination the card displays comes from the stored
 *   connection.
 * * **Nothing is offered that cannot happen.** The privacy options come from
 *   TikTok's own answer for this account, so an unaudited app shows only
 *   "Yalnızca ben" and says why.
 * * **Scheduling is absent, not broken.** TikTok's Direct Post cannot schedule,
 *   so the card says so rather than showing a picker that would publish
 *   immediately.
 */

import type {
  PublishDraft,
  PublishHistoryEntry,
  PublishJob,
  PublishJobEvent,
  SocialPlatform,
  TikTokConnection,
  TikTokDraft,
} from '@/api/publishing-types'
import {
  MAX_TIKTOK_TITLE_CHARS,
  composeCaption,
} from '@/api/publishing-types'
import { ErrorBox } from '@/components/ErrorBox'
import { formatBytes, formatDateTime } from '@/lib/format'
import { CountedTextarea, TagEditor } from './fields'

export interface PlatformCardProps {
  draft: PublishDraft
  busy: boolean
  onEdit: (mutate: (draft: PublishDraft) => void) => void
  /** The live job, but only when it belongs to *this* platform. */
  job: PublishJob | null
  event: PublishJobEvent | null
  duplicateOf: PublishHistoryEntry | undefined
  allowDuplicate: boolean
  onAllowDuplicate: (value: boolean) => void
  onPublish: () => void
  onCancel: () => void
  onRetry: (jobId: string) => void
  /** Blocks unrelated to the platform: no file selected, source changed, … */
  sharedBlockedReason: string | null
}

const PHASE_LABEL: Record<PublishJobEvent['phase'], string> = {
  validate: 'Bilgiler kontrol ediliyor',
  authenticate: 'Bağlantı doğrulanıyor',
  'hash-source': 'Dosya parmak izi hesaplanıyor',
  'upload-video': 'Video yükleniyor',
  'set-thumbnail': 'Kapak görseli konuluyor',
  'upload-captions': 'Altyazı gönderiliyor',
  'create-container': 'Platform videoyu alıyor',
  'await-processing': 'Platform videoyu işliyor',
  'fetch-status': 'Durum okunuyor',
  complete: 'Tamamlandı',
}

/** The shared frame: heading, connection state, form, actions, progress. */
function PlatformShell({
  platform,
  title,
  destination,
  connectionProblem,
  connectionSuggestion,
  blockedReason,
  buttonLabel,
  props,
  children,
}: {
  platform: SocialPlatform
  title: string
  destination: string | null
  connectionProblem: string | null
  connectionSuggestion: string | null
  blockedReason: string | null
  buttonLabel: string
  props: PlatformCardProps
  children: React.ReactNode
}) {
  const { job, event, busy, duplicateOf, allowDuplicate, onAllowDuplicate } = props
  const mine = job !== null && job.platform === platform
  const running = mine && (job.status === 'queued' || job.status === 'running')
  const percent = Math.round((event?.progress ?? (mine ? job.progress : 0)) * 100)
  const reason = blockedReason ?? props.sharedBlockedReason

  return (
    <section className={`card platform-card ${platform}`}>
      <div className="platform-head">
        <div>
          <h3>{title}</h3>
          <p className="muted">{destination ?? 'Bağlantı kurulmadı'}</p>
        </div>
        {destination && !connectionProblem && (
          <span className="status-pill status-completed">bağlı</span>
        )}
      </div>

      {connectionProblem && (
        <div className="platform-connect">
          <p>{connectionProblem}</p>
          {connectionSuggestion && <p className="hint">{connectionSuggestion}</p>}
          <p className="hint">
            Bağlantı ayarları Ayarlar → Bağlantılar ve servisler bölümünde.
          </p>
        </div>
      )}

      {children}

      {duplicateOf && (
        <div className="warning-card">
          <strong>Bu dosya daha önce {title}'a yüklenmiş.</strong>
          <p>
            {duplicateOf.videoUrl ? (
              <a href={duplicateOf.videoUrl} target="_blank" rel="noreferrer">
                {duplicateOf.title || duplicateOf.videoUrl}
              </a>
            ) : (
              duplicateOf.title
            )}{' '}
            ({formatDateTime(duplicateOf.uploadedAt)})
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={allowDuplicate}
              disabled={busy}
              onChange={(e) => onAllowDuplicate(e.target.checked)}
            />
            Yine de yeni gönderi olarak yükle
          </label>
        </div>
      )}

      {!running && (
        <div className="platform-actions">
          <button
            type="button"
            className="primary"
            onClick={props.onPublish}
            disabled={busy || reason !== null}
            title={reason ?? undefined}
          >
            {buttonLabel}
          </button>
          {reason && <span className="hint">{reason}</span>}
        </div>
      )}

      {running && (
        <div className="publish-progress">
          <div className="render-head">
            <h4>{PHASE_LABEL[event?.phase ?? job.phase]}</h4>
            <span className="status-pill status-running">yükleniyor</span>
          </div>
          <div className="progress-track" role="progressbar" aria-valuenow={percent}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <div className="render-meta">
            <span className="percent">{percent}%</span>
            <span>{event?.message ?? job.message}</span>
            <span className="spacer" />
            {(event?.totalBytes ?? job.totalBytes) > 0 && (
              <span>
                {formatBytes(event?.uploadedBytes ?? job.uploadedBytes)} /{' '}
                {formatBytes(event?.totalBytes ?? job.totalBytes)}
              </span>
            )}
          </div>
          <button type="button" className="danger" onClick={props.onCancel}>
            Yüklemeyi iptal et
          </button>
        </div>
      )}

      {mine && !running && job.status === 'completed' && (
        <div className="publish-result">
          <h4>{title} gönderisi yayınlandı</h4>
          <dl className="result-grid">
            <dt>Gönderi numarası</dt>
            <dd>
              <code>{job.videoId}</code>
            </dd>
            <dt>Bağlantı</dt>
            <dd>
              {job.videoUrl ? (
                <a href={job.videoUrl} target="_blank" rel="noreferrer">
                  {job.videoUrl}
                </a>
              ) : (
                // A self-only TikTok post has no public address, and inventing
                // one would be worse than saying so.
                <span className="muted">Herkese açık bir bağlantı yok.</span>
              )}
            </dd>
          </dl>
          {job.warnings.length > 0 && (
            <div className="warnings">
              {job.warnings.map((warning) => (
                <p key={warning}>⚠ {warning}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {mine && !running && job.status !== 'completed' && job.errorMessage && (
        <ErrorBox
          error={{
            code: job.errorCode ?? 'publishing_platform_unavailable',
            message: job.errorMessage,
            details: job.errorDetails,
            suggestion: job.errorSuggestion ?? 'Ayrıntılar için kayıt dosyasına bakın.',
            logPath: null,
            context: {},
          }}
          onRetry={() => props.onRetry(job.id)}
        />
      )}
    </section>
  )
}

const TIKTOK_PRIVACY_LABEL: Record<string, string> = {
  SELF_ONLY: 'Yalnızca ben',
  MUTUAL_FOLLOW_FRIENDS: 'Karşılıklı takipleşilen arkadaşlar',
  FOLLOWER_OF_CREATOR: 'Takipçiler',
  PUBLIC_TO_EVERYONE: 'Herkese açık',
}

export function TikTokPanel({
  tiktok,
  ...props
}: PlatformCardProps & { tiktok: TikTokConnection | null }) {
  const draft: TikTokDraft = props.draft.tiktok
  const edit = (mutate: (value: TikTokDraft) => void) =>
    props.onEdit((next) => mutate(next.tiktok))
  const info = tiktok?.creatorInfo ?? null
  // The list TikTok itself reported for this account. Empty until connected, and
  // never widened locally — offering an option TikTok would refuse is exactly
  // the kind of fake success this panel avoids.
  const options = info?.privacyLevelOptions ?? []

  const blocked = (() => {
    if (!tiktok || !tiktok.appConfigured) return 'TikTok uygulama bilgileri girilmedi.'
    if (!tiktok.tokenPresent) return 'TikTok hesabı bağlı değil.'
    if (!tiktok.scopesSufficient) return 'TikTok izinleri yetersiz; yeniden bağlanın.'
    if (tiktok.expired) return 'TikTok bağlantısının süresi dolmuş.'
    if (options.length > 0 && !options.includes(draft.privacy)) {
      return 'Seçili gizlilik bu hesap için kullanılamıyor.'
    }
    return null
  })()

  const composed = composeCaption(draft.caption, draft.hashtags)

  return (
    <PlatformShell
      platform="tiktok"
      title="TikTok"
      destination={tiktok?.displayName ?? null}
      connectionProblem={blocked}
      connectionSuggestion={tiktok?.suggestion ?? null}
      blockedReason={blocked}
      buttonLabel="TikTok'a gönder"
      props={props}
    >
      <CountedTextarea
        id="tiktok-caption"
        label="Başlık / açıklama"
        rows={4}
        value={draft.caption}
        used={composed.length}
        limit={MAX_TIKTOK_TITLE_CHARS}
        unit="karakter"
        disabled={props.busy}
        onChange={(next) => edit((value) => void (value.caption = next))}
      />

      <TagEditor
        id="tiktok-hashtags"
        label="Hashtagler"
        tags={draft.hashtags}
        disabled={props.busy}
        onChange={(hashtags) => edit((value) => void (value.hashtags = hashtags))}
      />

      {tiktok?.auditRequired && tiktok.tokenPresent && (
        <div className="warning-card">
          <strong>Uygulama TikTok denetiminden geçmedi.</strong>
          <p>
            Denetlenmemiş uygulamalar yalnızca “Yalnızca ben” gizliliğiyle gönderi
            oluşturabilir. Gönderi TikTok hesabınıza düşer, ama kimse göremez.
          </p>
          <p className="hint">
            Herkese açık paylaşım için TikTok Developer panelinden Content Posting API
            denetimine (audit) başvurmanız gerekir.
          </p>
        </div>
      )}

      <label className="publish-field" htmlFor="tiktok-privacy">
        <span className="publish-field-head">Gizlilik</span>
        <select
          id="tiktok-privacy"
          value={draft.privacy}
          disabled={props.busy || options.length === 0}
          onChange={(event) => edit((value) => void (value.privacy = event.target.value))}
        >
          {options.length === 0 ? (
            <option value={draft.privacy}>
              Hesap bağlanınca TikTok'un izin verdiği seçenekler listelenir
            </option>
          ) : (
            options.map((option) => (
              <option key={option} value={option}>
                {TIKTOK_PRIVACY_LABEL[option] ?? option}
              </option>
            ))
          )}
        </select>
        <span className="hint">Seçenekler TikTok'un bu hesap için bildirdikleridir.</span>
      </label>

      <div className="publish-flags">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.allowComments}
            disabled={props.busy || info?.commentDisabled === true}
            onChange={(event) => edit((value) => void (value.allowComments = event.target.checked))}
          />
          Yorumlara izin ver
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.allowDuet}
            disabled={props.busy || info?.duetDisabled === true}
            onChange={(event) => edit((value) => void (value.allowDuet = event.target.checked))}
          />
          Duet'e izin ver
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.allowStitch}
            disabled={props.busy || info?.stitchDisabled === true}
            onChange={(event) => edit((value) => void (value.allowStitch = event.target.checked))}
          />
          Stitch'e izin ver
        </label>
      </div>

      <p className="hint">
        Video doğrudan TikTok'a gönderilir; hiçbir yere kopyalanmaz. Planlama API üzerinden
        yapılamaz.
      </p>
    </PlatformShell>
  )
}
