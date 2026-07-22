# Architecture

EVB is a local web app: a FastAPI backend and a React (Vite) frontend, talking
over HTTP on localhost. All state is files on disk under the data directory —
there is no database.

```
frontend (React/Vite, :5173)  ──HTTP/SSE──▶  backend (FastAPI, :8756)  ──▶  FFmpeg / Pillow / edge-tts
        │                                            │
        └── autosaving project store                 └── project.json + media files on disk
```

In development (`./dev.sh`) the Vite dev server on :5173 proxies `/api` and
`/media` to the backend. In production (`./prod.sh`) there is no dev server: the
frontend is built to `frontend/dist` and `main.mount_frontend()` serves it from
the backend, so the app and the API share one origin on :8756. That single-port,
dependency-free server is what a future Tauri/Electron shell would wrap.

## Backend (`backend/app`)

| Area | What it does |
|---|---|
| `api/` | HTTP routers: `projects`, `audio`, `render`, `diagnostics`, `settings_api`. |
| `models/` | Pydantic schema (`project.py`), enums, and schema migrations. camelCase on the wire, snake_case in Python. |
| `storage/` | The on-disk layout (`layout.py`), path safety (`paths.py`), media I/O (`media.py`), the project repository (`repository.py`), and content-package import. |
| `tts/` | Provider abstraction (Edge / ElevenLabs / imported) with content-hash caching. |
| `timing/` | The Timeline (`schedule.py`), audio probing, and subtitle cue building. |
| `synth/` | The basic generated ambient music bed. |
| `render/` | The render pipeline and its stages. |
| `shorts/` | Cutting a vertical 9:16 Short out of a **finished** long render. Its own models, planner, pipeline and job queue. |

### The render pipeline (`render/pipeline.py`)

Fourteen ordered stages:

1. validate the project
2. verify source files
3. generate missing narration
4. probe audio durations
5. **compute the Timeline** ← single source of truth for all timing
6. build subtitle cues
7. preflight disk space
8. render scene clips (Pass A, cached per-scene)
9. assemble with transitions (Pass B)
10. mix audio on the same Timeline
11. encode the final file
12. validate the output with ffprobe
13. write artifacts (SRT, narration audio, description, thumbnail prompt, log)
14. clean up

Nothing after stage 5 recomputes a duration or offset. Scene clips are cached by
a key covering everything that affects their pixels, so re-rendering only redoes
what changed.

### Shorts (`shorts/`)

A Short is **never** a second render. It is cut out of an MP4 the long pipeline
already finished and validated, so the mixed narration, music, burned-in
subtitles and in-scene transitions arrive exactly as they were produced — there
is no second TTS pass, no second music mix and no second subtitle pass.

The long pipeline's only involvement is one additive call in stage 13: it writes
a **render manifest** (`<export>-manifest.json`) beside the MP4. That manifest is
immutable, versioned (`schemaVersion`) and bound to one exact file by size,
SHA-256 and an ffprobe summary. It records every section's absolute
`startSeconds`/`endSeconds`, its user-visible number (intro `0`, active scenes
`1..N`, outro `N+1`), the transition into the next section, and a **safe
window** — the section minus any transition overlap it shares with a neighbour.
Before a Short is cut, the file on disk is checked against the manifest again; a
deleted, edited, truncated or unreadable export is a `stale_render` error, never
a bad cut.

Section boundaries therefore never come from guessing at the container duration.
That matters because a transition of duration `d` *overlaps* two sections, so
those frames belong to both:

- Sections selected back-to-back with no trim at the join merge into **one
  contiguous cut**, taken out of the finished video in a single span. The
  transition between them survives untouched.
- Separate cuts are clamped to the safe window, so a non-contiguous selection can
  never carry half a dissolve from a section the user did not pick.
- Nothing is added between cuts — no fade, no dip, no effect the source does not
  already contain. `ShortLayout.groupGapFadeSeconds` is the extension point for
  an optional micro-fade later, and is locked to zero.

The pipeline cuts each group frame-accurately (near-lossless re-encode by
default; a stream copy only when ffprobe proves the start is keyframe-aligned),
concatenates in selection order with a re-encode fallback, then places the
horizontal picture centred on a 1080×1920 black canvas in one `filter_complex` —
1920×1080 lands at 1080×608 with 656 rows of black above and below, never
stretched, cropped or zoomed. Output is validated with ffprobe (geometry, CFR,
frame rate, H.264/yuv420p, AAC 48 kHz, non-silent audio, duration, aspect
preserved) and only then moved into `exports/shorts/` with a single atomic
rename, so a cancelled or failed Short can never appear as complete.

