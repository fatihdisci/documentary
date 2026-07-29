/**
 * The Meta connection, as the Settings page shows it.
 *
 * One connection, two destinations: the same grant publishes an Instagram Reel
 * and a Facebook Page Reel. Everything rendered here comes from the backend's
 * connection report, which by design contains **no App ID, no App Secret and no
 * access token** — only whether they exist, whether the grant still works, and
 * which Page and Instagram account it resolved to.
 *
 * The App ID and App Secret are entered once. After that the fields disappear
 * behind an explicit "change" tick, because a stray save from a half-filled
 * form would otherwise break a working connection — and there is no endpoint
 * that could read the old values back to restore it.
 *
 * "Disconnect" removes the stored authorization and nothing else: the App ID and
 * App Secret stay, so reconnecting is one click rather than a trip back to the
 * Meta panel.
 */

import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { MetaConnection } from '@/api/publishing-types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ErrorBox } from '@/components/ErrorBox'
import { formatDateTime } from '@/lib/format'

export function MetaConnectionCard() {
  const [connection, setConnection] = useState<MetaConnection | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [appId, setAppId] = useState('')
  const [appSecret, setAppSecret] = useState('')
  const [replacing, setReplacing] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const [copied, setCopied] = useState(false)

  const load = useCallback(async () => {
    try {
      setConnection(await api.metaStatus())
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function run(action: () => Promise<MetaConnection>) {
    setBusy(true)
    setError(null)
    try {
      setConnection(await action())
    } catch (err) {
      setError(describeError(err))
      await load()
    } finally {
      setBusy(false)
    }
  }

  async function saveCredentials() {
    await run(() =>
      api.saveMetaAppCredentials({ appId, appSecret, replace: replacing }),
    )
    // Clear the inputs whatever happened: the values must not sit in a DOM node
    // any longer than the request needed them.
    setAppId('')
    setAppSecret('')
    setReplacing(false)
  }

  /**
   * Open Meta's consent screen.
   *
   * The backend builds the URL (it is the only side that knows the App ID) and
   * receives the redirect; this page just opens the tab and re-reads the status
   * when the user comes back.
   */
  async function connect() {
    setBusy(true)
    setError(null)
    try {
      const start = await api.startMetaConnect()
      window.open(start.authorizationUrl, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const configured = connection?.appConfigured === true
  const showCredentialFields = !configured || replacing
  const connected = connection?.connected === true

  return (
    <div className="connection-block">
      <div className="connection-head">
        <div>
          <strong>Meta (Instagram + Facebook)</strong>
          <p className="muted">{connection?.statusMessage ?? 'Durum okunuyor…'}</p>
        </div>
      </div>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <dl className="connection-grid">
        <dt>Uygulama bilgileri</dt>
        <dd>{configured ? 'Kayıtlı' : 'Girilmedi'}</dd>
        <dt>Yetki (token)</dt>
        <dd>{connection?.tokenPresent ? 'Kayıtlı' : 'Yok'}</dd>
        <dt>Bağlantı</dt>
        <dd>
          {connected
            ? 'Geçerli'
            : connection?.needsReconnect
              ? 'Yeniden bağlanmalı'
              : 'Kurulmadı'}
        </dd>
        <dt>İzinler</dt>
        <dd>
          {connection?.scopesSufficient
            ? 'Yayınlama izinleri tam'
            : connection?.tokenPresent
              ? `Eksik: ${connection.missingScopes.join(', ')}`
              : '—'}
        </dd>
        <dt>Facebook Sayfası</dt>
        <dd>{connection?.pageName ?? '—'}</dd>
        <dt>Instagram hesabı</dt>
        <dd>
          {connection?.instagramUsername ? `@${connection.instagramUsername}` : '—'}
        </dd>
        <dt>Geçerlilik</dt>
        <dd>{connection?.expiresAt ? formatDateTime(connection.expiresAt) : '—'}</dd>
        <dt>Son kontrol</dt>
        <dd>{formatDateTime(connection?.checkedAt)}</dd>
      </dl>

      {connection?.problem && (
        <p className="connection-problem">
          ⚠ {connection.problem} {connection.suggestion}
        </p>
      )}

      <label className="publish-field" htmlFor="meta-redirect-uri">
        <span className="publish-field-head">OAuth callback adresi</span>
        <input id="meta-redirect-uri" readOnly value={connection?.redirectUri ?? ''} />
        <span className="hint">
          Meta Developer panelinde <strong>Facebook Login → Settings → Valid OAuth Redirect
          URIs</strong> alanına bu adresi olduğu gibi ekleyin.
        </span>
      </label>
      <div className="row">
        <button
          type="button"
          disabled={!connection?.redirectUri}
          onClick={() => {
            void navigator.clipboard?.writeText(connection?.redirectUri ?? '')
            setCopied(true)
          }}
        >
          Adresi kopyala
        </button>
        {copied && <span className="hint">Kopyalandı.</span>}
      </div>

      {showCredentialFields ? (
        <>
          <div className="field-grid">
            <label htmlFor="meta-app-id">
              App ID
              <input
                id="meta-app-id"
                value={appId}
                autoComplete="off"
                placeholder="1234567890123456"
                onChange={(event) => setAppId(event.target.value)}
              />
            </label>
            <label htmlFor="meta-app-secret">
              App Secret
              <input
                id="meta-app-secret"
                type="password"
                value={appSecret}
                autoComplete="off"
                placeholder="Meta panelinde “Show” diyerek kopyalayın"
                onChange={(event) => setAppSecret(event.target.value)}
              />
            </label>
          </div>
          <p className="hint">
            İkisi de yalnızca bu bilgisayarda, yalnızca sizin okuyabileceğiniz bir dosyada
            saklanır. Kaydedildikten sonra hiçbir ekranda, kayıt dosyasında veya API yanıtında
            görünmez — bu yüzden geri okunamaz, yalnızca değiştirilebilir.
          </p>
          <div className="row">
            <button
              type="button"
              className="primary"
              disabled={busy || !appId || !appSecret}
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

      {connection && connection.pages.length > 1 && (
        <label className="publish-field" htmlFor="meta-page">
          <span className="publish-field-head">Yayın yapılacak Facebook Sayfası</span>
          <select
            id="meta-page"
            value={connection.selectedPageId ?? ''}
            disabled={busy}
            onChange={(event) => void run(() => api.selectMetaPage(event.target.value))}
          >
            <option value="" disabled>
              Sayfa seçin
            </option>
            {connection.pages.map((page) => (
              <option key={page.pageId} value={page.pageId}>
                {page.name}
                {page.instagramUsername ? ` — @${page.instagramUsername}` : ' — Instagram yok'}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="row connection-actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !configured}
          onClick={() => void connect()}
        >
          {connection?.tokenPresent ? 'Yeniden bağlan' : "Meta'ya bağlan"}
        </button>
        <button type="button" disabled={busy} onClick={() => void load()}>
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
        <summary>Meta hesabı nasıl bağlanır?</summary>
        <ol>
          <li>
            Meta Developer panelinde uygulamanızı açın. Uygulama
            <strong> Development</strong> modunda kalabilir; yayına almanız gerekmez.
          </li>
          <li>
            <strong>Instagram → API setup with Facebook Login</strong> bölümünü kullanın ve
            Instagram profesyonel hesabınızın Facebook Sayfanıza bağlı olduğundan emin olun.
          </li>
          <li>
            Yukarıdaki callback adresini <strong>Valid OAuth Redirect URIs</strong> alanına
            ekleyip kaydedin.
          </li>
          <li>App ID ve App Secret değerlerini yukarıya bir kez girin.</li>
          <li>
            “Meta'ya bağlan” deyin, açılan sekmede hesabınızı seçip istenen izinlerin
            <em> hepsini</em> onaylayın.
          </li>
          <li>Birden fazla Sayfanız varsa hangisine yayınlanacağını buradan seçin.</li>
        </ol>
        <p className="hint">
          Instagram ve Facebook videoyu bir adresten kendileri indirir. Bu yüzden yayın
          yapabilmek için aşağıdaki “Geçici medya barındırma” bölümünün de tanımlı olması gerekir.
        </p>
      </details>

      {confirmingDisconnect && (
        <ConfirmDialog
          title="Meta bağlantısını kaldır"
          confirmLabel="Bağlantıyı kaldır"
          destructive
          body={
            <p>
              Kayıtlı yetki silinecek. App ID ve App Secret yerinde kalır, bu yüzden
              istediğinizde tek tıkla yeniden bağlanabilirsiniz. Instagram ve Facebook'taki
              gönderilerinize hiçbir şey olmaz.
            </p>
          }
          onCancel={() => setConfirmingDisconnect(false)}
          onConfirm={() => {
            setConfirmingDisconnect(false)
            void run(() => api.disconnectMeta())
          }}
        />
      )}
    </div>
  )
}
