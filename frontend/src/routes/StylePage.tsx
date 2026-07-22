/**
 * Style editor.
 *
 * The render pipeline already understands every field on `project.style`; this
 * screen is the editor for them. Titles, subtitles, captions and burned-in
 * subtitles each get the full text-card treatment (font, colour, shadow,
 * outline, background box, animation), plus the global controls that apply to
 * every overlay: position, safe margin, scrim, watermark and the default
 * transition between scenes.
 *
 * A live preview mirrors the backend's text-card model closely enough to make
 * font, colour, weight, spacing and box decisions without rendering a proxy.
 */

import { useState } from 'react'
import { useProjectStore } from '@/store/project'
import type { TextStyle, SubtitleStyle, Style, TextAnimation } from '@/api/project-types'
import type { TransitionPreset } from '@/api/types'
import './StylePage.css'

type TextGroup = 'title' | 'subtitle' | 'caption' | 'subtitles'

const TEXT_GROUPS: { id: TextGroup; label: string; sample: string }[] = [
  { id: 'title', label: 'Başlık', sample: 'Dodo Kuşu' },
  { id: 'subtitle', label: 'Alt başlık', sample: 'Raphus cucullatus' },
  { id: 'caption', label: 'Küçük yazı', sample: 'Mauritius · 1681 civarı' },
  { id: 'subtitles', label: 'Altyazı', sample: 'Uçamıyordu ve hiç düşmanı yoktu.' },
]

// Human labels for the pipeline's transition presets.
const TRANSITIONS: { id: TransitionPreset; label: string }[] = [
  { id: 'none', label: 'Geçiş yok (sert kesme)' },
  { id: 'cross-dissolve', label: 'Yumuşak geçiş' },
  { id: 'documentary-dissolve', label: 'Belgesel geçişi (önerilen)' },
  { id: 'slow-cinematic-dissolve', label: 'Yavaş sinematik geçiş' },
  { id: 'fade-through-black', label: 'Siyaha kararıp açılma' },
  { id: 'fade-through-white', label: 'Beyaza açılıp kararma' },
  { id: 'dip-to-black', label: 'Kısa siyah' },
  { id: 'subtle-zoom-dissolve', label: 'Hafif yakınlaşarak geçiş' },
  { id: 'horizontal-slide', label: 'Yana kaydırma' },
  { id: 'vertical-slide', label: 'Yukarı kaydırma' },
  { id: 'blur-dissolve', label: 'Bulanıklaşarak geçiş' },
]

const ANIMATIONS: { id: TextAnimation; label: string }[] = [
  { id: 'none', label: 'Yok (birden görünsün)' },
  { id: 'fade', label: 'Yavaşça belirsin' },
  { id: 'slide-up', label: 'Aşağıdan kaysın' },
  { id: 'slide-left', label: 'Sağdan kaysın' },
]

const TEXT_POSITIONS = [
  'top-left', 'top-center', 'top-right',
  'middle-left', 'middle-center', 'middle-right',
  'bottom-left', 'bottom-center', 'bottom-right',
] as const

const POSITION_LABEL: Record<(typeof TEXT_POSITIONS)[number], string> = {
  'top-left': 'Üst sol',
  'top-center': 'Üst orta',
  'top-right': 'Üst sağ',
  'middle-left': 'Orta sol',
  'middle-center': 'Tam orta',
  'middle-right': 'Orta sağ',
  'bottom-left': 'Alt sol',
  'bottom-center': 'Alt orta',
  'bottom-right': 'Alt sağ',
}

// Bundled first; the rest are common system families offered as suggestions.
// A family the machine lacks is reported by the renderer, never silently swapped.
const FONT_SUGGESTIONS = [
  'Inter', 'Helvetica Neue', 'Arial', 'Georgia', 'Times New Roman',
  'Avenir Next', 'Futura', 'Gill Sans', 'Palatino', 'Baskerville',
  'Trebuchet MS', 'Verdana',
]

/** hex + 0–1 opacity → rgba() for the preview only. */
function rgba(hex: string, opacity: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!m) return hex
  const [r, g, b] = [m[1], m[2], m[3]].map((h) => parseInt(h ?? '0', 16))
  return `rgba(${r}, ${g}, ${b}, ${opacity})`
}

