"""
YouTube Publisher — Upload and schedule videos via YouTube Data API v3.

First-time setup:
    1. Go to https://console.cloud.google.com
    2. Create a project (or use an existing one)
    3. Enable the "YouTube Data API v3"
    4. Create OAuth 2.0 credentials (Desktop application type)
    5. Download the JSON file and save it as client_secrets.json
       in the project root (next to .env)
    6. Run a publish command — a browser window will open for authorization.
       After approving, a youtube_token.json is saved so you won't need
       to re-authorize again (unless the token expires/is revoked).

Usage:
    from src.publisher import upload_to_youtube

    upload_to_youtube(
        video_path="workspace/my_video/output/my_video.mp4",
        title="My Video Title",
        description="My description with #hashtags",
        tags=["tag1", "tag2"],
        privacy="private",            # "public", "private", or "unlisted"
        publish_at="2026-02-16T14:00:00Z",  # optional scheduled publish time
        is_short=True,                # optional — adds #Shorts tag
    )
"""

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# OAuth2 scope for uploading videos
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Default locations for credentials
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CLIENT_SECRETS = _PROJECT_ROOT / "client_secrets.json"
_TOKEN_FILE = _PROJECT_ROOT / "youtube_token.json"

# YouTube API category IDs (common ones)
CATEGORIES = {
    "film":          1,
    "autos":         2,
    "music":         10,
    "pets":          15,
    "sports":        17,
    "travel":        19,
    "gaming":        20,
    "people":        22,  # People & Blogs
    "comedy":        23,
    "entertainment": 24,
    "news":          25,
    "howto":         26,
    "education":     27,
    "science":       28,
}


