# Install & run

Extinct Video Builder (EVB) is a local-first desktop web app. It runs two
local servers — a Python backend and a Vite-served frontend — and does all its
work on your machine. Nothing is uploaded anywhere.

## Requirements

| Tool | Version | Install (macOS) |
|---|---|---|
| macOS | 13+ (developed on 26.5) | — |
| Python | 3.11 | `brew install python@3.11` |
| Node.js | 20+ (developed on 22) | `brew install node` |
| FFmpeg + ffprobe | 6+ (developed on 8.1.1) | `brew install ffmpeg` |

FFmpeg must be installed **before** you set up the app. EVB shells out to your
`ffmpeg` and `ffprobe` binaries for probing and encoding; the Diagnostics page
reports exactly what it found and what your build supports.

> **FFmpeg builds without libfreetype/libass are fine.** EVB renders *all* text
> — titles, subtitles, captions, watermarks — with Pillow into transparent PNGs
> that FFmpeg composites. It therefore does not need the `drawtext` filter or
> the `subtitles` filter, which some builds omit.

## First-time setup

```bash
git clone <this repo> documentary
cd documentary
./dev.sh --setup      # creates backend/.venv, installs Python + npm deps
```

`./dev.sh --setup` only installs dependencies; it does not install FFmpeg.

## Running

### Option A — double-click (macOS)

Double-click **`Launch EVB.command`** in Finder. The first run installs
everything (a few minutes, one-time); later runs start in seconds, open a
Terminal window with the logs, and open your browser to the app. Keep that
Terminal window open while you work — closing it stops both servers.

If macOS blocks it the first time ("unidentified developer"), right-click the
file → **Open** → **Open**. You only need to do this once.

### Option B — terminal

```bash
./dev.sh
```

Both options start the same two servers:

- Frontend: <http://localhost:5173>
- Backend API: <http://127.0.0.1:8756> (interactive docs at `/docs`)

Open the app and visit **Diagnostics** first. If it says *Ready to render*,
you're set. See [troubleshooting.md](troubleshooting.md) if a check fails.

## Where your data lives

Everything is stored under `~/ExtinctVideoBuilder`:

```
~/ExtinctVideoBuilder/
├── projects/     one folder per project (project.json, images, audio, music)
├── exports/      finished MP4s and side-car files
├── temp/         render scratch (safe to delete)
├── cache/        derived assets
├── music/        shared background-music library
├── logs/         backend.log and per-render logs
├── settings.json app settings (also editable on the Settings page)
└── secrets.json  API keys, 0600 perms — never logged or bundled
```

Override the root with `EVB_DATA_DIR`, or change individual directories on the
Settings page. `EVB_PROJECTS_DIR`, `EVB_EXPORTS_DIR` and `EVB_TEMP_DIR`
override those specific folders.

## Configuration precedence

Settings resolve in this order (first wins): environment variables prefixed
`EVB_`, then a `.env` file in `backend/`, then `settings.json` in the data
directory, then built-in defaults. API keys additionally accept
`EVB_SECRET_<NAME>` so CI and one-off runs never have to write `secrets.json`.

## Updating

Pull the latest code and re-run setup to pick up any new dependencies:

```bash
git pull
./dev.sh --setup
```

Your projects, images, audio and exports live outside the repo (under
`~/ExtinctVideoBuilder`), so updating the code never touches them.
