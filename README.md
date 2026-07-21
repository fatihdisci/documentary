# Extinct Video Builder

A local-first desktop web app that turns a folder of AI-generated images plus a
content package into a polished **1920×1080, constant 60 FPS** documentary MP4
about extinct animals — with generated voiceover, audio-driven scene timing, Ken
Burns motion, transitions, text overlays, subtitles and ducked background music.

Everything runs on your machine. No cloud service is required for any part of
the core workflow, and the basic path uses no paid APIs.

---

## Requirements

| Tool | Version | Install |
|---|---|---|
| macOS | 13+ (developed on 26.5) | — |
| Python | 3.11 | `brew install python@3.11` |
| Node.js | 20+ (developed on 22) | `brew install node` |
| FFmpeg + ffprobe | 6+ (developed on 8.1.1) | `brew install ffmpeg` |

> **A note on FFmpeg builds.** This app renders **all** text — titles, subtitles,
> captions, watermarks — with Pillow into transparent PNGs that FFmpeg
> composites. It therefore works correctly on FFmpeg builds compiled *without*
> `libfreetype`/`libass` (no `drawtext`, no `subtitles` filter), which is exactly
> the case on the machine it was developed on. The Diagnostics page tells you
> what your build supports.

## Install

```bash
git clone <this repo> documentary
cd documentary
./dev.sh --setup      # creates backend/.venv, installs Python + npm deps
```

