#!/usr/bin/env python3
"""Upload and schedule the Chinese paddlefish release set on YouTube.

The script is safe to rerun: it finds already scheduled videos by their exact
titles, then reuses them rather than uploading a duplicate. It also adds the
English SRT track to the long video if it is not already present.
"""

from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


ISTANBUL = ZoneInfo("Europe/Istanbul")
DOWNLOADS = Path.home() / "Downloads"
APP_EXPORTS = Path.home() / "ExtinctVideoBuilder" / "projects" / "chinese-paddlefish" / "exports"
SECRETS = Path.home() / "ExtinctVideoBuilder" / "secrets"
CLIENT_FILE = SECRETS / "client_secret_190473268387-1uq00dn3lo4e0290k8s8842b8pl99ipi.apps.googleusercontent.com.json"
TOKEN_FILE = SECRETS / "youtube-upload-token.json"
MANIFEST_FILE = SECRETS / "youtube-chinese-paddlefish-schedule.json"
THUMBNAIL_FILE = DOWNLOADS / "CHINESE PADDLEFISH.png"
LONG_VIDEO_FILE = DOWNLOADS / "chinese-paddlefish_v01.mp4"
CAPTION_FILE = DOWNLOADS / "chinese-paddlefish_v01.srt"
SCOPE = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


@dataclass(frozen=True)
class ScheduledVideo:
    path: Path
    title: str
    description: str
    tags: list[str]
    publish_at: datetime
    notify_subscribers: bool


def oauth_credentials() -> Credentials:
    credentials: Credentials | None = None
    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid or not credentials.has_scopes(SCOPE):
        if not CLIENT_FILE.exists():
            raise FileNotFoundError(f"OAuth client file is missing: {CLIENT_FILE}")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPE)
        credentials = flow.run_local_server(host="localhost", port=0, open_browser=True)
        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(TOKEN_FILE, 0o600)
    return credentials


