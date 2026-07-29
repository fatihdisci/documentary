"""Wire and storage models for publishing.

Three separate concerns live here, and they are deliberately not merged:

* **Media** — what can be published: a finished long render or a finished Short.
  Described from files that exist on disk *now*, never from a stale list.
* **Drafts** — what the user typed. One draft per media file, holding shared
  fields plus a per-platform block. Editing a draft never touches
  ``project.metadata``; the project is the seed, not the store.
* **Jobs** — one upload attempt, persisted on every state change exactly like a
  render or a Short, so progress survives a reload and a killed job is reported
  as interrupted rather than sitting in "running" forever.

Nothing in this module can carry a credential. Tokens live in the app's secrets
directory and are read only by ``publishing/youtube.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import Field

from app.models.base import CamelModel
from app.models.enums import JobStatus

#: YouTube's own limits, validated on both sides of the wire.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_BYTES = 5_000
MAX_TAGS_LENGTH = 500

#: Category 15 is "Pets & Animals", the closest YouTube category for the
#: wildlife and extinct-animal documentaries produced by this application.
DEFAULT_CATEGORY_ID = "15"
DEFAULT_LANGUAGE = "en"

#: The clock the user thinks in. Every scheduled time is entered and displayed in
#: this zone and converted to an offset-aware RFC 3339 value for the API.
LOCAL_TIMEZONE = "Europe/Istanbul"

#: Meta's and TikTok's own limits, validated before anything is sent.
MAX_INSTAGRAM_CAPTION_CHARS = 2_200
MAX_INSTAGRAM_HASHTAGS = 30
MAX_FACEBOOK_DESCRIPTION_CHARS = 5_000
MAX_TIKTOK_TITLE_CHARS = 2_200

#: Thumbnails: YouTube's own limit is 2 MB.
MAX_THUMBNAIL_BYTES = 2 * 1_048_576
#: Captions: generous for an SRT, small enough that nothing silly gets stored.
MAX_CAPTION_BYTES = 2 * 1_048_576


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PublishingPlatform(str, Enum):
    """Platforms the panel can publish to.

    All four have an implementation. What differs is the *connection* each one
    needs, and that is the only thing that makes a platform unavailable at a
    given moment — never the code being missing.
    """

    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"

    @property
    def is_implemented(self) -> bool:
        return True

    @property
    def label(self) -> str:
        return {
            PublishingPlatform.YOUTUBE: "YouTube",
            PublishingPlatform.INSTAGRAM: "Instagram",
            PublishingPlatform.FACEBOOK: "Facebook",
            PublishingPlatform.TIKTOK: "TikTok",
        }[self]

    @property
    def needs_hosted_media(self) -> bool:
        """True when the platform fetches the video from a URL we must provide.

        Meta's publishing APIs never take an uploaded file for Reels; they take
        a link and download it themselves. YouTube and TikTok both accept the
        bytes directly, so they never need the hosting layer.
        """
        return self in {PublishingPlatform.INSTAGRAM, PublishingPlatform.FACEBOOK}


class MediaKind(str, Enum):
    LONG = "long"
    SHORT = "short"


class PrivacyStatus(str, Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class PublishMode(str, Enum):
    NOW = "now"
    SCHEDULE = "schedule"


class PublishPhase(str, Enum):
    """Ordered phases. Progress weighting and the UI's step list both use these.

    The first three and the last two are shared by every platform. The middle is
    where they differ: YouTube streams the file and then decorates the video,
    while Meta is handed a URL and then waits for a container to finish
    processing before anything is visible.
    """

    VALIDATE = "validate"
    AUTHENTICATE = "authenticate"
    HASH_SOURCE = "hash-source"

    # YouTube
    UPLOAD_VIDEO = "upload-video"
    SET_THUMBNAIL = "set-thumbnail"
    UPLOAD_CAPTIONS = "upload-captions"

    # Instagram / Facebook / TikTok
    #: Put the bytes somewhere the platform can fetch them from.
    HOST_MEDIA = "host-media"
    #: Ask the platform to start ingesting: an IG container, an FB Reel session,
    #: or a TikTok publish init.
    CREATE_CONTAINER = "create-container"
    #: The platform is transcoding. Nothing exists publicly yet.
    AWAIT_PROCESSING = "await-processing"
    #: The one irreversible call: the post becomes real.
    PUBLISH_POST = "publish-post"
    #: Remove the temporary copy once the platform no longer needs it.
    CLEANUP = "cleanup"

    FETCH_STATUS = "fetch-status"
    COMPLETE = "complete"


class AssetStatus(str, Enum):
    """Outcome of a step that must not fail the video upload behind it."""

    SKIPPED = "skipped"
    PENDING = "pending"
    UPLOADED = "uploaded"
    FAILED = "failed"


# --- media ------------------------------------------------------------------


class SourceFingerprint(CamelModel):
    """Identity of the exact file a draft or an upload was bound to.

    Size is the cheap check run on every page load; the SHA-256 is computed once
    before an upload starts and is what makes duplicate detection and "the source
    changed" reliable rather than a guess from the filename.
    """

    filename: str
    size_bytes: int = 0
    sha256: str = ""


class MediaItem(CamelModel):
    """One publishable file. Never carries an absolute path."""

    #: ``long:<renderId>`` or ``short:<shortId>``. Stable across restarts.
    media_id: str
    kind: MediaKind
    filename: str
    #: Download/preview URL under the API, so the page can open the file.
    url: str
    project_slug: str
    project_name: str = ""
    created_at: datetime
    duration_seconds: float = 0.0
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    fps: int = 0
    quality: str = ""
    thumbnail_url: str | None = None
    #: A preview render is a check, not something to publish. Hidden by default.
    recommended: bool = True
    #: Present when ``recommended`` is False, or when something is off.
    note: str | None = None
    fingerprint: SourceFingerprint
    #: An English .srt produced beside this render, matched through the render
    #: job's recorded artifacts — never guessed from the filename.
    caption_filename: str | None = None
    caption_url: str | None = None
    #: True when a draft already exists for this media.
    has_draft: bool = False
    #: Set when this exact file already reached YouTube from this computer.
    published_video_id: str | None = None
    #: The authored `shortsPlan` item whose section sequence produced this file.
    content_plan_id: str | None = None


# --- drafts -----------------------------------------------------------------


class YouTubeDraft(CamelModel):
    """The YouTube block of a draft. Mirrors the API body it will produce."""

    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: str = DEFAULT_CATEGORY_ID
    default_language: str = DEFAULT_LANGUAGE
    default_audio_language: str = DEFAULT_LANGUAGE
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    publish_mode: PublishMode = PublishMode.NOW
    #: Local wall-clock time in ``Europe/Istanbul``, e.g. ``2026-08-01T22:00``.
    #: Deliberately not a UTC instant: the user picks a time in their own day and
    #: the backend is what binds it to a zone.
    publish_at_local: str | None = None
    made_for_kids: bool = False
    notify_subscribers: bool = True
    embeddable: bool = True
    #: Filename inside ``publishing/assets/thumbnails``. Never a path.
    thumbnail_file: str | None = None
    #: Filename inside ``publishing/assets/captions``, or an export's own .srt.
    caption_file: str | None = None
    #: Where ``caption_file`` lives: an uploaded asset or the render's export.
    caption_source: str = "none"
    caption_language: str = DEFAULT_LANGUAGE
    caption_name: str = "English"
    caption_is_draft: bool = False
    upload_captions: bool = False


class SocialDraft(CamelModel):
    """Shared shape for the three non-YouTube platforms.

    ``account`` is a note the user keeps for themselves. The account actually
    posted to comes from the stored connection, never from a name typed here —
    a text field cannot authorize anything.
    """

    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    account: str = ""
    publish_mode: PublishMode = PublishMode.NOW
    publish_at_local: str | None = None


class InstagramDraft(SocialDraft):
    """Instagram Reels. Meta's own limits are validated before publishing."""

    #: Reels can also appear on the main profile grid. Meta's default is on.
    share_to_feed: bool = True