interface FieldProps {
  label: string
  hint?: string
  span2?: boolean
  children: React.ReactNode
}

function Field({ label, hint, span2, children }: FieldProps) {
  return (
    <label className={span2 ? 'span-2' : undefined}>
      {label}
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

export function StylePage() {
  const { project, edit } = useProjectStore()
  const [group, setGroup] = useState<TextGroup>('title')

  if (!project) {
    return (
      <div className="page">
        <h1>Görünüm</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
      </div>
    )
  }

  const style = project.style
  const active = style[group]
  const meta = TEXT_GROUPS.find((g) => g.id === group)!

  // Edit a field on the currently selected text class.
  function editText(mutate: (t: TextStyle) => void) {
    edit((d) => mutate(d.style[group]))
  }
  // Edit a subtitle-only field (guarded by the caller checking `group`).
  function editSubtitle(mutate: (t: SubtitleStyle) => void) {
    edit((d) => mutate(d.style.subtitles))
  }
  function editStyle(mutate: (s: Style) => void) {
    edit((d) => mutate(d.style))
  }

  function resetTextGroup() {
    editText((t) => {
      // Reset the fields this screen edits back to the pipeline defaults.
      Object.assign(t, {
        fontFamily: style.fontFamily,
        fontWeight: group === 'title' ? 700 : group === 'subtitles' ? 500 : group === 'caption' ? 500 : 400,
        size: group === 'title' ? 64 : group === 'subtitles' ? 38 : group === 'caption' ? 38 : 36,
        color: '#FFFFFF',
        letterSpacing: 0,
        lineSpacing: 1.25,
        shadow: true,
        shadowBlur: 12,
        shadowOffset: 3,
        outlineWidth: 0,
        outlineColor: '#000000',
        box: true,
        boxColor: '#000000',
        boxOpacity: 0.45,
        boxPaddingX: 32,
        boxPaddingY: 18,
        boxRadius: 8,
        animation: 'fade' as TextAnimation,
        fadeInSeconds: 0.5,
        fadeOutSeconds: 0.5,
      })
    })
  }

  // Preview box geometry: cap the on-screen font so a 300px title still fits.
  const previewSize = Math.min(active.size, 56)
  const boxBg = active.box ? rgba(active.boxColor, active.boxOpacity) : 'transparent'
  const textShadow = active.shadow
    ? `${active.shadowOffset}px ${active.shadowOffset}px ${active.shadowBlur}px rgba(0,0,0,0.85)`
    : 'none'
  const textStroke = active.outlineWidth
    ? `${Math.min(active.outlineWidth, 3)}px ${active.outlineColor}`
    : undefined

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Görünüm</h1>
          <p className="page-subtitle">
            Videodaki yazıların ve sahne geçişlerinin görünümü. Burada yaptığınız ayarlar tüm
            sahneler için geçerlidir.
          </p>
        </div>
      </header>

      <section className="card">
        <h2>Genel</h2>
        <div className="field-grid">
          <Field label="Yazı tipi" hint="Inter uygulamayla birlikte gelir ve her bilgisayarda aynı görünür.">
            <input
              list="font-suggestions"
              value={style.fontFamily}
              onChange={(e) => editStyle((s) => void (s.fontFamily = e.target.value))}
            />
            <datalist id="font-suggestions">
              {FONT_SUGGESTIONS.map((f) => (
                <option key={f} value={f} />
              ))}
            </datalist>
          </Field>

          <Field label="Yazıların yeri" hint="Yazılar ekranın neresinde dursun?">
            <select
              value={style.textPosition}
              onChange={(e) =>
                editStyle((s) => void (s.textPosition = e.target.value as Style['textPosition']))
              }
            >
              {TEXT_POSITIONS.map((p) => (
                <option key={p} value={p}>
                  {POSITION_LABEL[p]}
                </option>
              ))}
            </select>
          </Field>

          <Field label={`Kenar boşluğu — ${style.textSafeMargin}px`} hint="Yazıların ekran kenarına yapışmasını önler.">
            <input
              type="range"
              min={0}
              max={400}
              step={4}
              value={style.textSafeMargin}
              onChange={(e) => editStyle((s) => void (s.textSafeMargin = Number(e.target.value)))}
            />
          </Field>

          <Field
            label={`Karartma — ${style.overlayOpacity.toFixed(2)}`}
            hint="Yazıların altına çizilen hafif koyu perde. Yazıyı okunaklı yapar."
          >
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={style.overlayOpacity}
              onChange={(e) => editStyle((s) => void (s.overlayOpacity = Number(e.target.value)))}
            />
          </Field>

          <Field label="Sahne geçişi" hint="Kendi geçişini seçmemiş sahneler arasında kullanılır.">
            <select
              value={style.transitionPreset}
              onChange={(e) =>
                editStyle((s) => void (s.transitionPreset = e.target.value as TransitionPreset))
              }
            >
              {TRANSITIONS.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Köşe yazısı (filigran)" hint="İstemiyorsanız boş bırakın.">
            <input
              value={style.watermarkText}
              maxLength={80}
              placeholder="örneğin @kanaladiniz"
              onChange={(e) => editStyle((s) => void (s.watermarkText = e.target.value))}
            />
          </Field>

          {style.watermarkText.trim() !== '' && (
            <Field label={`Köşe yazısı belirginliği — ${style.watermarkOpacity.toFixed(2)}`}>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={style.watermarkOpacity}
                onChange={(e) => editStyle((s) => void (s.watermarkOpacity = Number(e.target.value)))}
              />
            </Field>
          )}
        </div>
      </section>

      <section className="card">
        <div className="style-tabs-row">
          <div className="style-tabs" role="tablist" aria-label="Yazı türü">
            {TEXT_GROUPS.map((g) => (
              <button
                key={g.id}
                role="tab"
                aria-selected={group === g.id}
                className={`style-tab ${group === g.id ? 'active' : ''}`}
                onClick={() => setGroup(g.id)}
              >
                {g.label}
              </button>
            ))}
          </div>
          <button onClick={resetTextGroup} title="Bu yazı türünü varsayılan ayarlara döndür">
            Sıfırla: {meta.label.toLowerCase()}
          </button>
        </div>

        <div
          className="style-preview"
          data-testid="style-preview"
          style={{ ['--scrim' as string]: rgba('#000000', style.overlayOpacity) }}
        >
          <span
            className="style-preview-text"
            style={{
              fontFamily: `${active.fontFamily}, ${
                'var(--font)'
              }`,
              fontWeight: active.fontWeight,
              fontSize: `${previewSize}px`,
              color: active.color,
              letterSpacing: `${active.letterSpacing}px`,
              lineHeight: active.lineSpacing,
              background: boxBg,
              padding: active.box
                ? `${Math.round(active.boxPaddingY * 0.5)}px ${Math.round(active.boxPaddingX * 0.5)}px`
                : 0,
              borderRadius: `${active.boxRadius}px`,
              textShadow,
              WebkitTextStroke: textStroke,
            }}
          >
            {meta.sample}
          </span>
        </div>

        <div className="field-grid">
          <Field label="Kalınlık">
            <select
              value={active.fontWeight}
              onChange={(e) => editText((t) => void (t.fontWeight = Number(e.target.value)))}
            >
              {[300, 400, 500, 600, 700, 900].map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </Field>

          <Field label={`Boyut — ${active.size}px`}>
            <input
              type="range"
              min={8}
              max={200}
              step={1}
              value={active.size}
              onChange={(e) => editText((t) => void (t.size = Number(e.target.value)))}
            />
          </Field>

          <Field label="Renk">
            <div className="color-row">
              <input
                type="color"
                className="color-swatch"
                value={active.color}
                onChange={(e) => editText((t) => void (t.color = e.target.value.toUpperCase()))}
                aria-label="Yazı rengi"
              />
              <input
                value={active.color}
                pattern="^#[0-9A-Fa-f]{6}$"
                onChange={(e) => editText((t) => void (t.color = e.target.value.toUpperCase()))}
              />
            </div>
          </Field>

          <Field label="Nasıl görünsün?">
            <select
              value={active.animation}
              onChange={(e) => editText((t) => void (t.animation = e.target.value as TextAnimation))}
            >
              {ANIMATIONS.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label={`Harf aralığı — ${active.letterSpacing.toFixed(1)}`}>
            <input
              type="range"
              min={-5}
              max={30}
              step={0.5}
              value={active.letterSpacing}
              onChange={(e) => editText((t) => void (t.letterSpacing = Number(e.target.value)))}
            />
          </Field>

          <Field label={`Satır aralığı — ${active.lineSpacing.toFixed(2)}`}>
            <input
              type="range"
              min={0.6}
              max={3}
              step={0.05}
              value={active.lineSpacing}
              onChange={(e) => editText((t) => void (t.lineSpacing = Number(e.target.value)))}
            />
          </Field>

          <Field label={`En fazla genişlik — ekranın %${Math.round(active.maxWidthRatio * 100)}'i`}>
            <input
              type="range"
              min={0.1}
              max={1}
              step={0.01}
              value={active.maxWidthRatio}
              onChange={(e) => editText((t) => void (t.maxWidthRatio = Number(e.target.value)))}
            />
          </Field>

          <Field label={`Belirme / kaybolma — ${active.fadeInSeconds.toFixed(1)} sn / ${active.fadeOutSeconds.toFixed(1)} sn`}>
            <div className="color-row">
              <input
                type="number"
                min={0}
                max={5}
                step={0.1}
                value={active.fadeInSeconds}
                onChange={(e) => editText((t) => void (t.fadeInSeconds = Number(e.target.value)))}
                aria-label="Belirme süresi (saniye)"
              />
              <input
                type="number"
                min={0}
                max={5}
                step={0.1}
                value={active.fadeOutSeconds}
                onChange={(e) => editText((t) => void (t.fadeOutSeconds = Number(e.target.value)))}
                aria-label="Kaybolma süresi (saniye)"
              />
            </div>
          </Field>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={active.shadow}
            onChange={(e) => editText((t) => void (t.shadow = e.target.checked))}
          />
          Gölge
        </label>
        {active.shadow && (
          <div className="field-grid">
            <Field label={`Gölge yumuşaklığı — ${active.shadowBlur}px`}>
              <input
                type="range"
                min={0}
                max={64}
                step={1}
                value={active.shadowBlur}
                onChange={(e) => editText((t) => void (t.shadowBlur = Number(e.target.value)))}
              />
            </Field>
            <Field label={`Gölge kayması — ${active.shadowOffset}px`}>
              <input
                type="range"
                min={0}
                max={40}
                step={1}
                value={active.shadowOffset}
                onChange={(e) => editText((t) => void (t.shadowOffset = Number(e.target.value)))}
              />
            </Field>
          </div>
        )}

        <label className="checkbox">
          <input
            type="checkbox"
            checked={active.outlineWidth > 0}
            onChange={(e) => editText((t) => void (t.outlineWidth = e.target.checked ? 2 : 0))}
          />
          Yazı kenarlığı
        </label>
        {active.outlineWidth > 0 && (
          <div className="field-grid">
            <Field label={`Kenarlık kalınlığı — ${active.outlineWidth}px`}>
              <input
                type="range"
                min={1}
                max={12}
                step={1}
                value={active.outlineWidth}
                onChange={(e) => editText((t) => void (t.outlineWidth = Number(e.target.value)))}
              />
            </Field>
            <Field label="Kenarlık rengi">
              <div className="color-row">
                <input
                  type="color"
                  className="color-swatch"
                  value={active.outlineColor}
                  onChange={(e) => editText((t) => void (t.outlineColor = e.target.value.toUpperCase()))}
                  aria-label="Kenarlık rengi"
                />
                <input
                  value={active.outlineColor}
                  pattern="^#[0-9A-Fa-f]{6}$"
                  onChange={(e) => editText((t) => void (t.outlineColor = e.target.value.toUpperCase()))}
                />
              </div>
            </Field>
          </div>
        )}

        <label className="checkbox">
          <input
            type="checkbox"
            checked={active.box}
            onChange={(e) => editText((t) => void (t.box = e.target.checked))}
          />
          Yazının arkasına kutu koy
        </label>
        {active.box && (
          <div className="field-grid">
            <Field label="Kutu rengi">
              <div className="color-row">
                <input
                  type="color"
                  className="color-swatch"
                  value={active.boxColor}
                  onChange={(e) => editText((t) => void (t.boxColor = e.target.value.toUpperCase()))}
                  aria-label="Kutu rengi"
                />
                <input
                  value={active.boxColor}
                  pattern="^#[0-9A-Fa-f]{6}$"
                  onChange={(e) => editText((t) => void (t.boxColor = e.target.value.toUpperCase()))}
                />
              </div>
            </Field>
            <Field label={`Kutu koyuluğu — ${active.boxOpacity.toFixed(2)}`}>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={active.boxOpacity}
                onChange={(e) => editText((t) => void (t.boxOpacity = Number(e.target.value)))}
              />
            </Field>
            <Field label={`Yatay boşluk — ${active.boxPaddingX}px`}>
              <input
                type="range"
                min={0}
                max={200}
                step={2}
                value={active.boxPaddingX}
                onChange={(e) => editText((t) => void (t.boxPaddingX = Number(e.target.value)))}
              />
            </Field>
            <Field label={`Dikey boşluk — ${active.boxPaddingY}px`}>
              <input
                type="range"
                min={0}
                max={200}
                step={2}
                value={active.boxPaddingY}
                onChange={(e) => editText((t) => void (t.boxPaddingY = Number(e.target.value)))}
              />
            </Field>
            <Field label={`Köşe yuvarlaklığı — ${active.boxRadius}px`}>
              <input
                type="range"
                min={0}
                max={80}
                step={1}
                value={active.boxRadius}
                onChange={(e) => editText((t) => void (t.boxRadius = Number(e.target.value)))}
              />
            </Field>
          </div>
        )}

        {group === 'subtitles' && (
          <div className="subtitle-extra">
            <h3>Altyazı satırları</h3>
            <p className="muted">
              Altyazı metninin nasıl bölüneceğini ve her satırın ekranda ne kadar kalacağını
              belirler.
            </p>
            <div className="field-grid">
              <Field label={`Satırda en fazla harf — ${(style.subtitles as SubtitleStyle).maxCharsPerLine}`}>
                <input
                  type="range"
                  min={16}
                  max={90}
                  step={1}
                  value={(style.subtitles as SubtitleStyle).maxCharsPerLine}
                  onChange={(e) => editSubtitle((t) => void (t.maxCharsPerLine = Number(e.target.value)))}
                />
              </Field>
              <Field label={`En fazla satır — ${(style.subtitles as SubtitleStyle).maxLines}`}>
                <input
                  type="range"
                  min={1}
                  max={4}
                  step={1}
                  value={(style.subtitles as SubtitleStyle).maxLines}
                  onChange={(e) => editSubtitle((t) => void (t.maxLines = Number(e.target.value)))}
                />
              </Field>
              <Field label={`En kısa süre — ${(style.subtitles as SubtitleStyle).minCueSeconds.toFixed(1)} sn`}>
                <input
                  type="range"
                  min={0.3}
                  max={5}
                  step={0.1}
                  value={(style.subtitles as SubtitleStyle).minCueSeconds}
                  onChange={(e) => editSubtitle((t) => void (t.minCueSeconds = Number(e.target.value)))}
                />
              </Field>
              <Field label={`En uzun süre — ${(style.subtitles as SubtitleStyle).maxCueSeconds.toFixed(1)} sn`}>
                <input
                  type="range"
                  min={1}
                  max={15}
                  step={0.1}
                  value={(style.subtitles as SubtitleStyle).maxCueSeconds}
                  onChange={(e) => editSubtitle((t) => void (t.maxCueSeconds = Number(e.target.value)))}
                />
              </Field>
              <Field
                label={`Okuma hızı sınırı — saniyede ${(style.subtitles as SubtitleStyle).maxCharsPerSecond.toFixed(0)} harf`}
                hint="Daha hızlı akacak satırlar biraz uzatılır."
              >
                <input
                  type="range"
                  min={5}
                  max={40}
                  step={1}
                  value={(style.subtitles as SubtitleStyle).maxCharsPerSecond}
                  onChange={(e) => editSubtitle((t) => void (t.maxCharsPerSecond = Number(e.target.value)))}
                />
              </Field>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