You need FFmpeg on your machine before this step — see [Requirements](#requirements)
above. `./dev.sh --setup` installs the Python and Node dependencies only.

## Run

### Option A — double-click (macOS, easiest)

Double-click **`Launch EVB.command`** in Finder (or in the repo folder from
Terminal: `open "Launch EVB.command"`).

- First run installs everything automatically (a few minutes, one-time —
  equivalent to `./dev.sh --setup`).
- Every run after that starts in a few seconds.
- A Terminal window opens showing backend/frontend logs, and your browser
  opens automatically to the app once the backend is ready.
- **Keep that Terminal window open** while you use the app — closing it (or
  pressing <kbd>Ctrl</kbd>+<kbd>C</kbd> inside it) stops both servers.

If macOS refuses to run it the first time ("cannot be opened because it is
from an unidentified developer"), right-click the file → **Open** → **Open**
in the dialog. You only need to do this once.

### Option B — from the terminal

```bash
./dev.sh
```

Both commands start the same two servers:

- Frontend: <http://localhost:5173>
- Backend API: <http://127.0.0.1:8756> (interactive docs at `/docs`)

Open the app and go to **Diagnostics** first. It probes your FFmpeg binary, disk
space, storage permissions and narration sources, and reports exactly what it
found. If it says *Ready to render*, you are good.

## Using the app

1. **Diagnostics** — confirm the environment is ready (see above). Fix
   anything marked failed before continuing.
2. **Projects** — click **Create project**, give it a name.
3. **Content** — import a content package JSON (narration, titles, image
   prompts for every scene). Use **Download example template** for the
   documented format and a working sample (the Dodo), or see
   [`docs/content-schema.md`](docs/content-schema.md).
4. **Scenes** — upload your ~10 images (drag-and-drop works). Name them with a
   numeric prefix, e.g. `01-opening.png`, `02-habitat.png`, so they map onto
   scenes in the right order automatically. Reorder scenes by dragging their
   cards; click **Auto-map images** if you add or reorder images afterwards.
5. **Audio** — pick a TTS provider and voice (Edge TTS is free and needs no
   API key), then **Generate missing** to synthesize narration for every
   scene. No internet? Upload your own audio file per scene instead — the app
   works fully offline that way. This tab also shows the computed video
   runtime and lets you choose background music (none / your own upload / a
   basic generated ambient bed).
6. **Export** — check the preflight panel (it lists anything blocking a
   render), pick a quality preset, and press **Render video**. Progress
   streams live; you can cancel or retry at any point. Finished renders and
   every side-car file (SRT, narration-only audio, description, thumbnail
   prompt, render log) are listed below with one-click downloads.

Projects, images, generated audio and every export live under
`~/ExtinctVideoBuilder` (see below) — nothing leaves your machine.

## Where your data lives

By default everything is under `~/ExtinctVideoBuilder`:

```
~/ExtinctVideoBuilder/
├── projects/     one folder per project (project.json + images + audio)
├── exports/      finished MP4s, SRTs and side-car files
├── temp/         render scratch (safe to delete)
├── cache/        derived assets
├── music/        your background music library
├── logs/         backend.log and per-render logs
└── settings.json
```

Override with `EVB_DATA_DIR`, or change the individual directories on the
Settings page.

API keys are stored in `secrets.json` with `0600` permissions. They are never
returned by any endpoint, written to a log, or included in a project bundle.

## Tests

```bash
cd backend && .venv/bin/python -m pytest      # backend (494 passing)
cd frontend && npm test                       # frontend (21 passing)
```

Backend tests that need a real TTS network call are skipped by default; run
with `EVB_TEST_NETWORK=1` to exercise live Edge TTS as well.

## Documentation

Further docs live in `docs/` and are written as the corresponding features land.

- [`docs/content-schema.md`](docs/content-schema.md) — the importable content
  package format (narration, titles, image prompts, framing hints)

## Project status

Built in milestones; see the sections of the app that are live in Diagnostics
and the navigation sidebar. M1–M6 are complete and pushed; M7 is the remaining
hardening pass before this is a finished product.

- **M1 — Foundation** ✅ repo, schema, backend + frontend skeletons, diagnostics
- **M2 — Projects & content** ✅ project CRUD, scenes, image pipeline, content
  package import with natural-order image mapping
- **M3 — Audio & subtitles** ✅ TTS provider abstraction (Edge / imported /
  ElevenLabs), content-hash caching, the timeline as single source of truth,
  non-uniform subtitle timing
- **M4 — Motion & text** ✅ Ken Burns pan/zoom with deterministic auto-variation,
  Pillow text cards (replaces `drawtext`, which this FFmpeg build lacks), the
  render smoke test gate — passed
- **M5 — Render pipeline** ✅ full 14-stage pipeline, benchmarked intermediate
  codec, transitions, audio mixing with ducking, per-scene clip caching,
  output validation against the real file
- **M6 — Jobs & export** ✅ background render queue, live SSE progress,
  cancellation, crash recovery, render history, the Export page
- **M7 — Hardening** ⬜ *in progress* — see below

### What's left (M7)

- [ ] **Simple mode** — a guided, minimal-decision path through the six-step
  workflow (import content → upload images → pick voice → pick music →
  generate audio → render) for a first-time user, distinct from the full
  editor
- [x] **Style page** — font/size/colour/position controls for titles,
  subtitles and captions (each with shadow, outline and background-box
  options), the default transition picker, subtitle cue-timing bounds,
  watermark and scrim — all backed by the existing render pipeline, with a
  live text preview and a per-class reset
- [ ] **Frontend canvas preview** — an in-browser Ken Burns/text preview that
  mirrors the backend's motion math, so scenes can be scrubbed without
  rendering a proxy clip
- [ ] **Error taxonomy polish in the UI** — audit every screen for the
  message/details/suggestion/log-path pattern used on Diagnostics and Export;
  a few older pages (Content, Scenes) still show plain fetch errors in places
- [ ] **Dark/light theme polish** — the toggle exists and both palettes are
  defined in `theme.css`, but light mode hasn't had a full visual pass
- [ ] **End-user documentation** — install guide, user guide, architecture
  doc, FFmpeg/render troubleshooting (currently only the content schema is
  written up)
- [ ] **Packaging** — a production build/launch path beyond `./dev.sh`
  (the plan keeps this Tauri/Electron-ready but nothing is wired up yet)
- [ ] **Frontend test coverage** — most new pages (Audio, Export, Content,
  Scenes) don't have component tests yet; Diagnostics, the Style page and the
  project store do
- [ ] **Music library UI** — uploading/managing tracks in `music/` has a
  backend endpoint but no dedicated screen yet
