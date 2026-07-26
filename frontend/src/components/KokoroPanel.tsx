/**
 * Everything Kokoro exposes, surfaced in the app itself.
 *
 * Kokoro is the only provider whose setup happens outside the app — a pip
 * install and a one-off model download — so the panel is written to be useful
 * *before* any of that has happened: the catalogue, the options and the exact
 * commands all render whether or not the package is present.
 */

import { useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { KokoroInfo, KokoroVoiceInfo } from '@/api/audio-types'
import type { ApiErrorPayload } from '@/api/types'
import { gradeRank } from '@/lib/kokoroVoices'
import './KokoroPanel.css'

interface Props {
  /** The voice the project currently uses, so the panel can highlight it. */
  currentVoice: string
  onPickVoice: (voiceId: string) => void
}

export function KokoroPanel({ currentVoice, onPickVoice }: Props) {
  const [info, setInfo] = useState<KokoroInfo | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [device, setDevice] = useState<string | null>(null)
  const [savingDevice, setSavingDevice] = useState(false)
  const [open, setOpen] = useState<'setup' | 'usage' | 'input' | 'languages' | null>(null)

  useEffect(() => {
    let cancelled = false
    void api
      .getKokoroInfo()
      .then((result) => {
        if (cancelled) return
        setInfo(result)
        setError(null)
      })
      .catch((err) => !cancelled && setError(describeError(err)))
    void api
      .getSettings()
      .then((r) => !cancelled && setDevice(r.settings.kokoroDevice))
      .catch(() => undefined) // the panel still works without the device control
    return () => {
      cancelled = true
    }
  }, [])

  async function changeDevice(next: string) {
    setSavingDevice(true)
    const previous = device
    setDevice(next)
    try {
      const current = await api.getSettings()
      await api.updateSettings({
        ...current.settings,
        kokoroDevice: next as typeof current.settings.kokoroDevice,
      })
      setInfo(await api.getKokoroInfo())
    } catch (err) {
      setDevice(previous)
      setError(describeError(err))
    } finally {
      setSavingDevice(false)
    }
  }

  if (error) {
    return (
      <section className="card kokoro-panel">
        <h2>Kokoro</h2>
        <p className="muted">Kokoro bilgileri alınamadı: {error.message}</p>
      </section>
    )
  }

  if (!info) {
    return (
      <section className="card kokoro-panel">
        <h2>Kokoro</h2>
        <p className="muted">Yükleniyor…</p>
      </section>
    )
  }

  const { environment: env } = info
  const ready = env.installed && env.modelCached
  const stateLabel = !env.installed ? 'kurulu değil' : env.modelCached ? 'hazır' : 'model inecek'
  const stateClass = !env.installed ? 'kokoro-state-off' : env.modelCached
    ? 'kokoro-state-ready'
    : 'kokoro-state-pending'

  const english = info.voices
    .filter((v) => v.langCode === 'a' || v.langCode === 'b')
    .sort((a, b) => gradeRank(a.grade) - gradeRank(b.grade))
  const recommended = info.recommended
    .map((id) => info.voices.find((v) => v.id === id))
    .filter((v): v is KokoroVoiceInfo => Boolean(v))

  function toggle(section: 'setup' | 'usage' | 'input' | 'languages') {
    setOpen((current) => (current === section ? null : section))
  }

  return (
    <section className="card kokoro-panel">
      <header className="kokoro-header">
        <div>
          <h2>
            Kokoro <span className={`kokoro-pill ${stateClass}`}>{stateLabel}</span>
          </h2>
          <p className="muted">
            82 milyon parametrelik yerel model. {ready
              ? 'Bu bilgisayarda çalışıyor, internet gerektirmiyor.'
              : 'Kurulumu tamamlandığında internetsiz çalışır.'}
          </p>
        </div>
      </header>

      <dl className="kokoro-facts">
        <div>
          <dt>Paket</dt>
          <dd className={env.installed ? '' : 'warn'}>
            {env.installed
              ? `kurulu${env.torchVersion ? ` · torch ${env.torchVersion}` : ''}`
              : 'bulunamadı'}
          </dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd className={env.modelCached ? '' : 'warn'}>
            {env.modelCached ? 'indirildi' : 'ilk kullanımda inecek (~350 MB)'}
          </dd>
        </div>
        <div>
          <dt>Telaffuz motoru</dt>
          <dd className={env.espeakAvailable ? '' : 'warn'}>
            {env.espeakAvailable ? 'espeak-ng var' : 'espeak-ng yok'}
          </dd>
        </div>
        <div>
          <dt>Çalıştığı birim</dt>
          <dd>
            <select
              value={device ?? 'auto'}
              disabled={device === null || savingDevice}
              onChange={(e) => void changeDevice(e.target.value)}
              aria-label="Kokoro'nun çalışacağı birim"
            >
              {info.deviceOptions.map((option) => (
                <option key={option} value={option}>
                  {option === 'auto' ? `auto (şu an ${env.device})` : option}
                </option>
              ))}
            </select>
          </dd>
        </div>
      </dl>

      {!env.installed && (
        <p className="kokoro-callout">
          Kokoro kurulu olmadan seçilemez. Aşağıdaki <strong>Kurulum</strong> adımlarını
          uygulayıp uygulamayı yeniden başlatın.
        </p>
      )}

      <div className="kokoro-recommended">
        <span className="kokoro-recommended-label">Belgesel için önerilenler</span>
        <div className="kokoro-chips">
          {recommended.map((voice) => (
            <button
              key={voice.id}
              type="button"
              className={`kokoro-chip ${voice.id === currentVoice ? 'active' : ''}`}
              onClick={() => onPickVoice(voice.id)}
              title={`${voice.language} · ${voice.grade} notu · ${voice.training}`}
            >
              {voice.label}
              <span className="kokoro-chip-grade">{voice.grade}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="kokoro-sections">
        <Section
          id="setup"
          title="Kurulum"
          count={info.setupSteps.length}
          open={open === 'setup'}
          onToggle={toggle}
        >
          <ol className="kokoro-steps">
            {info.setupSteps.map((step) => (
              <li key={step}>{renderStep(step)}</li>
            ))}
          </ol>
          <p className="hint">
            Model şuraya iner: <code>{env.cacheDir}</code>
          </p>
        </Section>

        <Section
          id="usage"
          title="Kullanım"
          count={info.usageNotes.length}
          open={open === 'usage'}
          onToggle={toggle}
        >
          <ul className="kokoro-notes">
            {info.usageNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Section>

        <Section
          id="input"
          title="Metin yazarken"
          count={info.inputNotes.length}
          open={open === 'input'}
          onToggle={toggle}
        >
          <ul className="kokoro-notes">
            {info.inputNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Section>

        <Section
          id="languages"
          title="Diller ve sesler"
          count={info.voices.length}
          open={open === 'languages'}
          onToggle={toggle}
        >
          <table className="kokoro-table">
            <thead>
              <tr>
                <th>Dil</th>
                <th>Ses</th>
                <th>Kelime zamanlaması</th>
                <th>Ek kurulum</th>
              </tr>
            </thead>
            <tbody>
              {info.languages.map((language) => (
                <tr key={language.code}>
                  <td>{language.label}</td>
                  <td>{language.voiceCount}</td>
                  <td>{language.wordTimings ? 'var' : 'yok — altyazı tahmin edilir'}</td>
                  <td>{language.extraInstall ? <code>{language.extraInstall}</code> : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4 className="kokoro-subhead">İngilizce sesler, nota göre</h4>
          <table className="kokoro-table">
            <thead>
              <tr>
                <th>Ses</th>
                <th>Not</th>
                <th>Cinsiyet</th>
                <th>Aksan</th>
                <th>Eğitim verisi</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {english.map((voice) => (
                <tr key={voice.id} className={voice.id === currentVoice ? 'current' : ''}>
                  <td>
                    <code>{voice.id}</code> {voice.note && <span className="muted">{voice.note}</span>}
                  </td>
                  <td>{voice.grade}</td>
                  <td>{voice.gender === 'Female' ? 'Kadın' : 'Erkek'}</td>
                  <td>{voice.locale}</td>
                  <td>{voice.training}</td>
                  <td>
                    <button type="button" onClick={() => onPickVoice(voice.id)}>
                      {voice.id === currentVoice ? 'seçili' : 'seç'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>
    </section>
  )
}

interface SectionProps {
  id: 'setup' | 'usage' | 'input' | 'languages'
  title: string
  count: number
  open: boolean
  onToggle: (id: 'setup' | 'usage' | 'input' | 'languages') => void
  children: React.ReactNode
}

function Section({ id, title, count, open, onToggle, children }: SectionProps) {
  return (
    <div className={`kokoro-section ${open ? 'open' : ''}`}>
      <button type="button" className="kokoro-section-head" onClick={() => onToggle(id)} aria-expanded={open}>
        <span>{title}</span>
        <span className="kokoro-section-meta">
          {count} <span aria-hidden>{open ? '▾' : '▸'}</span>
        </span>
      </button>
      {open && <div className="kokoro-section-body">{children}</div>}
    </div>
  )
}

/** Render a "label: command" step with the command in a code tag. */
function renderStep(step: string): React.ReactNode {
  const separator = step.indexOf(': ')
  if (separator === -1) return step
  const label = step.slice(0, separator + 1)
  const rest = step.slice(separator + 2)
  // Only the command-shaped tail becomes code; prose tails stay as prose.
  if (!/^(pip|brew|source|python|npm|\.\/)/.test(rest)) return step
  return (
    <>
      {label} <code>{rest}</code>
    </>
  )
}
