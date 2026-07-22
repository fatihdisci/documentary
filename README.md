# Extinct Video Builder

A local-first desktop web app that turns a folder of AI-generated images plus a
content package into a polished **1920×1080, constant 60 FPS** documentary MP4
about extinct animals — with generated voiceover, audio-driven scene timing, Ken
Burns motion, transitions, text overlays, subtitles and ducked background music.

Everything runs on your machine. No cloud service is required for any part of
the core workflow, and the basic path uses no paid APIs.

A **Shorts** tab turns any completed render into a 1080×1920 vertical clip by
cutting the sections you pick out of the finished video — no re-render, so the
narration, music and transitions come through untouched. Captions can either be
the ones already in the picture, or large Shorts-native ones drawn on the
vertical canvas.

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

### Production (single server)

To just *use* the app rather than develop it, run the production build — one
process serves the built frontend and the API from the same origin, so there's
no Vite dev server and nothing to proxy:

```bash
./prod.sh            # builds frontend/dist if needed, serves http://127.0.0.1:8756
./prod.sh --build    # force a fresh frontend build first
```

macOS users can double-click **`Launch EVB (Production).command`** instead. This
single-origin server is also the natural thing to wrap in a Tauri/Electron shell
later — the app already runs from one port with no external dependencies.

Open the app and go to **Diagnostics** first. It probes your FFmpeg binary, disk
space, storage permissions and narration sources, and reports exactly what it
found. If it says *Ready to render*, you are good.

## Using the app

New here? Opening a project drops you on the **Guided setup** tab — a six-step
stepper (content → images → voice → music → narration → render) that walks the
whole workflow with minimal decisions. Click **Switch to the full editor** any
time for the tabs below, which give you the fine controls.

1. **Diagnostics** — confirm the environment is ready (see above). Fix
   anything marked failed before continuing.
2. **Projects** — click **Create project**, give it a name.
3. **Content** — import a content package JSON (narration, titles, image
   prompts for every scene). Use **Download example template** for the
   documented format and a working sample (the Dodo), or see
   [`docs/content-schema.md`](docs/content-schema.md).