def _authenticate(
    client_secrets: Path | str | None = None,
    token_file: Path | str | None = None,
) -> Credentials:
    """
    Authenticate with YouTube via OAuth2.

    On first run, opens a browser for user authorization.
    Subsequent runs use the cached token.
    """
    client_secrets = Path(client_secrets or _CLIENT_SECRETS)
    token_file = Path(token_file or _TOKEN_FILE)

    creds = None

    # Load existing token if available
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), _SCOPES)
        except Exception:
            creds = None

    # Refresh or re-authorize
    if creds and creds.expired and creds.refresh_token:
        print("[YouTube] Refreshing access token...")
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not client_secrets.exists():
            raise FileNotFoundError(
                f"[YouTube] OAuth credentials not found at: {client_secrets}\n"
                f"\n"
                f"To set up YouTube publishing:\n"
                f"  1. Go to https://console.cloud.google.com\n"
                f"  2. Create a project and enable 'YouTube Data API v3'\n"
                f"  3. Create OAuth 2.0 credentials (Desktop application)\n"
                f"  4. Download the JSON and save it as:\n"
                f"     {client_secrets}\n"
            )
        print("[YouTube] Opening browser for authorization...")
        print("[YouTube] (First time only — token will be saved for future use)")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets), _SCOPES
        )
        creds = flow.run_local_server(port=0)

    # Save token for next time
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload_to_youtube(
    video_path: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category: str = "people",
    privacy: str = "private",
    publish_at: str | None = None,
    is_short: bool = False,
    language: str = "en",
    embeddable: bool = True,
    made_for_kids: bool = False,
    contains_synthetic_media: bool = False,
    license_type: str = "youtube",
    public_stats_viewable: bool = True,
    client_secrets: Path | str | None = None,
    token_file: Path | str | None = None,
) -> dict:
    """
    Upload a video to YouTube with optional scheduled publishing.

    Args:
        video_path: Path to the MP4 file to upload.
        title: Video title (max 100 chars).
        description: Video description.
        tags: List of keyword tags.
        category: Category name (e.g., "people", "education", "entertainment").
                  See CATEGORIES dict for options.
        privacy: "public", "private", or "unlisted".
                 Must be "private" if using publish_at.
        publish_at: ISO 8601 datetime for scheduled publishing (e.g.,
                    "2026-02-16T14:00:00Z"). Video will auto-publish at this
                    time. Requires privacy="private".
        is_short: If True, adds "#Shorts" to tags for YouTube Shorts detection.
        language: Default language code (e.g., "en", "es", "fr"). Default: "en".
        embeddable: Whether the video can be embedded on other sites. Default: True.
        made_for_kids: Whether the video is made for kids. Default: False.
        contains_synthetic_media: Whether the video contains AI-generated or
            altered content (deepfakes, fake events, etc.). Default: False —
            stock b-roll with AI narration does not meet YouTube's criteria.
        license_type: "youtube" for standard license, "creativeCommon" for CC-BY.
            Default: "youtube".
        public_stats_viewable: Whether like/view counts are publicly visible.
            Default: True.
        client_secrets: Path to OAuth client_secrets.json (default: project root).
        token_file: Path to cached token file (default: project root).

    Returns:
        Dict with upload result: {"video_id": "...", "url": "...", "status": "..."}.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Validate scheduling
    if publish_at and privacy != "private":
        print("[YouTube] Warning: publish_at requires privacy='private'. Setting to private.")
        privacy = "private"

    # Build tags
    all_tags = list(tags or [])
    if is_short and "#Shorts" not in all_tags and "Shorts" not in all_tags:
        all_tags.append("Shorts")

    # Resolve category ID
    category_id = str(CATEGORIES.get(category.lower(), 22))

    # Truncate title if needed
    if len(title) > 100:
        print(f"[YouTube] Warning: Title truncated to 100 chars (was {len(title)})")
        title = title[:97] + "..."

    # Authenticate
    creds = _authenticate(client_secrets, token_file)
    youtube = build("youtube", "v3", credentials=creds)

    # Build request body
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": all_tags,
            "categoryId": category_id,
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
            "containsSyntheticMedia": contains_synthetic_media,
            "embeddable": embeddable,
            "license": license_type,
            "publicStatsViewable": public_stats_viewable,
        },
    }

    if publish_at:
        body["status"]["publishAt"] = publish_at

    # Upload with resumable media
    file_size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"[YouTube] Uploading: {video_path.name} ({file_size_mb:.1f} MB)")
    print(f"[YouTube] Title: {title}")
    if publish_at:
        print(f"[YouTube] Scheduled publish: {publish_at}")
    else:
        print(f"[YouTube] Privacy: {privacy}")

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    # Execute upload with progress reporting
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"[YouTube] Upload progress: {pct}%")

    video_id = response["id"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    print(f"[YouTube] Upload complete!")
    print(f"[YouTube] Video ID: {video_id}")
    print(f"[YouTube] URL: {video_url}")

    if publish_at:
        print(f"[YouTube] Will auto-publish at: {publish_at}")

    return {
        "video_id": video_id,
        "url": video_url,
        "status": privacy,
        "publish_at": publish_at,
    }


def publish_workspace_video(
    workspace_dir: str | Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category: str = "people",
    privacy: str = "private",
    publish_at: str | None = None,
    is_short: bool = False,
    version: str | None = None,
) -> dict:
    """
    Convenience function to publish the latest video from a workspace folder.

    Finds the most recent .mp4 in the workspace's output/ directory and uploads it.

    Args:
        workspace_dir: Path to the project workspace (e.g., "workspace/business_short_01").
        title, description, tags, etc.: Same as upload_to_youtube.
        version: Specific version to upload (e.g., "v2"). If None, uploads the latest.

    Returns:
        Dict with upload result.
    """
    workspace_dir = Path(workspace_dir)
    output_dir = workspace_dir / "output"

    if not output_dir.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    # Find the video file
    mp4s = sorted(output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not mp4s:
        raise FileNotFoundError(f"No .mp4 files found in: {output_dir}")

    if version:
        # Look for a specific version
        matching = [p for p in mp4s if version in p.stem]
        if not matching:
            raise FileNotFoundError(f"No video matching version '{version}' in: {output_dir}")
        video_path = matching[-1]
    else:
        # Use the most recent
        video_path = mp4s[-1]

    print(f"[YouTube] Selected video: {video_path.name}")

    return upload_to_youtube(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        category=category,
        privacy=privacy,
        publish_at=publish_at,
        is_short=is_short,
    )