class FacebookDraft(SocialDraft):
    """A Reel on the connected Facebook Page."""


class TikTokDraft(SocialDraft):
    #: Mirrors TikTok's ``privacy_level``. ``SELF_ONLY`` is the only value an
    #: unaudited app may use, and the UI says so rather than failing later.
    privacy: str = "SELF_ONLY"
    allow_comments: bool = True
    allow_duet: bool = False
    allow_stitch: bool = False


class CommonDraft(CamelModel):
    """Fields shared by every platform, seeded from the project's metadata."""

    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    #: Reference only. Never sent to any platform — the user reads these while
    #: making the actual thumbnail image.
    thumbnail_text: str = ""
    thumbnail_prompt: str = ""
    #: The Short's opening hook, seeded from the authored Shorts plan and shown
    #: next to the thumbnail text so the two can be read as one promise. Also
    #: reference only: the hook that is *drawn* was burned in when the Short was
    #: rendered, so editing it here is a note for the next cut, not a change to
    #: this file. The panel says so.
    hook_text: str = ""
    #: What the long video opens with, for the record. Read-only in the panel.
    long_intro_summary: str = ""


class PublishDraft(CamelModel):
    """Everything the user typed for one media file."""

    media_id: str
    project_slug: str = ""
    source_fingerprint: SourceFingerprint
    common: CommonDraft = Field(default_factory=CommonDraft)
    youtube: YouTubeDraft = Field(default_factory=YouTubeDraft)
    instagram: InstagramDraft = Field(default_factory=InstagramDraft)
    facebook: FacebookDraft = Field(default_factory=FacebookDraft)
    tiktok: TikTokDraft = Field(default_factory=TikTokDraft)
    updated_at: datetime = Field(default_factory=_now)

    def social(self, platform: PublishingPlatform) -> SocialDraft:
        """The block belonging to one platform. Raises for YouTube by design."""
        block = {
            PublishingPlatform.INSTAGRAM: self.instagram,
            PublishingPlatform.FACEBOOK: self.facebook,
            PublishingPlatform.TIKTOK: self.tiktok,
        }.get(platform)
        if block is None:
            raise KeyError(f"{platform.value} has no social draft block")
        return block


