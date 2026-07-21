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

## Run

```bash
./dev.sh
```

- Frontend: <http://localhost:5173>
- Backend API: <http://127.0.0.1:8756> (docs at `/docs`)

Open the app and go to **Diagnostics** first. It probes your FFmpeg binary, disk
space, storage permissions and narration sources, and reports exactly what it
found. If it says *Ready to render*, you are good.

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
cd backend && .venv/bin/python -m pytest      # backend
cd frontend && npm test                       # frontend
```

## Documentation

Further docs live in `docs/` and are written as the corresponding features land.

## Project status

Built in milestones; see the sections of the app that are live in Diagnostics
and the navigation sidebar.

- **M1 — Foundation** ✅ repo, schema, backend + frontend skeletons, diagnostics
- M2 — Projects, scenes, images, content JSON import
- M3 — TTS providers, timing engine, subtitles
- M4 — Ken Burns motion, Pillow text cards, preview, render smoke test
- M5 — Full render pipeline
- M6 — Background jobs, progress, cancellation, exports
- M7 — Hardening, docs, UI polish
