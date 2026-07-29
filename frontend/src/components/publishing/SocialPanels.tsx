/**
 * The Instagram, Facebook and TikTok cards.
 *
 * These publish for real. Each one is its own backend job with its own
 * duplicate protection, so a failure on one platform neither retries nor
 * touches anything already posted on another.
 *
 * Three things the cards are deliberate about:
 *
 * * **The account shown is the connected one.** The "hesap" field in the form is
 *   the user's own note and authorizes nothing; what the card displays as the
 *   destination comes from the stored connection.
 * * **Nothing is offered that cannot happen.** Instagram and Facebook need the
 *   temporary hosting layer, so without it the button is disabled and says why.
 *   TikTok's privacy options come from TikTok's own answer for this account, so
 *   an unaudited app shows only "Yalnızca ben" and says why.
 * * **Scheduling is absent, not broken.** Neither Meta's Reels APIs nor TikTok's
 *   Direct Post can schedule, so the card says so rather than showing a picker
 *   that would publish immediately.
 */

import type {
  MediaHostStatus,
  MetaConnection,
  PublishDraft,
  PublishHistoryEntry,
  PublishJob,
  PublishJobEvent,
  SocialDraft,
  SocialPlatform,
  TikTokConnection,
  TikTokDraft,
} from '@/api/publishing-types'
import {
  MAX_FACEBOOK_DESCRIPTION_CHARS,
  MAX_INSTAGRAM_CAPTION_CHARS,
  MAX_INSTAGRAM_HASHTAGS,
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
  'host-media': 'Video geçici adrese yükleniyor',
  'create-container': 'Platform videoyu alıyor',
  'await-processing': 'Platform videoyu işliyor',
  'publish-post': 'Gönderi yayınlanıyor',
  cleanup: 'Geçici kopya siliniyor',
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

/** Caption + hashtags + the user's own account note, shared by all three. */
function SocialFields({
  idPrefix,
  value,
  captionLabel,
  captionLimit,
  accountLabel,
  accountPlaceholder,
  busy,
  onChange,
}: {
  idPrefix: string
  value: SocialDraft
  captionLabel: string
  captionLimit: number
  accountLabel: string
  accountPlaceholder: string
  busy: boolean
  onChange: (mutate: (draft: SocialDraft) => void) => void
}) {
  // The counter measures what is actually sent — caption *and* hashtags —
  // because that is the string the platform's limit applies to.
  const composed = composeCaption(value.caption, value.hashtags)
  return (
    <>
      <CountedTextarea
        id={`${idPrefix}-caption`}
        label={captionLabel}
        rows={5}
        value={value.caption}
        used={composed.length}
        limit={captionLimit}
        unit="karakter"
        disabled={busy}
        hint="Sayaç hashtagler dahil gönderilecek metni ölçer."
        onChange={(next) => onChange((draft) => void (draft.caption = next))}
      />

      <TagEditor
        id={`${idPrefix}-hashtags`}
        label="Hashtagler"
        tags={value.hashtags}
        disabled={busy}
        onChange={(hashtags) => onChange((draft) => void (draft.hashtags = hashtags))}
      />

      <label className="publish-field" htmlFor={`${idPrefix}-account`}>
        <span className="publish-field-head">{accountLabel}</span>
        <input
          id={`${idPrefix}-account`}
          value={value.account}
          placeholder={accountPlaceholder}
          disabled={busy}
          onChange={(event) => onChange((draft) => void (draft.account = event.target.value))}
        />
        <span className="hint">
          Yalnızca kendi notunuz. Gönderi her zaman bağlı hesaba gider.
        </span>
      </label>
    </>
  )
}

/** Why Instagram or Facebook cannot publish right now, or `null`. */
function metaBlockedReason(
  meta: MetaConnection | null,
  host: MediaHostStatus | null,
  platform: 'instagram' | 'facebook',
): string | null {
  if (!meta || !meta.appConfigured) return 'Meta uygulama bilgileri girilmedi.'
  if (!meta.tokenPresent) return 'Meta hesabı bağlı değil.'
  if (!meta.scopesSufficient) return 'Meta izinleri yetersiz; yeniden bağlanın.'
  if (meta.expired) return 'Meta bağlantısının süresi dolmuş.'
  if (!meta.selectedPageId) return 'Yayın yapılacak Facebook Sayfası seçilmedi.'
  if (platform === 'instagram' && !meta.instagramId) {
    return 'Bu sayfaya bağlı bir Instagram profesyonel hesabı yok.'
  }
  if (!host?.configured) {
    return 'Geçici medya barındırma tanımlı değil; Meta videoyu bir adresten indirir.'
  }
  return null
}

export function InstagramPanel({
  meta,
  mediaHost,
  ...props
}: PlatformCardProps & { meta: MetaConnection | null; mediaHost: MediaHostStatus | null }) {
  const blocked = metaBlockedReason(meta, mediaHost, 'instagram')
  const destination = meta?.instagramUsername ? `@${meta.instagramUsername}` : null

  return (
    <PlatformShell
      platform="instagram"
      title="Instagram"
      destination={destination}
      connectionProblem={blocked}
      connectionSuggestion={meta?.suggestion ?? mediaHost?.suggestion ?? null}
      blockedReason={blocked}
      buttonLabel="Instagram Reels olarak yayınla"
      props={props}
    >
      <SocialFields
        idPrefix="instagram"
        value={props.draft.instagram}
        captionLabel="Reels açıklaması"
        captionLimit={MAX_INSTAGRAM_CAPTION_CHARS}
        accountLabel="Instagram hesabı (not)"
        accountPlaceholder="@hesap"
        busy={props.busy}
        onChange={(mutate) => props.onEdit((next) => mutate(next.instagram))}
      />

      {props.draft.instagram.hashtags.length > MAX_INSTAGRAM_HASHTAGS && (
        <p className="hint">
          ⚠ {props.draft.instagram.hashtags.length} hashtag var; Instagram en fazla{' '}
          {MAX_INSTAGRAM_HASHTAGS} tanesini kabul eder.
        </p>
      )}

      <label className="checkbox">
        <input
          type="checkbox"
          checked={props.draft.instagram.shareToFeed}
          disabled={props.busy}
          onChange={(event) =>
            props.onEdit((next) => void (next.instagram.shareToFeed = event.target.checked))
          }
        />
        Profil akışında da göster
      </label>

      <p className="hint">
        Instagram API'si ileri tarihe planlamayı desteklemez; gönderi onayladığınız anda
        yayınlanır. Video, Meta'nın indirebilmesi için geçici ve süreli bir adrese konur, sonra
        oradan silinir.
      </p>
    </PlatformShell>
  )
}

export function FacebookPanel({
  meta,
  mediaHost,
  ...props
}: PlatformCardProps & { meta: MetaConnection | null; mediaHost: MediaHostStatus | null }) {
  const blocked = metaBlockedReason(meta, mediaHost, 'facebook')

  return (
    <PlatformShell
      platform="facebook"
      title="Facebook"
      destination={meta?.pageName ?? null}
      connectionProblem={blocked}
      connectionSuggestion={meta?.suggestion ?? mediaHost?.suggestion ?? null}
      blockedReason={blocked}
      buttonLabel="Facebook Reels olarak yayınla"
      props={props}
    >
      <SocialFields
        idPrefix="facebook"
        value={props.draft.facebook}
        captionLabel="Reels açıklaması"
        captionLimit={MAX_FACEBOOK_DESCRIPTION_CHARS}
        accountLabel="Facebook Sayfası (not)"
        accountPlaceholder="Sayfa adı"
        busy={props.busy}
        onChange={(mutate) => props.onEdit((next) => mutate(next.facebook))}
      />
      <p className="hint">
        Gönderi bağlı Sayfaya Reel olarak eklenir. Planlama API üzerinden yapılamaz; onay
        verdiğinizde yayınlanır.
      </p>
    </PlatformShell>
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