Storage is separate and explicit: `derived/shorts-cache/` for regenerable cuts,
proxies and preview frames, `exports/shorts/` for finished Shorts and their
side-cars. The long render's exports and `derived/clips/` are read-only to this
package. Shorts are content-addressed by a cache key over the source checksum,
the normalised cut list, the layout and the encoder, so an identical request
reuses the existing file instead of encoding again, and a changed source
invalidates it.

Shorts run on their own queue with their own on-disk history and SSE contract,
but share a process-wide render slot (`render/slot.py`) with the long-render
queue, so only one CPU-heavy FFmpeg job runs at a time whichever kind it is.

### Shorts captions

The long video's subtitles are sized for a 1920×1080 frame. Centred on a
1080×1920 canvas that frame becomes 1080×608 — the same captions at roughly a
third of the height, which is unreadable on a phone. And they cannot be taken
back out: **captions burned into an MP4 are permanent**, and OCR, inpainting,
cropping and blur masks all destroy picture the user deliberately rendered. So
this is built as a clean-source capability, never as subtitle removal.

Each Short chooses a `captionMode`:

| Mode | Source cut | Captions |
|---|---|---|
| `source-burned-in` *(default)* | the finished captioned export | whatever is already in the picture |
| `shorts-native` | the render's **clean master** | drawn fresh on the 9:16 canvas |
| `off` | the render's **clean master** | none |

A request that omits `captionMode` behaves — and hashes — exactly as it did
before the feature existed, so every Short already on disk keeps matching its own
request.

**The Shorts-ready source package.** With `export.prepareCleanMasterForShorts`
on, a completed render also produces, in `exports/shorts-source/`:

- a **clean master** — the same images, Ken Burns motion, titles, scene text,
  watermark, scrim, fades, transitions, timing, codec profile, frame rate and
  audio mix, with narration subtitles switched off and nothing else changed. It
  reuses the scene-clip machinery under its own cache namespace
  (`derived/clips/clean/`), so neither clip cache can evict the other's files.
  When the export has no burned-in subtitles it *is* the clean master, recorded
  as `origin: "primary-export"` and hard-linked at zero cost; otherwise a second
  pass runs and the render takes roughly twice as long.
- a **cue side-car** (`<clean-master>-shorts-cues.json`) — the exact
  `Timeline.cues` that render used, with absolute times, `unitId`, lines,
  a schema version and a content hash. Shorts never look for a casually named
  `.srt`; the ordinary `.srt` and per-scene `.srt` exports are unchanged and
  remain user artifacts.

Both are named explicitly in the render manifest (v2, `shortsSource`), each with
its own SHA-256, ffprobe summary, profile and binding to the render job and
project snapshot. Before anything is cut, all of it is verified: existence, size,
checksum, schema versions, geometry, frame rate, duration and the pairing between
side-car and master. Any mismatch is a `short_clean_source_stale` error. A render
with no package at all is `short_captions_unavailable`, with the actionable
message *"This render only has burned-in captions. Re-render the long video with
a Shorts-ready clean master to use large Shorts captions."* **There is no
fallback path to the captioned export** — that would caption the Short twice.

**Drawing.** Cues are clipped to the spans actually cut and rebased onto the
Short's own clock: groups play in the user's chosen order, a cue straddling a
preserved in-source transition is carried through once, a cue clipped by a trim
keeps only its surviving part, genuinely overlapping cues from a dissolve stay
overlapping, and slivers under 120 ms are dropped. Cards are drawn by the same
Pillow path as every other overlay (`render/text.py`), at one type size fitted so
every cue in the Short fits `maxLines`, and composited **after** the pad — in
canvas coordinates, bottom-centre, in the black band below the picture, clear of
the Shorts scrubber and the like/comment rail. Above twelve cards the track is
pre-composited into one QT RLE alpha video first, mirroring what the long
pipeline does for a dense scene. Caption text only ever exists as pixels in a
PNG; nothing user-supplied reaches a filtergraph.

The Short's cache key gains a `captions` block — mode, normalised style, clean
master checksum, cue schema and cue content hash, and a renderer version — only
in the modes where captions change the output.

### Subtitle timing

Cues are placed from **measured word boundaries** whenever the TTS provider
reports them (Edge is asked for word granularity explicitly). Those timings are
written to a `.timings.json` beside the audio, keyed by the same content hash,
so a render that reuses cached narration still gets them — narration is
generated on the Audio tab and the render usually happens later, so without that
persistence every cue would silently fall back to estimation.

