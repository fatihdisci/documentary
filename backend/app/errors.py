"""Error taxonomy for Extinct Video Builder.

Every failure surfaced to the user carries four things: a human-readable message,
a technical detail block, a suggested fix, and where to find the relevant log.
"Something went wrong" is never an acceptable payload.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from app.models.base import CamelModel


class ErrorCode(str, Enum):
    """Stable machine-readable codes. The frontend switches on these."""

    # Environment / tooling
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    FFMPEG_FAILED = "ffmpeg_failed"
    FFMPEG_CAPABILITY_MISSING = "ffmpeg_capability_missing"
    FONT_UNAVAILABLE = "font_unavailable"

    # Storage
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_EXISTS = "project_exists"
    PATH_TRAVERSAL = "path_traversal"
    PERMISSION_DENIED = "permission_denied"
    INSUFFICIENT_DISK_SPACE = "insufficient_disk_space"
    EXPORT_EXISTS = "export_exists"
    FILE_TOO_LARGE = "file_too_large"

    # Media
    UNSUPPORTED_IMAGE = "unsupported_image"
    CORRUPT_IMAGE = "corrupt_image"
    IMAGE_TOO_SMALL = "image_too_small"
    MISSING_IMAGE = "missing_image"
    UNSUPPORTED_AUDIO = "unsupported_audio"
    CORRUPT_AUDIO = "corrupt_audio"

    # Content / schema
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION = "schema_validation"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"

    # TTS
    TTS_PROVIDER_UNAVAILABLE = "tts_provider_unavailable"
    TTS_TIMEOUT = "tts_timeout"
    TTS_FAILED = "tts_failed"
    TTS_INVALID_API_KEY = "tts_invalid_api_key"
    TTS_QUOTA_EXCEEDED = "tts_quota_exceeded"
    MISSING_NARRATION = "missing_narration"
    MISSING_AUDIO = "missing_audio"

    # Timing / render
    INVALID_DURATION = "invalid_duration"
    INVALID_TRANSITION = "invalid_transition"
    RENDER_CANCELLED = "render_cancelled"
    RENDER_FAILED = "render_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    JOB_NOT_FOUND = "job_not_found"

    # Shorts. A Short is only ever cut from a finished long render, so most of
    # these describe the source no longer being what the manifest recorded.
    SHORT_SOURCE_NOT_READY = "short_source_not_ready"
    SHORT_MANIFEST_MISSING = "short_manifest_missing"
    STALE_RENDER = "stale_render"
    SHORT_INVALID_SELECTION = "short_invalid_selection"
    SHORT_INVALID_TRIM = "short_invalid_trim"
    SHORT_TOO_LONG = "short_too_long"
    SHORT_JOB_NOT_FOUND = "short_job_not_found"
    SHORT_NOT_FOUND = "short_not_found"

    # Generic fallback (still requires a real message + fix)
    INTERNAL = "internal"


#: Default remediation advice per code. Callers may override with something
#: more specific, but there is always a non-empty suggestion.
_DEFAULT_FIXES: dict[ErrorCode, str] = {
    ErrorCode.FFMPEG_NOT_FOUND: (
        "Install FFmpeg (`brew install ffmpeg`) or set an explicit path in "
        "Settings → FFmpeg path."
    ),
    ErrorCode.FFPROBE_NOT_FOUND: (
        "ffprobe ships with FFmpeg. Install FFmpeg or set the ffprobe path in Settings."
    ),
    ErrorCode.FFMPEG_FAILED: (
        "Open the render log for the exact FFmpeg stderr. If it mentions a missing "
        "filter, check Diagnostics for your build's capabilities."
    ),
    ErrorCode.FFMPEG_CAPABILITY_MISSING: (
        "Your FFmpeg build lacks a required filter. Check Diagnostics to see which "
        "capabilities were detected."
    ),
    ErrorCode.FONT_UNAVAILABLE: (
        "Pick a different font in Style settings, or restore the bundled fonts in "
        "backend/assets/fonts."
    ),
    ErrorCode.PROJECT_NOT_FOUND: "Reload the project list; the project may have been moved or deleted.",
    ErrorCode.PROJECT_EXISTS: "Choose a different project name.",
    ErrorCode.PATH_TRAVERSAL: (
        "This file path points outside the project folder and was rejected. Re-upload "
        "the file through the app instead of referencing it by path."
    ),
    ErrorCode.PERMISSION_DENIED: (
        "Check folder permissions for the storage directory configured in Settings."
    ),
    ErrorCode.INSUFFICIENT_DISK_SPACE: (
        "Free up disk space, lower the export quality, or point the temporary "
        "directory at a larger volume in Settings."
    ),
    ErrorCode.EXPORT_EXISTS: (
        "Exports are auto-versioned. If you see this, the versioning counter could not "
        "find a free filename — remove old exports or rename the project."
    ),
    ErrorCode.FILE_TOO_LARGE: "Reduce the file size or raise the upload limit in Settings.",
    ErrorCode.UNSUPPORTED_IMAGE: "Use PNG, JPEG or WebP.",
    ErrorCode.CORRUPT_IMAGE: "The file could not be decoded. Re-export it and upload again.",
    ErrorCode.IMAGE_TOO_SMALL: (
        "Use an image at least 1280x720. Smaller images visibly soften once pan and "
        "zoom is applied."
    ),
    ErrorCode.MISSING_IMAGE: "Upload or re-link an image for this scene.",
    ErrorCode.UNSUPPORTED_AUDIO: "Use WAV, MP3 or M4A.",
    ErrorCode.CORRUPT_AUDIO: "The audio could not be decoded by FFmpeg. Re-export and upload again.",
    ErrorCode.INVALID_JSON: "Fix the JSON syntax. The position of the parse error is in the details.",
    ErrorCode.SCHEMA_VALIDATION: "Correct the fields listed in the details and import again.",
    ErrorCode.UNSUPPORTED_SCHEMA_VERSION: (
        "This file was written by a newer version of the app. Upgrade, or export the "
        "project again from the newer version using an older schema."
    ),
    ErrorCode.TTS_PROVIDER_UNAVAILABLE: (
        "Check your internet connection, or switch to the 'imported' provider and "
        "upload narration audio per scene — the app renders fully offline that way."
    ),
    ErrorCode.TTS_TIMEOUT: "Retry. If it keeps timing out, switch provider or import audio manually.",
    ErrorCode.TTS_FAILED: "See the details for the provider's response, then retry the affected scenes.",
    ErrorCode.TTS_INVALID_API_KEY: "Re-enter the API key in Settings → TTS.",
    ErrorCode.TTS_QUOTA_EXCEEDED: (
        "Wait for your quota to reset, switch to Edge TTS (free), or import audio."
    ),
    ErrorCode.MISSING_NARRATION: "Add narration text to this scene, or disable the scene.",
    ErrorCode.MISSING_AUDIO: (
        "Generate TTS for this scene or upload an audio file before rendering."
    ),
    ErrorCode.INVALID_DURATION: "Adjust the scene duration or switch the duration mode.",
    ErrorCode.INVALID_TRANSITION: (
        "Shorten the transition, or lengthen the scenes it sits between. A transition "
        "cannot exceed 40% of its shorter neighbour."
    ),
    ErrorCode.RENDER_CANCELLED: "Start a new render when ready.",
    ErrorCode.RENDER_FAILED: "Open the render log for the failing stage, fix the cause, then retry.",
    ErrorCode.OUTPUT_VALIDATION_FAILED: (
        "FFmpeg finished but the output failed verification. The details list every "
        "assertion and its actual value. Retry, and report this if it repeats."
    ),
    ErrorCode.JOB_NOT_FOUND: "The job list may be stale. Refresh the render history.",
    ErrorCode.SHORT_SOURCE_NOT_READY: (
        "Shorts are cut from a finished video. Pick a render whose status is 'completed', "
        "or render the long video first."
    ),
    ErrorCode.SHORT_MANIFEST_MISSING: (
        "This export predates the Shorts feature, so it has no section timeline. Re-render "
        "the long video once; the new export carries a manifest and can be used for Shorts."
    ),
    ErrorCode.STALE_RENDER: (
        "The exported video no longer matches the render it was recorded from. Re-render the "
        "long video, then build the Short from the new export."
    ),
    ErrorCode.SHORT_INVALID_SELECTION: (
        "Select at least one section from the source render's timeline, and only sections that "
        "the selected render actually contains."
    ),
    ErrorCode.SHORT_INVALID_TRIM: (
        "Keep each trim inside the section's safe range, with the start before the end."
    ),
    ErrorCode.SHORT_TOO_LONG: (
        "YouTube only treats videos up to three minutes as Shorts. Deselect a section or "
        "trim the selection down."
    ),
    ErrorCode.SHORT_JOB_NOT_FOUND: "The Shorts history may be stale. Reload the Shorts tab.",
    ErrorCode.SHORT_NOT_FOUND: (
        "That Short is no longer on disk. Reload the Shorts tab to refresh the list."
    ),
    ErrorCode.INTERNAL: "Check the backend log for the traceback.",
}


class ErrorPayload(CamelModel):
    """The wire format for every error the API returns."""

    code: ErrorCode
    message: str = Field(description="Human-readable, specific, no jargon required to act on it.")
    details: str | None = Field(default=None, description="Technical detail: stderr, traceback, field errors.")
    suggestion: str = Field(description="What the user should do next.")
    log_path: str | None = Field(default=None, description="Absolute path to the most relevant log file.")
    context: dict[str, Any] = Field(default_factory=dict)


class AppError(Exception):
    """Base class for every deliberate failure in the application.

    Raising this (rather than a bare Exception) guarantees the user gets an
    actionable message instead of a 500.
    """

    http_status: int = 400

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: str | None = None,
        suggestion: str | None = None,
        log_path: str | None = None,
        http_status: int | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.suggestion = suggestion or _DEFAULT_FIXES.get(code, _DEFAULT_FIXES[ErrorCode.INTERNAL])
        self.log_path = log_path
        self.context = context
        if http_status is not None:
            self.http_status = http_status

    def to_payload(self) -> ErrorPayload:
        return ErrorPayload(
            code=self.code,
            message=self.message,
            details=self.details,
            suggestion=self.suggestion,
            log_path=self.log_path,
            context=self.context,
        )

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"[{self.code.value}] {self.message}"


class NotFoundError(AppError):
    http_status = 404


class ConflictError(AppError):
    http_status = 409


class ValidationError(AppError):
    http_status = 422


class EnvironmentError_(AppError):
    """Tooling/environment problem (FFmpeg missing, etc.). Not the user's data."""

    http_status = 503


class RenderError(AppError):
    http_status = 500
