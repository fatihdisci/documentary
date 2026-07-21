import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload, AppSettings, SettingsResponse } from '@/api/types'
import { ErrorBox } from '@/components/ErrorBox'
import './SettingsPage.css'

export function SettingsPage() {
  const [data, setData] = useState<SettingsResponse | null>(null)
  const [draft, setDraft] = useState<AppSettings | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [apiKey, setApiKey] = useState('')

  const load = useCallback(async () => {
    try {
      const response = await api.getSettings()
      setData(response)
      setDraft(response.settings)
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function set<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current))
    setSaved(false)
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      const response = await api.updateSettings(draft)
      setData(response)
      setDraft(response.settings)
      setSaved(true)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  async function saveKey(value: string | null) {
    setBusy(true)
    try {
      setData(await api.setSecret('elevenlabs_api_key', value))
      setApiKey('')
      setError(null)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  if (!draft || !data) {
    return (
      <div className="page">
        <h1>Settings</h1>
        {error ? <ErrorBox error={error} onRetry={() => void load()} /> : <p className="muted">Loading…</p>}
      </div>
    )
  }

  const keyConfigured = data.configuredSecrets.includes('elevenlabs_api_key')

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Settings</h1>
          <p className="page-subtitle">Applies to new projects; existing projects keep their own values.</p>
        </div>
        <div className="header-actions">
          {saved && <span className="saved-pill">Saved</span>}
          <button className="primary" onClick={() => void save()} disabled={busy}>
            {busy ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <section className="card">
        <h2>Tools</h2>
        <div className="field-grid">
          <label>
            FFmpeg path
            <input value={draft.ffmpegPath} onChange={(e) => set('ffmpegPath', e.target.value)} />
            <span className="hint">Resolved: {data.resolvedPaths.ffmpeg || 'not found'}</span>
          </label>
          <label>
            ffprobe path
            <input value={draft.ffprobePath} onChange={(e) => set('ffprobePath', e.target.value)} />
            <span className="hint">Resolved: {data.resolvedPaths.ffprobe || 'not found'}</span>
          </label>
        </div>
        <p className="hint">
          Leave these as <code>ffmpeg</code> / <code>ffprobe</code> to search your PATH.
        </p>
      </section>

      <section className="card">
        <h2>Storage</h2>
        <div className="field-grid">
          <label>
            Projects directory
            <input
              value={draft.projectsDir}
              placeholder={data.resolvedPaths.projectsDir}
              onChange={(e) => set('projectsDir', e.target.value)}
            />
          </label>
          <label>
            Exports directory
            <input
              value={draft.exportsDir}
              placeholder={data.resolvedPaths.exportsDir}
              onChange={(e) => set('exportsDir', e.target.value)}
            />
          </label>
          <label>
            Temporary directory
            <input
              value={draft.tempDir}
              placeholder={data.resolvedPaths.tempDir}
              onChange={(e) => set('tempDir', e.target.value)}
            />
          </label>
          <label>
            Keep failed-render temp files for (days)
            <input
              type="number"
              min={0}
              max={90}
              value={draft.tempRetentionDays}
              onChange={(e) => set('tempRetentionDays', Number(e.target.value))}
            />
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.cleanupTempOnSuccess}
            onChange={(e) => set('cleanupTempOnSuccess', e.target.checked)}
          />
          Delete temporary files after a successful render
        </label>
      </section>

      <section className="card">
        <h2>Defaults for new projects</h2>
        <div className="field-grid">
          <label>
            Resolution
            <select
              value={`${draft.defaultWidth}x${draft.defaultHeight}`}
              onChange={(e) => {
                const [w, h] = e.target.value.split('x').map(Number)
                set('defaultWidth', w ?? 1920)
                set('defaultHeight', h ?? 1080)
              }}
            >
              <option value="1920x1080">1920 × 1080 (1080p)</option>
              <option value="2560x1440">2560 × 1440 (1440p)</option>
              <option value="3840x2160">3840 × 2160 (4K)</option>
            </select>
          </label>
          <label>
            Frame rate
            <select value={draft.defaultFps} onChange={(e) => set('defaultFps', Number(e.target.value))}>
              <option value={60}>60 fps</option>
              <option value={30}>30 fps</option>
              <option value={24}>24 fps</option>
            </select>
          </label>
          <label>
            Default transition
            <select
              value={draft.defaultTransition}
              onChange={(e) => set('defaultTransition', e.target.value as AppSettings['defaultTransition'])}
            >
              <option value="documentary-dissolve">Documentary dissolve</option>
              <option value="cross-dissolve">Cross dissolve</option>
              <option value="fade-through-black">Fade through black</option>
              <option value="none">No transition</option>
            </select>
            <span className="hint">Only restrained transitions are offered as a default.</span>
          </label>
          <label>
            Export quality
            <select
              value={draft.defaultQuality}
              onChange={(e) => set('defaultQuality', e.target.value as AppSettings['defaultQuality'])}
            >
              <option value="youtube-hq">YouTube high quality</option>
              <option value="high">High</option>
              <option value="standard">Standard</option>
              <option value="preview">Preview (fast, low quality)</option>
            </select>
          </label>
          <label>
            Scene lead-in (seconds)
            <input
              type="number"
              step={0.05}
              min={0}
              max={5}
              value={draft.defaultSceneLeadInSeconds}
              onChange={(e) => set('defaultSceneLeadInSeconds', Number(e.target.value))}
            />
            <span className="hint">Silence before narration starts in each scene.</span>
          </label>
          <label>
            Scene tail (seconds)
            <input
              type="number"
              step={0.05}
              min={0}
              max={10}
              value={draft.defaultSceneTailSeconds}
              onChange={(e) => set('defaultSceneTailSeconds', Number(e.target.value))}
            />
            <span className="hint">Visual hold after narration ends.</span>
          </label>
          <label>
            Intermediate codec
            <select
              value={draft.intermediateCodec}
              onChange={(e) => set('intermediateCodec', e.target.value as AppSettings['intermediateCodec'])}
            >
              <option value="h264-crf14-fast">H.264 CRF 14 (fast, small)</option>
              <option value="h264-crf12">H.264 CRF 12 (higher quality)</option>
              <option value="prores-lt">ProRes 422 LT (large, fastest to decode)</option>
              <option value="prores-422">ProRes 422 (largest)</option>
            </select>
            <span className="hint">Used for cached per-scene clips, not the final file.</span>
          </label>
          <label>
            Default voice
            <input value={draft.defaultVoice} onChange={(e) => set('defaultVoice', e.target.value)} />
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.useHardwareEncoder}
            onChange={(e) => set('useHardwareEncoder', e.target.checked)}
          />
          Use the hardware encoder when available
          <span className="hint">
            Faster, but software libx264 gives better quality per bit and always works.
          </span>
        </label>
      </section>

      <section className="card">
        <h2>API keys</h2>
        <p className="muted">
          Stored in <code>secrets.json</code> with owner-only permissions. Keys are never returned by
          the API, written to a log, or included in a project bundle.
        </p>
        <div className="row">
          <input
            type="password"
            value={apiKey}
            placeholder={keyConfigured ? '•••••••• (configured)' : 'ElevenLabs API key (optional)'}
            onChange={(e) => setApiKey(e.target.value)}
            aria-label="ElevenLabs API key"
          />
          <button onClick={() => void saveKey(apiKey)} disabled={!apiKey || busy}>
            Save key
          </button>
          {keyConfigured && (
            <button className="danger" onClick={() => void saveKey(null)} disabled={busy}>
              Remove key
            </button>
          )}
        </div>
        <p className="hint">
          Optional. Edge TTS is free and needs no key, and you can always upload your own narration
          audio instead.
        </p>
      </section>

      <section className="card">
        <h2>Limits</h2>
        <div className="field-grid">
          <label>
            Max upload size (MB)
            <input
              type="number"
              min={1}
              max={2048}
              value={draft.maxUploadMb}
              onChange={(e) => set('maxUploadMb', Number(e.target.value))}
            />
          </label>
          <label>
            Max content JSON size (MB)
            <input
              type="number"
              min={1}
              max={256}
              value={draft.maxJsonMb}
              onChange={(e) => set('maxJsonMb', Number(e.target.value))}
            />
          </label>
          <label>
            Disk safety margin (MB)
            <input
              type="number"
              min={0}
              max={102400}
              value={draft.diskSafetyMarginMb}
              onChange={(e) => set('diskSafetyMarginMb', Number(e.target.value))}
            />
            <span className="hint">Renders are blocked if less than this would remain free.</span>
          </label>
          <label>
            Log level
            <select value={draft.logLevel} onChange={(e) => set('logLevel', e.target.value)}>
              <option value="DEBUG">Debug (verbose)</option>
              <option value="INFO">Info</option>
              <option value="WARNING">Warning</option>
              <option value="ERROR">Error</option>
            </select>
          </label>
        </div>
      </section>
    </div>
  )
}
