/**
 * The YouTube connection, as the Settings page shows it.
 *
 * Everything here comes from the backend's connection report, which by design
 * contains no client id, no client secret and no token — only whether the files
 * are there, whether the grant still works, and which channel it belongs to.
 *
 * "Disconnect" removes the stored authorization and nothing else: the OAuth
 * client file the user installed stays where it is, so reconnecting is one
 * click rather than a fresh trip to Google Cloud.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { YouTubeConnection } from '@/api/publishing-types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ErrorBox } from '@/components/ErrorBox'
import { formatDateTime } from '@/lib/format'

export function YouTubeConnectionCard() {
  const [connection, setConnection] = useState<YouTubeConnection | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(async (refresh = false) => {
    try {
      setConnection(await api.youtubeStatus(refresh))
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  useEffect(() => {
    void load(false)
  }, [load])

  async function run(action: () => Promise<YouTubeConnection>) {
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

  async function chooseClientFile(file: File) {
    setBusy(true)
    setError(null)
    try {
      const response = await api.uploadYoutubeClientSecret(file)
      setConnection(response.connection)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  const connected = connection?.connected === true && connection.scopesSufficient

  return (
    <div className="connection-block">
      <div className="connection-head">
        <div>
          <strong>YouTube</strong>
          <p className="muted">{connection?.statusMessage ?? 'Durum okunuyor…'}</p>
        </div>
        {connected && connection?.channelThumbnailUrl && (
          <img className="channel-avatar" src={connection.channelThumbnailUrl} alt="" />
        )}
      </div>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <dl className="connection-grid">
        <dt>OAuth istemci dosyası</dt>
        <dd>
          {connection?.clientFilePresent
            ? `Bulundu — ${connection.clientFileName}`
            : 'Bulunamadı'}
        </dd>
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
            ? 'Yükleme, altyazı ve okuma izinleri tam'
            : connection?.tokenPresent
              ? 'Yetersiz — altyazı yükleme izni eksik'
              : '—'}
        </dd>
        <dt>Kanal</dt>
        <dd>{connection?.channelTitle ?? '—'}</dd>
        <dt>Kanal ID</dt>
        <dd>{connection?.channelId ? <code>{connection.channelId}</code> : '—'}</dd>
        <dt>Son kontrol</dt>
        <dd>{formatDateTime(connection?.checkedAt)}</dd>
      </dl>

      {connection?.problem && (
        <p className="connection-problem">
          ⚠ {connection.problem} {connection.suggestion}
        </p>
      )}

      {connection && connection.availableClientFiles.length > 1 && (
        <label className="publish-field" htmlFor="youtube-client-file">
          <span className="publish-field-head">Kullanılacak OAuth istemci dosyası</span>
          <select
            id="youtube-client-file"
            value={connection.clientFileName ?? ''}
            disabled={busy}
            onChange={(event) =>
              void run(() => api.selectYoutubeClientSecret(event.target.value))
            }
          >
            {connection.availableClientFiles.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}

      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        className="visually-hidden"
        aria-label="OAuth istemci dosyası seç"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void chooseClientFile(file)
          event.target.value = ''
        }}
      />

      <div className="row connection-actions">
        <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}>
          OAuth istemci dosyası seç
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || !connection?.clientFilePresent}
          onClick={() => void run(() => api.connectYoutube())}
        >
          {connection?.tokenPresent ? 'Yeniden bağlan' : "YouTube'a bağlan"}
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
        <summary>YouTube hesabı nasıl bağlanır?</summary>
        <ol>
          <li>Google Cloud Console'da YouTube Data API v3'ü etkinleştirin.</li>
          <li>OAuth consent screen'i (izin ekranını) hazırlayın ve kendi hesabınızı test
            kullanıcısı olarak ekleyin.</li>
          <li>“OAuth Client ID” oluştururken tür olarak <strong>Desktop app</strong> seçin.</li>
          <li>İndirilen JSON dosyasını yukarıdaki “OAuth istemci dosyası seç” ile ekleyin ya da
            <code>~/ExtinctVideoBuilder/secrets/</code> klasörüne koyun.</li>
          <li>“YouTube'a bağlan” düğmesine basın.</li>
          <li>Açılan tarayıcıda doğru YouTube hesabını seçip izin verin.</li>
        </ol>
        <p className="hint">
          API anahtarı ile video yüklenemez; YouTube yükleme için hesap izni (OAuth) ister. Bu
          yüzden burada anahtar değil, hesap bağlantısı kullanılır. Yetki dosyanız yalnızca bu
          bilgisayarda, sadece sizin okuyabileceğiniz bir dosyada saklanır.
        </p>
      </details>

      {confirmingDisconnect && (
        <ConfirmDialog
          title="YouTube bağlantısını kaldır"
          confirmLabel="Bağlantıyı kaldır"
          destructive
          body={
            <p>
              Kayıtlı yetki dosyası silinecek. OAuth istemci dosyanız yerinde kalır, bu yüzden
              istediğinizde tek tıkla yeniden bağlanabilirsiniz. YouTube'daki videolarınıza
              hiçbir şey olmaz.
            </p>
          }
          onCancel={() => setConfirmingDisconnect(false)}
          onConfirm={() => {
            setConfirmingDisconnect(false)
            void run(() => api.disconnectYoutube())
          }}
        />
      )}
    </div>
  )
}