# --- jobs and history -------------------------------------------------------


class PublishJob(CamelModel):
    """One upload attempt. Persisted on every state change."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    project_slug: str
    media_id: str
    platform: PublishingPlatform = PublishingPlatform.YOUTUBE
    source: SourceFingerprint
    #: The user's explicit "yine de yükle". Carried on the job because the
    #: duplicate check runs twice: when the job is queued, and again in the
    #: worker just before the bytes are sent.
    allow_duplicate: bool = False

    status: JobStatus = JobStatus.QUEUED
    phase: PublishPhase = PublishPhase.VALIDATE
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = "Sırada"

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    #: The process that owns this job, so one killed mid-upload is reported as
    #: interrupted after a restart rather than staying "running".
    pid: int | None = None

    #: Written the instant the platform accepts the video, before anything else
    #: runs. This is what makes a retry safe: a job with a video id never
    #: re-uploads. For Instagram it is the published media id, for Facebook the
    #: Reel's video id, for TikTok the publish id's resulting post.
    video_id: str | None = None
    video_url: str | None = None

    #: An ingestion handle that exists *before* anything is public: an Instagram
    #: container id, a Facebook Reel video id awaiting ``finish``, or a TikTok
    #: publish id. Persisted so a retry can resume rather than re-upload, and
    #: deliberately separate from ``video_id`` — a container is not a post.
    container_id: str | None = None
    #: The temporary URL the platform was given, kept only so the object can be
    #: deleted afterwards. Never shown to the user and never logged.
    hosted_object_key: str | None = None

    title: str = ""
    requested_privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    requested_publish_at: datetime | None = None
    #: What the API reported after the upload, which can differ from what was
    #: asked for — an unverified project forces videos to private, for one.
    actual_privacy_status: str | None = None
    actual_publish_at: datetime | None = None
    upload_status: str | None = None
    processing_status: str | None = None

    thumbnail_status: AssetStatus = AssetStatus.SKIPPED
    thumbnail_error: str | None = None
    caption_status: AssetStatus = AssetStatus.SKIPPED
    caption_error: str | None = None
    caption_track_id: str | None = None

    uploaded_bytes: int = 0
    total_bytes: int = 0

    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    error_details: str | None = None
    error_suggestion: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }

    @property
    def is_active(self) -> bool:
        return self.status in {JobStatus.QUEUED, JobStatus.RUNNING}

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or _now()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def estimated_remaining_seconds(self) -> float | None:
        if self.status is not JobStatus.RUNNING or self.progress < 0.05:
            return None
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return None
        return max(0.0, elapsed / self.progress - elapsed)


class PublishJobEvent(CamelModel):
    """One server-sent progress update."""

    job_id: str
    status: JobStatus
    phase: PublishPhase
    progress: float
    message: str
    elapsed_seconds: float
    estimated_remaining_seconds: float | None = None
    uploaded_bytes: int = 0
    total_bytes: int = 0
    video_id: str | None = None
    video_url: str | None = None
    thumbnail_status: AssetStatus = AssetStatus.SKIPPED
    caption_status: AssetStatus = AssetStatus.SKIPPED
    error_code: str | None = None
    error_message: str | None = None
    error_suggestion: str | None = None


class PublishHistoryEntry(CamelModel):
    """A video that reached the platform. Written the moment it did."""

    entry_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    job_id: str = ""
    project_slug: str
    media_id: str
    platform: PublishingPlatform = PublishingPlatform.YOUTUBE
    filename: str = ""
    title: str = ""
    video_id: str = ""
    video_url: str = ""
    uploaded_at: datetime = Field(default_factory=_now)
    requested_publish_at: datetime | None = None
    actual_publish_at: datetime | None = None
    privacy_status: str = ""
    upload_status: str | None = None
    processing_status: str | None = None
    thumbnail_status: AssetStatus = AssetStatus.SKIPPED
    caption_status: AssetStatus = AssetStatus.SKIPPED
    source: SourceFingerprint = Field(default_factory=lambda: SourceFingerprint(filename=""))
    warnings: list[str] = Field(default_factory=list)


class DraftResponse(CamelModel):
    """A draft plus the live state the page needs to render around it."""

    draft: PublishDraft
    media: MediaItem
    #: True when the file on disk no longer matches what the draft recorded.
    source_changed: bool = False
    source_changed_reason: str | None = None
    #: Set when this fingerprint already reached YouTube. Kept as its own field
    #: because YouTube is the panel's primary path and the warning above the
    #: editor reads from it directly.
    duplicate_of: PublishHistoryEntry | None = None
    #: The same check per platform, keyed by ``PublishingPlatform`` value. Each
    #: platform is independent: a Reel already on Instagram says nothing about
    #: whether the file has been to Facebook.
    duplicates: dict[str, PublishHistoryEntry] = Field(default_factory=dict)


# --- requests ---------------------------------------------------------------


class PublishRequest(CamelModel):
    """Ask for one upload. The draft on disk supplies everything else."""

    media_id: str
    #: Off by default and confirmed in the UI: uploading the same file twice
    #: makes a second, unrelated video on the channel.
    allow_duplicate: bool = False


class YouTubeConnection(CamelModel):
    """Everything the Settings page shows about the YouTube connection.

    Contains no client id, client secret, access token or refresh token — only
    whether the files exist, whether the grant is usable, and which channel it
    belongs to.
    """

    client_file_present: bool = False
    #: Basename only. The full path is never sent to the frontend.
    client_file_name: str | None = None
    available_client_files: list[str] = Field(default_factory=list)
    token_present: bool = False
    connected: bool = False
    #: True when the stored grant exists but can no longer be used as-is.
    needs_reconnect: bool = False
    expired: bool = False
    scopes_sufficient: bool = False
    missing_scopes: list[str] = Field(default_factory=list)
    channel_id: str | None = None
    channel_title: str | None = None
    channel_thumbnail_url: str | None = None
    checked_at: datetime | None = None
    #: A complete sentence describing the current state, in Turkish.
    status_message: str = ""
    #: Present when something needs fixing.
    problem: str | None = None
    suggestion: str | None = None


class ClientSecretUploadResponse(CamelModel):
    connection: YouTubeConnection
    stored_file_name: str


class RefreshStatusResponse(CamelModel):
    entry: PublishHistoryEntry


# --- Meta -------------------------------------------------------------------


class MetaPageSummary(CamelModel):
    """One Facebook Page the connected user administers.

    Ids and names only. The Page access token that comes back with each of these
    from ``/me/accounts`` stays in the token file and never enters this model.
    """

    page_id: str
    name: str
    #: The linked Instagram professional account, when there is one.
    instagram_id: str | None = None
    instagram_username: str | None = None


class MetaConnection(CamelModel):
    """What the Settings page shows about the Meta connection.

    Contains no App ID, no App Secret and no access token — only whether they
    exist, whether the grant still works, and which Page and Instagram account
    it resolved to. The App ID is deliberately absent too: the panel never needs
    it, and an id plus a leaked secret is a usable credential pair.
    """

    app_configured: bool = False
    token_present: bool = False
    connected: bool = False
    needs_reconnect: bool = False
    expired: bool = False
    #: When the long-lived user token stops working. Meta's is ~60 days.
    expires_at: datetime | None = None
    scopes_sufficient: bool = False
    missing_scopes: list[str] = Field(default_factory=list)

    pages: list[MetaPageSummary] = Field(default_factory=list)
    selected_page_id: str | None = None
    page_name: str | None = None
    instagram_id: str | None = None
    instagram_username: str | None = None

    #: The exact address to paste into "Valid OAuth Redirect URIs".
    redirect_uri: str = ""
    checked_at: datetime | None = None
    status_message: str = ""
    problem: str | None = None
    suggestion: str | None = None


class MetaAppCredentials(CamelModel):
    """Write-only. The values are stored and never read back by any endpoint."""

    app_id: str
    app_secret: str
    #: Refuse to overwrite an existing pair unless the user meant to.
    replace: bool = False


class MetaPageSelection(CamelModel):
    page_id: str


class OAuthStart(CamelModel):
    """Where to send the user's browser. Carries no secret beyond the app id.

    The app id is unavoidably part of an OAuth URL — that is what identifies the
    application to the provider — and this response is the only place it appears.
    """

    authorization_url: str
    redirect_uri: str


# --- TikTok -----------------------------------------------------------------


class TikTokCreatorInfo(CamelModel):
    """What TikTok says this creator may currently do.

    Queried before every post because the answer changes: an unaudited app is
    restricted to private posts, and the account's own settings can narrow it
    further.
    """

    nickname: str = ""
    username: str = ""
    avatar_url: str | None = None
    privacy_level_options: list[str] = Field(default_factory=list)
    comment_disabled: bool = False
    duet_disabled: bool = False
    stitch_disabled: bool = False
    max_video_post_duration_seconds: int = 0
    fetched_at: datetime | None = None


class TikTokConnection(CamelModel):
    """Connection state for TikTok. No client key, no secret, no token."""

    app_configured: bool = False
    token_present: bool = False
    connected: bool = False
    needs_reconnect: bool = False
    expired: bool = False
    expires_at: datetime | None = None
    scopes_sufficient: bool = False
    missing_scopes: list[str] = Field(default_factory=list)

    display_name: str | None = None
    avatar_url: str | None = None
    creator_info: TikTokCreatorInfo | None = None
    #: True until the app passes TikTok's audit. Public posting is unavailable
    #: while it is set, and the UI says so instead of offering it.
    audit_required: bool = True

    redirect_uri: str = ""
    checked_at: datetime | None = None
    status_message: str = ""
    problem: str | None = None
    suggestion: str | None = None


class TikTokAppCredentials(CamelModel):
    client_key: str
    client_secret: str
    replace: bool = False


# --- temporary media hosting ------------------------------------------------


class MediaHostStatus(CamelModel):
    """Whether the app can hand Meta a URL it can actually fetch.

    Bucket and endpoint are configuration, not credentials, so they are shown.
    The two keys are never included — only whether they are present.
    """

    provider: str = "none"
    configured: bool = False
    endpoint: str = ""
    bucket: str = ""
    region: str = ""
    prefix: str = ""
    keys_present: bool = False
    ttl_seconds: int = 0
    delete_after_publish: bool = True
    status_message: str = ""
    problem: str | None = None
    suggestion: str | None = None


class ObjectStorageSettings(CamelModel):
    """Bucket coordinates plus, optionally, a new key pair to store.

    The keys are write-only: they go straight into the secrets file and are
    never returned by any endpoint. Sending them empty leaves the stored pair
    alone, so saving the bucket name does not wipe the credentials.
    """

    provider: str = "none"
    endpoint: str = ""
    bucket: str = ""
    region: str = "auto"
    prefix: str = "evb-temp"
    ttl_seconds: int = 3600
    delete_after_publish: bool = True
    access_key_id: str | None = None
    secret_access_key: str | None = None
