/**
 * Encrypted settings bundles for moving one installation to another.
 *
 * The passphrase is mandatory because this file can travel through a USB drive
 * or a synced folder; API keys and OAuth grants must never sit there as plain
 * text. The component only renders counts and names, never secret values.
 */

import { useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload, BundleContents, BundleImportResult } from '@/api/types'
import { ErrorBox } from '@/components/ErrorBox'
import { formatDateTime } from '@/lib/format'

function bundleSummary(contents: Pick<BundleContents, 'secrets' | 'credentialFiles'>) {
  return `${contents.secrets} anahtar, ${contents.credentialFiles} yetki dosyası`
}

function NameList({ names, empty }: { names: string[]; empty: string }) {
  return names.length > 0 ? <>{names.join(', ')}</> : <>{empty}</>
}

export function SettingsBundleCard() {
  const [exportPassphrase, setExportPassphrase] = useState('')
  const [includeCredentials, setIncludeCredentials] = useState(true)
  const [exportBusy, setExportBusy] = useState(false)
  const [exported, setExported] = useState<Pick<BundleContents, 'secrets' | 'credentialFiles'> | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [contents, setContents] = useState<BundleContents | null>(null)
  const [inspectBusy, setInspectBusy] = useState(false)
  const [importPassphrase, setImportPassphrase] = useState('')
  const [overwrite, setOverwrite] = useState(true)
  const [includePaths, setIncludePaths] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [imported, setImported] = useState<BundleImportResult | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)

  async function exportBundle() {
    setExportBusy(true)
    setError(null)
    setExported(null)
    try {
      const result = await api.exportSettingsBundle(exportPassphrase, includeCredentials)
      setExportPassphrase('')
      const url = URL.createObjectURL(result.blob)
      const link = document.createElement('a')
      link.href = url
      link.download = result.filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setExported(result.contents)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setExportBusy(false)
    }
  }

  async function inspectBundle(selectedFile: File) {
    setFile(selectedFile)
    setContents(null)
    setImported(null)
    setError(null)
    setInspectBusy(true)
    try {
      setContents(await api.inspectSettingsBundle(selectedFile))
    } catch (err) {
      setFile(null)
      setError(describeError(err))
    } finally {
      setInspectBusy(false)
    }
  }

  async function importBundle() {
    if (!file) return
    setImportBusy(true)
    setError(null)
    setImported(null)
    try {
      const result = await api.importSettingsBundle(file, importPassphrase, { overwrite, includePaths })
      setImportPassphrase('')
      setImported(result)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setImportBusy(false)
    }
  }

  const exportReady = exportPassphrase.length >= 8
  const importReady = file !== null && contents !== null && importPassphrase.length >= 8

  return (
    <div className="connection-block">
      <div className="connection-head">
        <div>
          <strong>Ayarları başka bilgisayara taşı</strong>
          <p className="muted">API anahtarlarını ve isteğe bağlı hesap yetkilerini şifreli paketle taşıyın.</p>
        </div>
      </div>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <div className="field-grid">
        <label htmlFor="settings-bundle-export-passphrase">
          Paket parolası
          <input
            id="settings-bundle-export-passphrase"
            aria-label="Dışa aktarma parolası"
            type="password"
            autoComplete="new-password"
            value={exportPassphrase}
            onChange={(event) => setExportPassphrase(event.target.value)}
          />
          <span className="hint">En az 8 karakter. Bu parola olmadan paket açılamaz.</span>
        </label>
      </div>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={includeCredentials}
          onChange={(event) => setIncludeCredentials(event.target.checked)}
        />
        OAuth yetkilerini de dahil et
        <span className="hint">Kapatılırsa YouTube, Meta ve TikTok hesaplarını diğer bilgisayarda yeniden bağlamanız gerekir.</span>
      </label>
      <div className="row">
        <button type="button" className="primary" disabled={exportBusy || !exportReady} onClick={() => void exportBundle()}>
          Dışa aktar
        </button>
        {exported && <span className="hint">{bundleSummary(exported)} dışa aktarıldı.</span>}
      </div>

      <hr />

      <label htmlFor="settings-bundle-file">
        Şifreli ayar paketi
        <input
          id="settings-bundle-file"
          type="file"
          onChange={(event) => {
            const selectedFile = event.target.files?.[0]
            if (selectedFile) void inspectBundle(selectedFile)
            event.target.value = ''
          }}
        />
      </label>
      {inspectBusy && <p className="muted">Paket inceleniyor…</p>}
      {contents && (
        <p className="hint">
          Bu paket: {bundleSummary(contents)}, oluşturulma: {formatDateTime(contents.createdAt)}.
        </p>
      )}
      {contents && (
        <>
          <label htmlFor="settings-bundle-import-passphrase">
            Paket parolası
            <input
              id="settings-bundle-import-passphrase"
              aria-label="İçe aktarma parolası"
              type="password"
              autoComplete="current-password"
              value={importPassphrase}
              onChange={(event) => setImportPassphrase(event.target.value)}
            />
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} />
            Var olan anahtarları üzerine yaz
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={includePaths} onChange={(event) => setIncludePaths(event.target.checked)} />
            Klasör yollarını da al
            <span className="hint">Diğer bilgisayardaki proje ve dışa aktarma klasör yolları genelde bu bilgisayarda yoktur.</span>
          </label>
          <div className="row">
            <button type="button" className="primary" disabled={importBusy || !importReady} onClick={() => void importBundle()}>
              İçe aktar
            </button>
          </div>
        </>
      )}

      {imported && (
        <div className="connection-setup">
          <p className="hint">İçe aktarma tamamlandı. Bağlantı kartlarının güncel durumunu görmek için sayfayı yenileyin.</p>
          <button type="button" onClick={() => window.location.reload()}>Sayfayı yenile</button>
          <dl className="connection-grid">
            <dt>Alınan anahtarlar</dt>
            <dd><NameList names={imported.secretsImported} empty="Yok" /></dd>
            <dt>Atlanan anahtarlar</dt>
            <dd><NameList names={imported.secretsSkipped} empty="Yok" />{imported.secretsSkipped.length > 0 && ' (zaten kayıtlı)'}</dd>
            <dt>Alınan yetki dosyaları</dt>
            <dd><NameList names={imported.credentialFilesImported} empty="Yok" /></dd>
            <dt>Atlanan yetki dosyaları</dt>
            <dd><NameList names={imported.credentialFilesSkipped} empty="Yok" />{imported.credentialFilesSkipped.length > 0 && ' (zaten kayıtlı)'}</dd>
            <dt>Ayarlar</dt>
            <dd>{imported.settingsApplied ? 'Uygulandı' : 'Uygulanmadı'}</dd>
          </dl>
          {imported.warnings.length > 0 && (
            <div className="connection-problem">
              <strong>Uyarılar</strong>
              <ul>{imported.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