4. **Scenes** — upload your images (drag-and-drop works). Give **one more image
   than you have scenes** — e.g. 11 images for a 10-scene video — and the first
   becomes the intro's own picture, so the opening and the first scene no longer
   share a frame. Name them with a numeric prefix so they sort in order — the
   simplest layout keeps your scene names and prepends the intro:
   `00-intro.png`, `01-opening.png`, `02-habitat.png`, …. (With exactly one image
   per scene the intro simply reuses the first scene's image, as before.) Reorder
   scenes by dragging their cards; click **Auto-map images** if you add or
   reorder images afterwards.
5. **Audio** — pick a TTS provider and voice (Edge TTS is free and needs no
   API key), then **Generate missing** to synthesize narration for every
   scene. No internet? Upload your own audio file per scene instead — the app
   works fully offline that way. This tab also shows the computed video
   runtime and the mixing controls (voice/music levels, ducking, loudness), and
   the **Burn subtitles into the video** toggle — on by default, so a finished
   video is captioned without any extra steps (an `.srt` is exported either way).
6. **Music** *(optional)* — upload one or more background tracks, audition
   them in the browser, and pick the one the render uses. Or leave it on
   *No music* / the basic generated ambient bed.
7. **Style** *(optional)* — tune the look of every overlay: font, size,
   colour, position, drop shadow, outline and background box for titles,
   subtitles and captions (each editable on its own tab, with a live preview
   and a per-class reset), plus the default scene transition, subtitle
   cue-timing bounds, the readability scrim and an optional watermark. Sensible
   defaults ship out of the box, so you can skip this entirely.
8. **Export** — check the preflight panel (it lists anything blocking a
   render), pick a quality preset, and press **Render video**. Progress
   streams live; you can cancel or retry at any point. The **Preview** preset is
   a genuinely fast check — it renders at 30 FPS with light supersampling
   (roughly 8× quicker than a full export) into its own cache, so it never
   throws away the clips a full render built. Finished renders and every side-car
   file (SRT, narration-only audio, description, thumbnail prompt, render log)
   are listed below with one-click downloads.
9. **Shorts** *(optional)* — pick a completed render, tick the sections you want
   in the order you want them (intro is `0`, scenes are `1…N`, outro is `N+1`),
   trim each one inside its safe range, and press **Render Short**. The result is
   a 1080×1920 MP4 with the 16:9 picture centred on black at its original aspect
   ratio. Section boundaries come from the render's own manifest, so adjacent
   picks keep the transition between them exactly as rendered. 25–50 seconds is
   the recommended length; over 60 seconds you get a Content ID warning, and over
   three minutes YouTube no longer treats it as a Short so the render is blocked.
   **Captions**: keep the ones burned into the video (always available), or — if
   the render kept a caption-free *clean master*, which the Export tab prepares
   by default for new projects — have them redrawn large at the bottom of the
   vertical frame. Burned-in captions cannot be removed from an old render, so a
   render made without a clean master says so and offers the legacy option.

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
cd backend && .venv/bin/python -m pytest      # backend (631 passing)
cd frontend && npm test                       # frontend (100 passing)
```

Backend tests that need a real TTS network call are skipped by default; run
with `EVB_TEST_NETWORK=1` to exercise live Edge TTS as well.

## Documentation

Further docs live in `docs/`:

- [`docs/install.md`](docs/install.md) — install, run, data directory and
  configuration precedence
- [`docs/user-guide.md`](docs/user-guide.md) — the full tab-by-tab workflow for
  building one video
- [`docs/architecture.md`](docs/architecture.md) — how the backend, frontend and
  14-stage render pipeline fit together
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — FFmpeg builds, render
  failures and the error codes behind common problems
- [`docs/content-schema.md`](docs/content-schema.md) — the importable content
  package format (narration, titles, image prompts, framing hints)

## Project status

Built in milestones; see the sections of the app that are live in Diagnostics
and the navigation sidebar. M1–M7 are complete and pushed.

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
- **M7 — Hardening** ✅ simple/guided mode, Style editor, music library, canvas
  preview, error-taxonomy audit, theme pass, docs, test coverage, packaging
- **M8 — Shorts** ✅ a vertical 9:16 cut-down built from a completed render and
  its versioned manifest, with its own planner, pipeline, job queue and page —
  the long render pipeline is untouched apart from writing that manifest
- **M9 — Shorts captions** ✅ an opt-in caption-free *clean master* and immutable
  cue side-car written beside a render, so a Short can draw its own large
  captions on the 9:16 canvas instead of shrinking the burned-in ones. Legacy
  renders keep working unchanged and are told, in plain words, why they cannot
  use the new mode — burned-in captions are never removed from a finished file

### M7 detail

- [x] **Simple mode** — a **Guided setup** tab: a six-step stepper (content →
  images → voice → music → narration → render) that drives the same API and
  stores as the full editor, shows per-step completion, and links out to each
  full tab. Opening a project lands here; "Switch to the full editor" leaves it
- [x] **Style page** — font/size/colour/position controls for titles,
  subtitles and captions (each with shadow, outline and background-box
  options), the default transition picker, subtitle cue-timing bounds,
  watermark and scrim — all backed by the existing render pipeline, with a
  live text preview and a per-class reset
- [x] **Frontend canvas preview** — a scrubbable in-browser Ken Burns + text
  preview on the Scenes page. The geometry comes from a `/motion` endpoint (the
  exact numbers the render uses, same ordering and `auto` resolution), so the
  frontend only mirrors the smoothstep easing — guarded by a backend-parity
  unit test
- [x] **Error taxonomy polish in the UI** — every screen now routes failures
  through the single `ErrorBox` (code / message / suggested fix / log path /
  collapsible details), including the Content and Scenes pages; component
  tests lock the structured-error behaviour in place
- [x] **Dark/light theme polish** — every screen was reviewed in both themes
  (all surfaces read from the `theme.css` palette variables); light mode came
  through cleanly, and the Music tab's source toggle was tidied up so its
  active state no longer looks disabled
- [x] **End-user documentation** — install guide, tab-by-tab user guide,
  architecture doc and an FFmpeg/render troubleshooting guide, all under
  `docs/` and linked above
- [x] **Packaging** — `./prod.sh` (and a `Launch EVB (Production).command`)
  build the frontend and serve it plus the API from one process on a single
  origin — no Vite dev server. That single-port, no-external-deps server is the
  natural thing to wrap in Tauri/Electron next
- [x] **Frontend test coverage** — component tests now cover Content, Scenes,
  Audio, Export, Music and Style alongside Diagnostics and the project store,
  with a shared typed project fixture (`src/test/factories.ts`)
- [x] **Music library UI** — a dedicated Music tab to upload, audition, delete
  and pick the background track; deleting the selected track clears the
  project reference server-side so a stale path can't break a render
