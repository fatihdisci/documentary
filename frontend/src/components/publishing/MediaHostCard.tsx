/**
 * Temporary media hosting, as the Settings page shows it.
 *
 * Only Instagram and Facebook need this, and the card says so: their publishing
 * APIs are given a **URL** and download the video themselves, so a local-first
 * app has to park the file somewhere reachable for a few minutes. YouTube and
 * TikTok take the bytes directly and never touch this.
 *
 * The bucket's coordinates are configuration and are shown. The two keys are
 * write-only: leaving their fields empty keeps whatever is stored, so correcting
 * a bucket name cannot silently wipe working credentials, and no endpoint
 * returns either value.
 */

import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { MediaHostStatus } from '@/api/publishing-types'
import { ErrorBox } from '@/components/ErrorBox'

export function MediaHostCard() {
  const [status, setStatus] = useState<MediaHostStatus | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  const [provider, setProvider] = useState('none')
  const [endpoint, setEndpoint] = useState('')
  const [bucket, setBucket] = useState('')
  const [region, setRegion] = useState('auto')
  const [prefix, setPrefix] = useState('evb-temp')
  const [ttlMinutes, setTtlMinutes] = useState(60)
  const [deleteAfter, setDeleteAfter] = useState(true)
  const [accessKeyId, setAccessKeyId] = useState('')
  const [secretAccessKey, setSecretAccessKey] = useState('')

  const adopt = useCallback((value: MediaHostStatus) => {
    setStatus(value)
    setProvider(value.provider)
    setEndpoint(value.endpoint)
    setBucket(value.bucket)
    setRegion(value.region || 'auto')
    setPrefix(value.prefix)
    setTtlMinutes(Math.max(5, Math.round((value.ttlSeconds || 3600) / 60)))
    setDeleteAfter(value.deleteAfterPublish)
  }, [])

  const load = useCallback(async () => {
    try {
      adopt(await api.mediaHostStatus())
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [adopt])

  useEffect(() => {
    void load()
  }, [load])

  async function save() {
    setBusy(true)
    setError(null)
    try {
      adopt(
        await api.saveMediaHostSettings({
          provider,
          endpoint,
          bucket,
          region,
          prefix,
          ttlSeconds: ttlMinutes * 60,
          deleteAfterPublish: deleteAfter,
          // Empty means "keep what is stored"; the backend treats it that way.
          accessKeyId: accessKeyId || null,
          secretAccessKey: secretAccessKey || null,
        }),
      )
      setAccessKeyId('')
      setSecretAccessKey('')
      setSaved(true)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="connection-block">
      <div className="connection-head">
        <div>
          <strong>Geçici medya barındırma (Instagram ve Facebook için)</strong>
          <p className="muted">{status?.statusMessage ?? 'Durum okunuyor…'}</p>
        </div>
        {saved && <span className="saved-pill">Kaydedildi</span>}
      </div>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <p className="hint">
        Instagram ve Facebook, videoyu bilgisayarınızdan almaz; verdiğiniz bir adresten kendisi
        indirir. Bu yüzden yayın sırasında video, S3 uyumlu bir kovaya (Cloudflare R2 önerilir)
        süreli ve imzalı bir bağlantıyla konur, yayın bitince oradan silinir. YouTube ve TikTok
        bu bölümü kullanmaz.
      </p>

      <div className="field-grid">
        <label htmlFor="host-provider">
          Barındırma türü
          <select
            id="host-provider"
            value={provider}
            onChange={(event) => {
              setProvider(event.target.value)
              setSaved(false)
            }}
          >
            <option value="none">Yok (Instagram/Facebook kapalı)</option>
            <option value="s3">S3 uyumlu (Cloudflare R2, MinIO, AWS S3)</option>
          </select>
        </label>
        <label htmlFor="host-endpoint">
          Endpoint
          <input
            id="host-endpoint"
            value={endpoint}
            placeholder="https://<hesap-id>.r2.cloudflarestorage.com"
            onChange={(event) => {
              setEndpoint(event.target.value)
              setSaved(false)
            }}
          />
        </label>
        <label htmlFor="host-bucket">
          Kova (bucket) adı
          <input
            id="host-bucket"
            value={bucket}
            onChange={(event) => {
              setBucket(event.target.value)
              setSaved(false)
            }}
          />
        </label>
        <label htmlFor="host-region">
          Bölge
          <input
            id="host-region"
            value={region}
            onChange={(event) => {
              setRegion(event.target.value)
              setSaved(false)
            }}
          />
          <span className="hint">R2 için <code>auto</code>.</span>
        </label>
        <label htmlFor="host-prefix">
          Klasör öneki
          <input
            id="host-prefix"
            value={prefix}
            onChange={(event) => {
              setPrefix(event.target.value)
              setSaved(false)
            }}
          />
        </label>
        <label htmlFor="host-ttl">
          Bağlantı geçerlilik süresi (dakika)
          <input
            id="host-ttl"
            type="number"
            min={5}
            max={1440}
            value={ttlMinutes}
            onChange={(event) => {
              setTtlMinutes(Number(event.target.value))
              setSaved(false)
            }}
          />
          <span className="hint">
            Meta'nın videoyu indirmesine yetecek kadar uzun, sızan bir bağlantının yarın işe
            yaramayacağı kadar kısa.
          </span>
        </label>
      </div>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={deleteAfter}
          onChange={(event) => {
            setDeleteAfter(event.target.checked)
            setSaved(false)
          }}
        />
        Yayın bitince geçici kopyayı sil
      </label>

      <div className="field-grid">
        <label htmlFor="host-access-key">
          Access Key ID
          <input
            id="host-access-key"
            value={accessKeyId}
            autoComplete="off"
            placeholder={status?.keysPresent ? '•••••••• (kayıtlı)' : ''}
            onChange={(event) => setAccessKeyId(event.target.value)}
          />
        </label>
        <label htmlFor="host-secret-key">
          Secret Access Key
          <input
            id="host-secret-key"
            type="password"
            value={secretAccessKey}
            autoComplete="off"
            placeholder={status?.keysPresent ? '•••••••• (kayıtlı)' : ''}
            onChange={(event) => setSecretAccessKey(event.target.value)}
          />
        </label>
      </div>
      <p className="hint">
        Boş bırakırsanız kayıtlı anahtarlar korunur. Anahtarlar hiçbir ekranda geri gösterilmez.
      </p>

      <div className="row connection-actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
          Barındırma ayarlarını kaydet
        </button>
        <button type="button" disabled={busy} onClick={() => void load()}>
          Durumu yenile
        </button>
        {status?.keysPresent && (
          <button
            type="button"
            className="danger"
            disabled={busy}
            onClick={() => {
              setBusy(true)
              void api
                .clearMediaHostKeys()
                .then(adopt)
                .catch((err: unknown) => setError(describeError(err)))
                .finally(() => setBusy(false))
            }}
          >
            Anahtarları sil
          </button>
        )}
      </div>

      {status?.problem && (
        <p className="connection-problem">
          ⚠ {status.problem} {status.suggestion}
        </p>
      )}

      <details className="connection-setup">
        <summary>Cloudflare R2 nasıl hazırlanır?</summary>
        <ol>
          <li>Cloudflare hesabınızda <strong>R2</strong> bölümünden bir kova oluşturun.</li>
          <li>
            Kovayı <strong>herkese açık yapmayın</strong>. Program her yayında yalnızca o
            video için süreli ve imzalı bir bağlantı üretir.
          </li>
          <li>
            <strong>Manage R2 API Tokens</strong> bölümünden <em>Object Read &amp; Write</em>
            yetkili bir token oluşturun; verilen Access Key ID ve Secret Access Key'i buraya
            girin.
          </li>
          <li>
            Endpoint adresi <code>https://&lt;hesap-id&gt;.r2.cloudflarestorage.com</code>
            biçimindedir; bölge olarak <code>auto</code> yazın.
          </li>
          <li>
            İsterseniz kovada 1 günlük bir yaşam döngüsü (lifecycle) kuralı tanımlayın; program
            zaten yayından sonra siler, bu ikinci bir güvencedir.
          </li>
        </ol>
      </details>
    </div>
  )
}