The estimator is the fallback for audio nobody measured, mainly imported clips.
It splits the narration by speech weight (characters, word count and the pause
each punctuation mark implies) and lays the result across the audio *starting at
the first sound*, not at the file's first sample — a take opens with roughly
0.15s of silence, and treating that as speaking time puts the opening subtitle
on screen before a word is said and carries the lead through the scene.

Measured against real word boundaries on a three-scene sample: estimation ran a
mean of +0.18s and up to +0.66s ahead of the voice; the leading-silence
correction halves the mean bias, and real timings remove the error entirely.
A render whose generated narration predates the stored timings reports a warning
saying so, because regenerating narration once fixes it.

### Text without `drawtext`

All on-screen text is drawn by Pillow (`render/text.py`) into transparent PNGs
that FFmpeg composites via `overlay`. Fonts are **bundled** (Inter), so text
renders identically on every machine and EVB works on FFmpeg builds compiled
without libfreetype/libass. A requested font the machine lacks is reported as a
substitution, never silently swapped.

### Motion

Ken Burns pan/zoom is computed in `render/kenburns.py` and applied with FFmpeg's
`zoompan` over a supersampled working image, which keeps the motion sub-pixel
smooth at 60 fps. `animationPreset: auto` derives a deterministic per-scene
variation so a whole video doesn't use the same move.

## Frontend (`frontend/src`)

| Area | What it does |
|---|---|
| `App.tsx` | Shell: sidebar nav, top bar, routed centre pane. |
| `routes/` | One component per tab (Projects, Content, Scenes, Audio, Music, Style, Export, Shorts, Settings). |
| `components/` | Shared UI: `Diagnostics`, `ErrorBox`, `ConfirmDialog`. |
| `store/` | Zustand stores: `project` (autosaving editor state), `render` (SSE-driven job state) and `shorts` (its own job state, kept separate so a Short's progress can never overwrite a render's). |
| `api/` | The typed API client and hand-written wire types mirroring the backend models. |

### Autosave

Edits apply to local state immediately and flush to the backend on a debounce;
the save status is always visible, and a failed save keeps the change dirty so
it's retried rather than dropped (`store/project.ts`).

### Live render progress

A render is driven by a server-sent event stream. If the stream drops (reload,
sleeping laptop), the store falls back to polling and reattaches, so a render in
progress is never lost from the UI (`store/render.ts`). The Shorts store gets the
same behaviour from the shared `lib/jobStream.ts` helper against the
`/api/short-jobs` endpoints.

## Errors

Every failing endpoint returns a structured payload — `code`, `message`,
`details`, `suggestion`, `logPath`, `context` — and the frontend renders all of
them through a single `ErrorBox`: what happened, what to do, where the log is,
and (collapsed) the technical detail.

## Schema versions and compatibility

Four independent versions, each with its own reader rule: accept everything you
know, refuse only what is newer than you.

| Schema | Current | Where | Migration |
|---|---|---|---|
| `project.json` `schemaVersion` | **2** | `models/project.py` | chained functions in `models/migrations.py` |
| render manifest `schemaVersion` | **2** | `shorts/manifest.py` | none needed — every v2 field is optional |
| Shorts source package `packageVersion` | 1 | `shorts/manifest.py` | — |
| cue side-car `schemaVersion` | 1 | `shorts/cues.py` | — |

**project v1 → v2** added `export.prepareCleanMasterForShorts`. New projects
default it **on**: large Shorts captions are the point of the Shorts tab, the
option has to be set *before* a render, and a render that did not prepare a clean
master can never gain one afterwards — so defaulting it off would block nearly
every first Short on a re-render. But it costs a second full pass whenever
subtitles are burned in, so the migration writes it **off** for projects created
before it existed. Opening an old project never signs it up for extra work; the
Export tab states the cost either way.

**Render manifest v1 → v2** added the optional `shortsSource` package and
`sourceHasBurnedInSubtitles`. There is no migration and none is needed: a v1
manifest validates unchanged, still lists as a Shorts source, still cuts legacy
Shorts exactly as before, and simply reports that native captions are
unavailable. Old render history keeps parsing. Only a manifest from a *newer*
build is refused, as `unsupported_schema_version`.

## Testing

- Backend: `cd backend && .venv/bin/python -m pytest`. Tests that need a live
  TTS network call are skipped unless `EVB_TEST_NETWORK=1`. Render tests need
  FFmpeg on `PATH`.
- Frontend: `cd frontend && npm test` (Vitest + Testing Library). A shared typed
  project fixture lives in `src/test/factories.ts`.