def publish_time(start_date: str, day_offset: int, hour: int) -> datetime:
    start = datetime.fromisoformat(start_date).replace(tzinfo=ISTANBUL)
    return (start + timedelta(days=day_offset)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def release_plan(start_date: str, long_url: str) -> list[ScheduledVideo]:
    common_tags = [
        "Chinese paddlefish", "extinct animals", "extinct fish", "Yangtze River",
        "wildlife documentary", "natural history", "species extinction", "conservation",
        "Vanished Earth",
    ]
    long_description = (
        "A giant fish once moved through China's longest river. The Chinese paddlefish had a long, flat snout, "
        "a powerful body, and a life built around one great river journey.\n\n"
        "This documentary follows the fish through deep water, feeding grounds, long trips upstream, heavy fishing, "
        "a dam that cut off its route, the last confirmed sighting in twenty-oh-three, and the searches that found nothing.\n\n"
        "Sources and further reading:\n"
        "IUCN Red List: https://www.iucnredlist.org/species/18421/17618187\n"
        "Zhang et al. (2020): https://doi.org/10.1016/j.scitotenv.2020.138000\n"
        "FishBase: https://www.fishbase.se/summary/SpeciesSummary.php?id=2473\n\n"
        "Chapters:\n"
        "00:00 The Chinese Paddlefish\n00:22 One River, One Home\n00:46 Built Like No Other Fish\n"
        "01:10 A Hunter in Deep Water\n01:31 The Long River Journey\n01:52 Part of a Living River\n"
        "02:14 Nets Closed In\n02:41 A Wall Across the River\n03:04 The Last Signal\n"
        "03:26 The Search Found Nothing\n03:51 A Warning That Came Too Late\n04:19 Keep the Whole Journey Alive\n\n"
        "#ChinesePaddlefish #ExtinctFish #YangtzeRiver #ExtinctAnimals #Conservation"
    )
    return [
        ScheduledVideo(
            LONG_VIDEO_FILE,
            "The Chinese Paddlefish: The River Giant We Lost",
            long_description,
            common_tags,
            publish_time(start_date, 0, 22),
            True,
        ),
        ScheduledVideo(
            DOWNLOADS / "chinese-paddlefish-short-8eb8ef684fe19cc4.mp4",
            "The Last Signal Vanished in Hours",
            "In early twenty-oh-three, a Chinese paddlefish was tagged and released. Its signal vanished within hours. Later searches found no confirmed survivor.\n\nWatch the full documentary:\n"
            + long_url + "\n\n#ExtinctFish #YangtzeRiver #Shorts",
            common_tags + ["documentary shorts"],
            publish_time(start_date, 0, 23),
            True,
        ),
        ScheduledVideo(
            DOWNLOADS / "chinese-paddlefish-short-b5d0a9248135cb4a.mp4",
            "Why This Giant Fish Had to Keep Moving",
            "The Chinese paddlefish needed one long river route. Adults moved upstream to lay eggs, then young fish travelled back toward wider water. A free path was part of its life.\n\nWatch the full documentary:\n"
            + long_url + "\n\n#ChinesePaddlefish #ExtinctAnimals #Shorts",
            common_tags + ["documentary shorts"],
            publish_time(start_date, 1, 18),
            True,
        ),
        ScheduledVideo(
            DOWNLOADS / "chinese-paddlefish-short-73d206467d6ae03f.mp4",
            "A Fish Built Like No Other",
            "The Chinese paddlefish lived only in one connected river system. Its long paddle-shaped snout and powerful body made it one of the river's most striking animals.\n\nWatch the full documentary:\n"
            + long_url + "\n\n#ChinesePaddlefish #ExtinctFish #Shorts",
            common_tags + ["documentary shorts"],
            publish_time(start_date, 1, 23),
            False,
        ),
    ]


def upload(youtube, item: ScheduledVideo) -> dict[str, str]:
    if not item.path.is_file():
        raise FileNotFoundError(f"Video is missing: {item.path}")
    if item.publish_at <= datetime.now(ISTANBUL):
        raise ValueError(f"Publish time is not in the future: {item.publish_at.isoformat()}")
    body = {
        "snippet": {"title": item.title, "description": item.description, "tags": item.tags, "categoryId": "15", "defaultLanguage": "en"},
        "status": {"privacyStatus": "private", "publishAt": item.publish_at.isoformat(), "selfDeclaredMadeForKids": False, "embeddable": True},
    }
    mime_type = mimetypes.guess_type(item.path.name)[0] or "video/mp4"
    request = youtube.videos().insert(
        part="snippet,status", body=body, notifySubscribers=item.notify_subscribers,
        media_body=MediaFileUpload(str(item.path), mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return {"file": item.path.name, "title": item.title, "id": response["id"], "url": f"https://youtu.be/{response['id']}", "publishAt": item.publish_at.isoformat(), "notifySubscribers": str(item.notify_subscribers).lower()}


def set_thumbnail(youtube, video_id: str) -> None:
    if not THUMBNAIL_FILE.is_file():
        raise FileNotFoundError(f"Thumbnail is missing: {THUMBNAIL_FILE}")
    mime_type = mimetypes.guess_type(THUMBNAIL_FILE.name)[0] or "image/png"
    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(THUMBNAIL_FILE), mimetype=mime_type)).execute()


def upload_caption(youtube, video_id: str) -> str:
    if not CAPTION_FILE.is_file():
        raise FileNotFoundError(f"Caption file is missing: {CAPTION_FILE}")
    existing = youtube.captions().list(part="snippet", videoId=video_id).execute()
    for track in existing.get("items", []):
        snippet = track.get("snippet", {})
        if snippet.get("language") == "en" and snippet.get("name") == "English":
            return track["id"]
    response = youtube.captions().insert(
        part="snippet",
        body={"snippet": {"videoId": video_id, "language": "en", "name": "English", "isDraft": False}},
        media_body=MediaFileUpload(str(CAPTION_FILE), mimetype="application/octet-stream", resumable=True),
    ).execute()
    return response["id"]


def existing_videos(youtube, titles: set[str]) -> dict[str, dict[str, str]]:
    page = youtube.search().list(part="id,snippet", forMine=True, type="video", order="date", maxResults=50).execute()
    matches = {entry["snippet"]["title"]: entry["id"]["videoId"] for entry in page.get("items", []) if entry.get("snippet", {}).get("title") in titles and entry.get("id", {}).get("videoId")}
    if not matches:
        return {}
    details = youtube.videos().list(part="status", id=",".join(matches.values())).execute()
    statuses = {video["id"]: video.get("status", {}) for video in details.get("items", [])}
    return {title: {"id": video_id, "url": f"https://youtu.be/{video_id}", "publishAt": statuses.get(video_id, {}).get("publishAt", "")} for title, video_id in matches.items()}


def save_manifest(results: list[dict[str, str]]) -> None:
    MANIFEST_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    os.chmod(MANIFEST_FILE, 0o600)


def main() -> None:
    start_date = "2026-07-30"
    credentials = oauth_credentials()
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    preliminary = release_plan(start_date, "[FULL VIDEO LINK]")
    existing = existing_videos(youtube, {item.title for item in preliminary})
    results: list[dict[str, str]] = []
    long = preliminary[0]
    long_result = {"file": long.path.name, "title": long.title, **existing[long.title]} if long.title in existing else upload(youtube, long)
    set_thumbnail(youtube, long_result["id"])
    long_result["captionTrackId"] = upload_caption(youtube, long_result["id"])
    results.append(long_result)
    save_manifest(results)
    for item in release_plan(start_date, long_result["url"])[1:]:
        result = {"file": item.path.name, "title": item.title, **existing[item.title]} if item.title in existing else upload(youtube, item)
        results.append(result)
        save_manifest(results)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
