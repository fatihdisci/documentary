#!/usr/bin/env python3
"""Upload and schedule the Carolina parakeet release set on YouTube.

The script is safe to rerun: it searches the channel for its exact planned
titles and keeps already scheduled uploads instead of creating duplicates.
Credentials and the refresh token remain on this computer; no password or
token is printed or transmitted to chat.
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
ROOT = Path.home()
DOWNLOADS = ROOT / "Downloads"
SECRETS = ROOT / "ExtinctVideoBuilder" / "secrets"
CLIENT_FILE = SECRETS / "client_secret_190473268387-1uq00dn3lo4e0290k8s8842b8pl99ipi.apps.googleusercontent.com.json"
TOKEN_FILE = SECRETS / "youtube-upload-token.json"
MANIFEST_FILE = SECRETS / "youtube-carolina-parakeet-schedule.json"
THUMBNAIL_FILE = DOWNLOADS / "Carolina_Parakeet_YouTube_thumbnail_202607261310.jpeg"
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
        "Bright green parrots once crossed the woods and rivers of the eastern United States. "
        "The Carolina parakeet was noisy, social and hard to miss. Yet it disappeared "
        "before people understood how to save it.\n\n"
        "This documentary follows its life in old woods and wetlands, its clash with a "
        "changing land, and the uncertain final years that ended with the last known bird "
        "in nineteen eighteen.\n\n"
        "Sources and further reading:\n"
        "American Ornithological Society: https://checklist.americanornithology.org/taxa/590\n"
        "Natural History Museum, London: https://www.nhm.ac.uk/discover/news/2021/july/reviving-the-cold-case-of-the-carolina-parakeet-extinction.html\n"
        "Burgio et al. (2021): https://doi.org/10.1017/S0959270921000241\n\n"
        "#CarolinaParakeet #ExtinctAnimals #NaturalHistory #Conservation"
    )
    return [
        ScheduledVideo(
            "carolina-parakeet_v01.mp4",
            "The Carolina Parakeet: America's Lost Native Parrot",
            long_description,
            ["Carolina parakeet", "Conuropsis carolinensis", "extinct animals", "extinct birds", "native parrot", "North American wildlife", "natural history", "wildlife documentary", "bird extinction", "conservation", "Cincinnati Zoo", "Vanished Earth"],
            publish_time(start_date, 0, 22),
            True,
        ),
        ScheduledVideo(
            "carolina-parakeet-short-4fd2508ec73603e3.mp4",
            "The Last Known Carolina Parakeet",
            "The last known Carolina parakeet died in captivity on February twenty-first, nineteen eighteen. Later wild reports never proved that a flock had survived.\n\nWatch the full documentary:\n" + long_url + "\n\n#CarolinaParakeet #ExtinctBirds #Shorts",
            ["Carolina parakeet", "last Carolina parakeet", "Incas", "Cincinnati Zoo", "extinct birds", "extinct animals", "wildlife history", "conservation", "Vanished Earth", "documentary shorts"],
            publish_time(start_date, 0, 23),
            True,
        ),
        ScheduledVideo(
            "carolina-parakeet-short-571bec974c0e94de.mp4",
            "America Once Had a Native Parrot",
            "The Carolina parakeet was bright, loud and social. It was the only parrot native to the eastern and central United States.\n\nWatch the full documentary:\n" + long_url + "\n\n#CarolinaParakeet #ExtinctAnimals #Shorts",
            ["Carolina parakeet", "native parrot", "extinct birds", "extinct animals", "North American wildlife", "natural history", "wildlife documentary", "conservation", "Vanished Earth", "documentary shorts"],
            publish_time(start_date, 1, 18),
            True,
        ),
        ScheduledVideo(
            "carolina-parakeet-short-71f5474adaa5c04d.mp4",
            "Why a Bright Bird Was Not Safe",
            "Old trees disappeared. Birds were shot, caught and collected. The final cause is not one clean answer, but human pressure was all around the Carolina parakeet.\n\nWatch the full documentary:\n" + long_url + "\n\n#CarolinaParakeet #Conservation #Shorts",
            ["Carolina parakeet extinction", "habitat loss", "bird conservation", "extinct animals", "native parrot", "wildlife documentary", "North American wildlife", "Vanished Earth", "documentary shorts"],
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
            "categoryId": "27",
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
    start_date = "2026-07-26"
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
