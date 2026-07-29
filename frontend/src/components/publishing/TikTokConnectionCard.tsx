/**
 * The TikTok connection, as the Settings page shows it.
 *
 * The one thing this card refuses to be vague about is the **audit**. Until a
 * TikTok app passes the Content Posting API audit, everything it posts is
 * visible only to the creator. That is stated here, in the card, before anyone
 * spends time on a video — not discovered later from an error code.
 *
 * The second thing it is honest about is the redirect URI. TikTok requires
 * HTTPS, which a loopback backend cannot provide on its own, so the field is
 * editable and the limitation is spelled out rather than papered over.
 *
 * As everywhere else: the Client Key and Client Secret are write-only. No
 * endpoint returns either, so they can be replaced but never read back.
 */

import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { TikTokConnection } from '@/api/publishing-types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ErrorBox } from '@/components/ErrorBox'
import { formatDateTime } from '@/lib/format'

export function TikTokConnectionCard() {
  const [connection, setConnection] = useState<TikTokConnection | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [clientKey, setClientKey] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [replacing, setReplacing] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)

  const load = useCallback(async (refresh = false) => {
    try {
      setConnection(await api.tiktokStatus(refresh))
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  useEffect(() => {
    void load(false)
  }, [load])

  async function run(action: () => Promise<TikTokConnection>) {
    setBusy(true)
    setError(null)
    try {
      setConnection(await action())
    } catch (err) {
      setError(describeError(err))
      await load(false)
    } finally {
      setBusy(false)
    }
  }

  async function saveCredentials() {
    await run(() =>
      api.saveTiktokAppCredentials({ clientKey, clientSecret, replace: replacing }),
    )
    setClientKey('')
    setClientSecret('')
    setReplacing(false)
  }

  async function connect() {
    setBusy(true)
    setError(null)
    try {
      const start = await api.startTiktokConnect()
      window.open(start.authorizationUrl, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const configured = connection?.appConfigured === true
  const showCredentialFields = !configured || replacing
  const info = connection?.creatorInfo ?? null

  return (
    <div className="connection-block">
      <div className="connection-head">
        <div>
          <strong>TikTok</strong>
          <p className="muted">{connection?.statusMessage ?? 'Durum okunuyor…'}</p>
        </div>
        {connection?.avatarUrl && connection.connected && (
          <img className="channel-avatar" src={connection.avatarUrl} alt="" />
        )}
      </div>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <dl className="connection-grid">
        <dt>Uygulama bilgileri</dt>
        <dd>{configured ? 'Kayıtlı' : 'Girilmedi'}</dd>
        <dt>Yetki (token)</dt>
        <dd>{connection?.tokenPresent ? 'Kayıtlı' : 'Yok'}</dd>
        <dt>Bağlantı</dt>
        <dd>
          {connection?.connected
            ? 'Geçerli'
            : connection?.needsReconnect
              ? 'Yeniden bağlanmalı'
              : 'Kurulmadı'}
        </dd>
        <dt>Hesap</dt>
        <dd>{connection?.displayName ?? '—'}</dd>
        <dt>Paylaşım izni</dt>
        <dd>
          {!connection?.connected
            ? '—'
            : connection.auditRequired
              ? 'Yalnızca “Yalnızca ben” (denetim bekliyor)'
              : (info?.privacyLevelOptions.join(', ') ?? 'Bilinmiyor')}
        </dd>
        <dt>Geçerlilik</dt>
        <dd>{connection?.expiresAt ? formatDateTime(connection.expiresAt) : '—'}</dd>
        <dt>Son kontrol</dt>
        <dd>{formatDateTime(connection?.checkedAt)}</dd>
      </dl>

      {connection?.connected && connection.auditRequired && (
        <p className="connection-problem">
          ⚠ Uygulamanız TikTok denetiminden (Content Posting API audit) geçmedi. Bu durumda
          gönderiler yalnızca sizin görebileceğiniz şekilde paylaşılır — bu bir uygulama
          kısıtı değil, TikTok'un kuralıdır. Program bu yüzden panelde herkese açık paylaşım
          seçeneği sunmaz.
        </p>
      )}

      {connection?.problem && !connection.auditRequired && (
        <p className="connection-problem">
          ⚠ {connection.problem} {connection.suggestion}
        </p>
      )}

      <label className="publish-field" htmlFor="tiktok-redirect-uri">
        <span className="publish-field-head">OAuth callback adresi</span>
        <input id="tiktok-redirect-uri" readOnly value={connection?.redirectUri ?? ''} />
        <span className="hint">
          TikTok Developer panelinde <strong>Login Kit → Redirect URI</strong> alanına birebir
          aynısını ekleyin. TikTok yalnızca <strong>https</strong> adres kabul eder; yerel
          adresle bağlanmak için bu adresi HTTPS bir tünelin arkasına almanız gerekir. Adresi
          değiştirdiyseniz Ayarlar → <code>tiktokRedirectUri</code> alanından güncelleyin.
        </span>
      </label>

      {showCredentialFields ? (
        <>
          <div className="field-grid">
            <label htmlFor="tiktok-client-key">
              Client Key
              <input
                id="tiktok-client-key"
                value={clientKey}
                autoComplete="off"
                onChange={(event) => setClientKey(event.target.value)}
              />
            </label>
            <label htmlFor="tiktok-client-secret">
              Client Secret
              <input
                id="tiktok-client-secret"
                type="password"
                value={clientSecret}
                autoComplete="off"
                onChange={(event) => setClientSecret(event.target.value)}
              />
            </label>
          </div>
          <p className="hint">
            Yalnızca bu bilgisayarda, yalnızca sizin okuyabileceğiniz bir dosyada saklanır ve
            hiçbir ekranda geri gösterilmez.
          </p>
          <div className="row">
            <button
              type="button"
              className="primary"
              disabled={busy || !clientKey || !clientSecret}
              onClick={() => void saveCredentials()}
            >
              Uygulama bilgilerini kaydet
            </button>
            {replacing && (
              <button type="button" onClick={() => setReplacing(false)} disabled={busy}>
                Vazgeç
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="row">
          <button type="button" onClick={() => setReplacing(true)} disabled={busy}>
            Kimlik bilgilerini değiştir
          </button>
        </div>
      )}

      <div className="row connection-actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !configured}
          onClick={() => void connect()}
        >
          {connection?.tokenPresent ? 'Yeniden bağlan' : "TikTok'a bağlan"}
        </button>
        <button type="button" disabled={busy} onClick={() => void load(true)}>
          Durumu yenile
        </button>
        {connection?.tokenPresent && (
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => setConfirmingDisconnect(true)}
          >
            Bağlantıyı kaldır
          </button>
        )}
      </div>

      <details className="connection-setup">
        <summary>TikTok hesabı nasıl bağlanır?</summary>
        <ol>
          <li>TikTok for Developers'da bir uygulama oluşturun.</li>
          <li>
            <strong>Login Kit</strong> ve <strong>Content Posting API</strong> ürünlerini
            ekleyin, <code>user.info.basic</code> ve <code>video.publish</code> izinlerini
            isteyin.
          </li>
          <li>Yukarıdaki callback adresini Redirect URI olarak ekleyin (https zorunlu).</li>
          <li>Client Key ve Client Secret değerlerini yukarıya girin.</li>
          <li>“TikTok'a bağlan” deyip açılan sekmede izin verin.</li>
          <li>
            Herkese açık paylaşım için <strong>Content Posting API audit</strong> başvurusu
            yapın. Onaylanana kadar gönderiler yalnızca size görünür.
          </li>
        </ol>
      </details>

      {confirmingDisconnect && (
        <ConfirmDialog
          title="TikTok bağlantısını kaldır"
          confirmLabel="Bağlantıyı kaldır"
          destructive
          body={
            <p>
              Kayıtlı yetki silinecek. Client Key ve Client Secret yerinde kalır.
              TikTok'taki gönderilerinize hiçbir şey olmaz.
            </p>
          }
          onCancel={() => setConfirmingDisconnect(false)}
          onConfirm={() => {
            setConfirmingDisconnect(false)
            void run(() => api.disconnectTiktok())
          }}
        />
      )}
    </div>
  )
}
