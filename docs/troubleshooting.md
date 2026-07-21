# Troubleshooting

Every failure in EVB shows a structured error: **what happened**, a
**suggested fix**, and the **log path**, with the raw technical detail one click
away. This page covers the common cases and the error codes behind them.

Start with the **Diagnostics** tab — it measures FFmpeg, disk, permissions and
narration sources and tells you exactly what's wrong.

## FFmpeg

### "ffmpeg could not be found" / "ffprobe could not be found"
Codes: `ffmpeg_not_found`, `ffprobe_not_found`.

EVB shells out to both binaries. Install FFmpeg (`brew install ffmpeg`) — it
ships `ffprobe` too — or, if it's installed somewhere non-standard, set an
explicit **FFmpeg path** / **ffprobe path** on the Settings page. Diagnostics
shows the `PATH` it searched.

### "This FFmpeg build has no 'drawtext' filter" (a note, not an error)
This is expected and harmless. EVB never uses `drawtext` — it draws all text
with Pillow into PNGs and composites them with `overlay`. A build without
libfreetype/libass renders correctly. `ffmpeg_capability_missing` only appears
if a capability EVB *does* rely on is absent.

### A render fails partway through
Code: `ffmpeg_failed` / `render_failed`.

Open the per-render log (its path is in the error, under
`~/ExtinctVideoBuilder/logs/`) and click **Show technical details** for the
FFmpeg stderr. Most causes are a corrupt source image or audio file, or running
out of disk mid-render. Try the **Preview** quality preset to isolate whether
it's encoder-specific.

## Narration / TTS

### Edge TTS won't generate
Codes: `tts_provider_unavailable`, `tts_timeout`, `tts_failed`.

Edge TTS needs an internet connection. Check connectivity, then retry. If you're
offline, switch the provider to **Imported** and upload your own audio per scene
— the app works fully offline that way.

### ElevenLabs errors
Codes: `tts_invalid_api_key`, `tts_quota_exceeded`.

Set or fix your API key on the Settings page (stored in `secrets.json` with
`0600` permissions, never logged or bundled). `tts_quota_exceeded` means your
ElevenLabs plan is out of characters.

### "no voices available"
The provider's voice list needs a network call. For Edge TTS this means no
internet; the field still works if you type a known voice id. Imported audio
needs no voice.

## Images

Codes: `unsupported_image`, `corrupt_image`, `image_too_small`, `missing_image`.

Use PNG, JPEG or WebP. If a card shows a warning, the image is usable but not
ideal (e.g. an unusual aspect ratio for 1920×1080). `missing_image` after an
import usually means an image was renamed or deleted on disk — reload the
project.

## Content import

Codes: `invalid_json`, `schema_validation`, `unsupported_schema_version`.

The importer reports the exact field and line. Start from **Download example
template** on the Content tab and compare against
[content-schema.md](content-schema.md).

## Disk & permissions

### "insufficient disk space"
Code: `insufficient_disk_space`.

The Export preflight estimates the space a render needs and blocks if it's
short. Free some space (the `temp/` folder under the data directory is safe to
delete) or lower the quality preset. On a remote/managed disk, note that a fixed
allowance can read as full even when little is "used".

### "permission denied"
Code: `permission_denied`.

EVB couldn't write under the data directory. Check the folder's permissions, or
point `EVB_DATA_DIR` at a writable location.

## Timing looks off

If the runtime seems wrong, remember scene durations come from the *measured*
narration length, recomputed only when you regenerate audio. Regenerate
narration after big text edits, and check **Duration mode** on the Audio tab
(audio-driven vs target vs manual). The Audio tab's "Expected runtime" panel and
the Export preflight always reflect the same single Timeline the render uses.

## Still stuck?

The backend log is at `~/ExtinctVideoBuilder/logs/backend.log`; each render also
writes its own log next to it. The interactive API docs at
<http://127.0.0.1:8756/docs> let you exercise any endpoint directly.
