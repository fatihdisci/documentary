/**
 * Instagram, Facebook and TikTok cards.
 *
 * These are **UI only**. Their fields are real — what the user types is stored
 * in the same draft as everything else and will still be there when the
 * integrations arrive — but nothing here talks to a network, no SDK is loaded,
 * no token is asked for, and the publish buttons are disabled rather than
 * pretending to work.
 */

import type { PublishDraft, SocialDraft, TikTokDraft } from '@/api/publishing-types'
import { SchedulePicker, TagEditor } from './fields'

const NOT_CONNECTED = 'Bağlantı kurulmadı'

interface SharedProps {
  draft: PublishDraft
  busy: boolean
  onEdit: (mutate: (draft: PublishDraft) => void) => void
}

function PlatformShell({
  title, buttonLabel, children,
}: {
  title: string
  buttonLabel: string
  children: React.ReactNode
}) {
  return (
    <section className="card platform-card">
      <div className="platform-head">
        <div>
          <h3>{title}</h3>
          <p className="muted">{NOT_CONNECTED}</p>
        </div>
        <span className="status-pill status-queued">yakında</span>
      </div>
      {children}
      <div className="platform-actions">
        <button type="button" className="primary" disabled title="Bu platform henüz bağlanamıyor">
          {buttonLabel}
        </button>
        <span className="hint">
          Bu bölüm şimdilik yalnızca arayüz. Yazdıklarınız taslakta saklanır; hiçbir istek
          gönderilmez.
        </span>
      </div>
    </section>
  )
}

/** The caption + hashtags + account + schedule block all three share. */
function SocialFields({
  idPrefix, value, accountLabel, accountPlaceholder, busy, onChange,
}: {
  idPrefix: string
  value: SocialDraft
  accountLabel: string
  accountPlaceholder: string
  busy: boolean
  onChange: (mutate: (draft: SocialDraft) => void) => void
}) {
  return (
    <>
      <label className="publish-field" htmlFor={`${idPrefix}-caption`}>
        <span className="publish-field-head">Reels açıklaması</span>
        <textarea
          id={`${idPrefix}-caption`}
          rows={4}
          value={value.caption}
          disabled={busy}
          onChange={(event) => onChange((draft) => void (draft.caption = event.target.value))}
        />
      </label>

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
      </label>

      <SchedulePicker
        idPrefix={idPrefix}
        mode={value.publishMode}
        value={value.publishAtLocal}
        disabled={busy}
        onModeChange={(mode) => onChange((draft) => void (draft.publishMode = mode))}
        onValueChange={(next) => onChange((draft) => void (draft.publishAtLocal = next))}
      />
    </>
  )
}

export function InstagramPanel({ draft, busy, onEdit }: SharedProps) {
  return (
    <PlatformShell title="Instagram" buttonLabel="Instagram bağlantısı yakında">
      <SocialFields
        idPrefix="instagram"
        value={draft.instagram}
        accountLabel="Instagram hesabı"
        accountPlaceholder="@hesap"
        busy={busy}
        onChange={(mutate) => onEdit((next) => mutate(next.instagram))}
      />
    </PlatformShell>
  )
}

export function FacebookPanel({ draft, busy, onEdit }: SharedProps) {
  return (
    <PlatformShell title="Facebook" buttonLabel="Facebook bağlantısı yakında">
      <SocialFields
        idPrefix="facebook"
        value={draft.facebook}
        accountLabel="Facebook Sayfası"
        accountPlaceholder="Sayfa adı"
        busy={busy}
        onChange={(mutate) => onEdit((next) => mutate(next.facebook))}
      />
    </PlatformShell>
  )
}

export function TikTokPanel({ draft, busy, onEdit }: SharedProps) {
  const tiktok: TikTokDraft = draft.tiktok
  const edit = (mutate: (value: TikTokDraft) => void) => onEdit((next) => mutate(next.tiktok))

  return (
    <PlatformShell title="TikTok" buttonLabel="TikTok bağlantısı yakında">
      <label className="publish-field" htmlFor="tiktok-caption">
        <span className="publish-field-head">Başlık / açıklama</span>
        <textarea
          id="tiktok-caption"
          rows={4}
          value={tiktok.caption}
          disabled={busy}
          onChange={(event) => edit((value) => void (value.caption = event.target.value))}
        />
      </label>

      <TagEditor
        id="tiktok-hashtags"
        label="Hashtagler"
        tags={tiktok.hashtags}
        disabled={busy}
        onChange={(hashtags) => edit((value) => void (value.hashtags = hashtags))}
      />

      <label className="publish-field" htmlFor="tiktok-privacy">
        <span className="publish-field-head">Gizlilik</span>
        <select
          id="tiktok-privacy"
          value={tiktok.privacy}
          disabled={busy}
          onChange={(event) => edit((value) => void (value.privacy = event.target.value))}
        >
          <option value="private">Yalnızca ben</option>
          <option value="friends">Arkadaşlar</option>
          <option value="public">Herkese açık</option>
        </select>
      </label>

      <div className="publish-flags">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={tiktok.allowComments}
            disabled={busy}
            onChange={(event) => edit((value) => void (value.allowComments = event.target.checked))}
          />
          Yorumlara izin ver
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={tiktok.allowDuet}
            disabled={busy}
            onChange={(event) => edit((value) => void (value.allowDuet = event.target.checked))}
          />
          Duet'e izin ver
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={tiktok.allowStitch}
            disabled={busy}
            onChange={(event) => edit((value) => void (value.allowStitch = event.target.checked))}
          />
          Stitch'e izin ver
        </label>
      </div>

      <SchedulePicker
        idPrefix="tiktok"
        mode={tiktok.publishMode}
        value={tiktok.publishAtLocal}
        disabled={busy}
        onModeChange={(mode) => edit((value) => void (value.publishMode = mode))}
        onValueChange={(next) => edit((value) => void (value.publishAtLocal = next))}
      />
    </PlatformShell>
  )
}
