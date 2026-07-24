#!/usr/bin/env python3
"""Upload and schedule the Steller's sea cow release set on YouTube.

Runs a local OAuth browser flow on first use. Credentials and the refresh token
remain on this computer; no password or token is printed or transmitted to chat.
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
ROOT = Path("/Users/fatihdisci")
DOWNLOADS = ROOT / "Downloads"
SECRETS = ROOT / "ExtinctVideoBuilder" / "secrets"
CLIENT_FILE = SECRETS / "client_secret_190473268387-1uq00dn3lo4e0290k8s8842b8pl99ipi.apps.googleusercontent.com.json"
TOKEN_FILE = SECRETS / "youtube-upload-token.json"
MANIFEST_FILE = SECRETS / "youtube-stellers-sea-cow-schedule.json"
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
    return (start + timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0, microsecond=0)


def release_plan(start_date: str) -> list[ScheduledVideo]:
    long_description = (
        "It was one of the largest animals in the northern Pacific. Then, within about 27 years of its first scientific description, Steller's sea cow was gone. This is the story of a gentle giant, the cold waters around Bering Island, and an extinction that happened in plain sight.\n\n"
        "#ExtinctAnimals #StellersSeaCow #OceanHistory #WildlifeDocumentary"
    )
    short_link = "[FULL VIDEO LINK]"
    return [
        ScheduledVideo(
            "stellers-sea-cow_v01.mp4",
            "Steller's Sea Cow: The Giant Lost in 27 Years",
            long_description,
            ["Steller's sea cow", "extinct animals", "extinction", "marine mammals", "Bering Island", "Bering Sea", "ocean documentary", "wildlife documentary", "animal history", "kelp forest", "Georg Steller", "lost species"],
            publish_time(start_date, 0, 20),
        ),
        ScheduledVideo(
            "stellers-sea-cow-short-1e388eaa25ba610f.mp4",
            "Gone in Just 27 Years",
            "Steller's sea cow was first described in 1741. By 1768, it was gone.\n\nThe final population lived in a small area and could recover slowly. Repeated hunting was enough to push this giant of the kelp forest over the edge. No rescue plan existed. No one was counting the last survivors.\n\nOne of the northern Pacific's largest animals vanished within a single human lifetime.\n\nWatch the full documentary:\n" + short_link + "\n\n#Extinction #StellersSeaCow #Conservation #Shorts",
            ["Steller's sea cow extinction", "gone in 27 years", "extinct animals", "animal extinction", "conservation", "Bering Island", "Commander Islands", "ocean history", "wildlife documentary", "marine mammal", "Vanished Earth", "documentary shorts"],
            publish_time(start_date, 0, 23),
        ),
        ScheduledVideo(
            "stellers-sea-cow-short-be1566a7dea80820.mp4",
            "The Only Scientist Who Saw This Giant Alive",
            "After a shipwreck in 1741, Georg Steller saw a giant animal moving through the shallows of Bering Island.\n\nSteller's sea cow lived in cold kelp forests near the shore. That coastline gave it food, but it also made the animals easy for people to reach.\n\nWatch the full documentary:\n" + short_link + "\n\n#StellersSeaCow #ExtinctAnimals #OceanHistory #Shorts",
            ["Steller's sea cow", "Georg Steller", "Bering Island", "extinct animals", "kelp forest", "marine mammal", "ocean history", "wildlife documentary", "animal extinction", "Commander Islands", "Vanished Earth", "documentary shorts"],
            publish_time(start_date, 1, 2),
        ),
        ScheduledVideo(
            "stellers-sea-cow-short-e9e13baa07f5cc1a.mp4",
            "The Kelp Forest Lost Its Giant",
            "Steller's sea cows ate kelp in the cold shallows of the northern Pacific.\n\nScientists are still exploring what a giant grazer like this may have meant for those forests. By feeding near the surface, the animals may have changed how light reached the kelp below.\n\nWatch the full documentary:\n" + short_link + "\n\n#StellersSeaCow #KelpForest #ExtinctAnimals #Shorts",
            ["Steller's sea cow", "kelp forest", "extinct animals", "marine ecosystem", "ocean ecology", "northern Pacific", "wildlife documentary", "animal extinction", "sea cow", "conservation", "Vanished Earth", "documentary shorts"],
            publish_time(start_date, 1, 6),
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
        media_body=MediaFileUpload(str(path), mimetype=mime_type, resumable=True, chunksize=8 * 1024 * 1024),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return {"id": response["id"], "url": f"https://youtu.be/{response['id']}", "publishAt": item.publish_at.isoformat()}


def existing_videos(youtube, titles: set[str]) -> dict[str, dict[str, str]]:
    """Find already-uploaded planned videos to make retries safe."""
    page = youtube.search().list(
        part="id,snippet",
        forMine=True,
        type="video",
        order="date",
        maxResults=50,
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
        title: {
            "id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "publishAt": statuses.get(video_id, {}).get("publishAt", ""),
        }
        for title, video_id in matches.items()
    }


def save_manifest(results: list[dict[str, str]]) -> None:
    MANIFEST_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    os.chmod(MANIFEST_FILE, 0o600)


def main() -> None:
    plan = release_plan("2026-07-24")
    credentials = oauth_credentials()
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    existing = existing_videos(youtube, {item.title for item in plan})
    results = []
    for item in plan:
        if item.title in existing:
            print(f"Already uploaded: {item.title}")
            results.append({"file": item.file_name, "title": item.title, **existing[item.title]})
            save_manifest(results)
            continue
        print(f"Uploading {item.file_name} for {item.publish_at.isoformat()}")
        results.append({"file": item.file_name, "title": item.title, **upload(youtube, item)})
        save_manifest(results)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
