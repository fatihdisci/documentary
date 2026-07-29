#!/usr/bin/env python3
"""Upload and schedule the Pinta Island tortoise release set on YouTube.

The script is safe to rerun. It looks up exact planned titles first and keeps
an existing upload instead of creating a duplicate. Credentials stay local.
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
SECRETS = Path.home() / "ExtinctVideoBuilder" / "secrets"
CLIENT_FILE = SECRETS / "client_secret_190473268387-1uq00dn3lo4e0290k8s8842b8pl99ipi.apps.googleusercontent.com.json"
TOKEN_FILE = SECRETS / "youtube-upload-token.json"
MANIFEST_FILE = SECRETS / "youtube-pinta-island-tortoise-schedule.json"
THUMBNAIL_FILE = DOWNLOADS / "pinta-island-tortoise-youtube-thumbnail.jpg"
SCOPE = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


@dataclass(frozen=True)
class ScheduledVideo:
    file_name: str
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
    long_description = (
        "For decades, one giant tortoise carried the fate of an entire lineage. "
        "Lonesome George was the last known pure Pinta Island tortoise, but his story "
        "began long before he became a conservation icon.\n\n"
        "This documentary follows the tortoises of Pinta through an arid volcanic world, "
        "centuries of human exploitation, the destruction caused by introduced goats, "
        "George's rediscovery, and the genetic clues that still survive in hybrid tortoises today.\n\n"
        "Sources and further reading:\n"
        "Charles Darwin Foundation: https://datazone.darwinfoundation.org/en/checklist/?species=5266\n"
        "Galapagos Conservancy: https://www.galapagos.org/about_galapagos/lonesome-george/\n"
        "American Museum of Natural History: https://www.amnh.org/explore/preserving-lonesome-george\n"
        "Edwards et al. (2013): https://doi.org/10.1016/j.biocon.2012.10.014\n\n"
        "Chapters:\n"
        "00:00 The last one\n00:23 An island lineage\n00:46 Life on dry Pinta\n"
        "01:09 Built to reach\n01:30 A slow daily rhythm\n01:54 The island's gardener\n"
        "02:14 Taken from the island\n02:41 Three goats\n03:06 George is found\n"
        "03:30 Forty years alone\n03:54 A living genetic trace\n04:23 One island, one warning\n\n"
        "#PintaIslandTortoise #LonesomeGeorge #ExtinctAnimals #Galapagos #Conservation"
    )
    common_tags = [
        "Pinta Island tortoise", "Chelonoidis abingdonii", "Lonesome George",
        "extinct animals", "extinct reptiles", "Galapagos tortoise", "Galapagos Islands",
        "wildlife documentary", "natural history", "species extinction", "conservation",
        "Vanished Earth",
    ]
    return [
        ScheduledVideo(
            "pinta-island-tortoise_v01.mp4",
            "The Pinta Island Tortoise: The Species That Ended With One",
            long_description,
            common_tags,
            publish_time(start_date, 0, 22),
            True,
        ),
        ScheduledVideo(
            "pinta-island-tortoise-short-bb04c9b44c520070.mp4",
            "When One Death Ended a Species",
            "Lonesome George was the last known pure Pinta Island tortoise. When he died in twenty twelve, a species ended. But hybrid tortoises still carry part of the Pinta lineage.\n\nWatch the full documentary:\n" + long_url + "\n\n#LonesomeGeorge #ExtinctAnimals #Shorts",
            ["Lonesome George", "Pinta Island tortoise", "extinct animals", "Galapagos", "conservation", "documentary shorts", "Vanished Earth"],
            publish_time(start_date, 0, 23),
            True,
        ),
        ScheduledVideo(
            "pinta-island-tortoise-short-712d2fc5506be924.mp4",
            "The Giant That Shaped Pinta",
            "Pinta's giant tortoises did more than eat plants. They shaped the island through browsing, trampling and seed dispersal. Then ships began to take them away.\n\nWatch the full documentary:\n" + long_url + "\n\n#Galapagos #ExtinctAnimals #Shorts",
            ["Pinta Island tortoise", "ecosystem engineer", "Galapagos", "extinct animals", "natural history", "conservation", "documentary shorts", "Vanished Earth"],
            publish_time(start_date, 1, 18),
            True,
        ),
        ScheduledVideo(
            "pinta-island-tortoise-short-b62f1cc1d07d7597.mp4",
            "Why This Tortoise Had a Saddle Shell",
            "The Pinta tortoise had a saddle-shaped shell, long neck and long legs. On a dry island, that extra reach helped it browse higher vegetation.\n\nWatch the full documentary:\n" + long_url + "\n\n#GalapagosTortoise #NaturalHistory #Shorts",
            ["Pinta Island tortoise", "saddleback tortoise", "Galapagos tortoise", "extinct reptiles", "natural history", "wildlife documentary", "documentary shorts", "Vanished Earth"],
            publish_time(start_date, 1, 23),
            False,
        ),
    ]


def upload(youtube, item: ScheduledVideo) -> dict[str, str]:
    path = DOWNLOADS / item.file_name
    if not path.is_file():
        raise FileNotFoundError(f"Video is missing: {path}")
    if item.publish_at <= datetime.now(ISTANBUL):
        raise ValueError(f"Publish time is not in the future: {item.publish_at.isoformat()}")
    body = {
        "snippet": {
            "title": item.title,
            "description": item.description,
            "tags": item.tags,
            "categoryId": "15",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": item.publish_at.isoformat(),
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }
    mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        notifySubscribers=item.notify_subscribers,
        media_body=MediaFileUpload(str(path), mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return {
        "file": item.file_name,
        "title": item.title,
        "id": response["id"],
        "url": f"https://youtu.be/{response['id']}",
        "publishAt": item.publish_at.isoformat(),
        "notifySubscribers": str(item.notify_subscribers).lower(),
    }


def set_thumbnail(youtube, video_id: str) -> None:
    if not THUMBNAIL_FILE.is_file():
        raise FileNotFoundError(f"Thumbnail is missing: {THUMBNAIL_FILE}")
    mime_type = mimetypes.guess_type(THUMBNAIL_FILE.name)[0] or "image/jpeg"
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(THUMBNAIL_FILE), mimetype=mime_type),
    ).execute()


def existing_videos(youtube, titles: set[str]) -> dict[str, dict[str, str]]:
    page = youtube.search().list(
        part="id,snippet", forMine=True, type="video", order="date", maxResults=50
    ).execute()
    matches = {
        entry["snippet"]["title"]: entry["id"]["videoId"]
        for entry in page.get("items", [])
        if entry.get("snippet", {}).get("title") in titles and entry.get("id", {}).get("videoId")
    }
    if not matches:
        return {}
    details = youtube.videos().list(part="status", id=",".join(matches.values())).execute()
    statuses = {video["id"]: video.get("status", {}) for video in details.get("items", [])}
    return {
        title: {"id": video_id, "url": f"https://youtu.be/{video_id}", "publishAt": statuses.get(video_id, {}).get("publishAt", "")}
        for title, video_id in matches.items()
    }


def save_manifest(results: list[dict[str, str]]) -> None:
    MANIFEST_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    os.chmod(MANIFEST_FILE, 0o600)


def main() -> None:
    start_date = "2026-07-28"
    credentials = oauth_credentials()
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    preliminary = release_plan(start_date, "[FULL VIDEO LINK]")
    existing = existing_videos(youtube, {item.title for item in preliminary})
    results: list[dict[str, str]] = []

    long = preliminary[0]
    if long.title in existing:
        long_result = {"file": long.file_name, "title": long.title, **existing[long.title]}
    else:
        print(f"Uploading {long.file_name} for {long.publish_at.isoformat()}")
        long_result = upload(youtube, long)
    set_thumbnail(youtube, long_result["id"])
    results.append(long_result)
    save_manifest(results)

    for item in release_plan(start_date, long_result["url"])[1:]:
        if item.title in existing:
            result = {"file": item.file_name, "title": item.title, **existing[item.title]}
        else:
            print(f"Uploading {item.file_name} for {item.publish_at.isoformat()}")
            result = upload(youtube, item)
        results.append(result)
        save_manifest(results)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
