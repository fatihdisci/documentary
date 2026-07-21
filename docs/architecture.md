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
| `routes/` | One component per tab (Projects, Content, Scenes, Audio, Music, Style, Export, Settings). |
| `components/` | Shared UI: `Diagnostics`, `ErrorBox`, `ConfirmDialog`. |
| `store/` | Zustand stores: `project` (autosaving editor state) and `render` (SSE-driven job state). |
| `api/` | The typed API client and hand-written wire types mirroring the backend models. |

### Autosave

Edits apply to local state immediately and flush to the backend on a debounce;
the save status is always visible, and a failed save keeps the change dirty so
it's retried rather than dropped (`store/project.ts`).

### Live render progress

A render is driven by a server-sent event stream. If the stream drops (reload,
sleeping laptop), the store falls back to polling and reattaches, so a render in
progress is never lost from the UI (`store/render.ts`).

## Errors

Every failing endpoint returns a structured payload — `code`, `message`,
`details`, `suggestion`, `logPath`, `context` — and the frontend renders all of
them through a single `ErrorBox`: what happened, what to do, where the log is,
and (collapsed) the technical detail.

## Testing

- Backend: `cd backend && .venv/bin/python -m pytest`. Tests that need a live
  TTS network call are skipped unless `EVB_TEST_NETWORK=1`. Render tests need
  FFmpeg on `PATH`.
- Frontend: `cd frontend && npm test` (Vitest + Testing Library). A shared typed
  project fixture lives in `src/test/factories.ts`.
