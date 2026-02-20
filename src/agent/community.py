"""
Community Engagement — Reply to YouTube comments in the channel's voice.

YouTube's algorithm rewards channels that engage their community. Reply rate
is a ranking signal. But more importantly, thoughtful replies build real
audience loyalty and surface content ideas through conversation.

This module:
  1. Fetches unreplied comments from recently published videos
  2. Filters for comments worth responding to (questions, praise, ideas —
     not spam, not "first!", not single emojis)
  3. Generates replies in the channel's voice using the LLM + content prompt
  4. Posts the replies via the YouTube API
  5. Logs everything and respects rate limits

Safety guardrails:
  - Max replies per session (avoids looking like a bot)
  - Skips negative/toxic comments (don't engage trolls)
  - Skips single-word or low-effort comments
  - Uses the channel's actual voice/persona, not generic boilerplate
  - Records every reply for the training dataset
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import CHANNELS_DIR


MAX_REPLIES_PER_SESSION = 10
MIN_COMMENT_LENGTH = 15


def _get_youtube_service(channel_id: str):
    from src.agent.analytics import _get_youtube_service as _get_yt
    return _get_yt(channel_id)


def fetch_unreplied_comments(
    channel_id: str,
    max_per_video: int = 20,
) -> list[dict]:
    """
    Fetch comments that haven't been replied to by the channel owner.
    Returns flat list sorted by likes (most engagement first).
    """
    from src.publishing.calendar_manager import load_calendar

    cal = load_calendar()
    video_ids = [
        s["youtube_video_id"]
        for s in cal["slots"]
        if s["channel_id"] == channel_id and s.get("youtube_video_id")
    ]

    if not video_ids:
        return []

    yt = _get_youtube_service(channel_id)
    unreplied = []

    # Get channel ID to identify owner replies
    try:
        ch_resp = yt.channels().list(part="id", mine=True).execute()
        owner_channel_id = ch_resp["items"][0]["id"] if ch_resp.get("items") else None
    except Exception:
        owner_channel_id = None

    already_replied = _load_replied_ids(channel_id)

    for vid in video_ids:
        try:
            resp = yt.commentThreads().list(
                part="snippet,replies",
                videoId=vid,
                maxResults=max_per_video,
                order="relevance",
                textFormat="plainText",
            ).execute()

            for item in resp.get("items", []):
                thread_id = item["id"]
                if thread_id in already_replied:
                    continue

                snippet = item["snippet"]["topLevelComment"]["snippet"]
                comment_text = snippet.get("textDisplay", "")
                author = snippet.get("authorDisplayName", "")

                # Skip if the comment IS from the channel owner
                if owner_channel_id and snippet.get("authorChannelId", {}).get("value") == owner_channel_id:
                    continue

                # Check if owner already replied in the thread
                has_owner_reply = False
                if item.get("replies"):
                    for reply in item["replies"].get("comments", []):
                        reply_author_id = reply["snippet"].get("authorChannelId", {}).get("value")
                        if reply_author_id == owner_channel_id:
                            has_owner_reply = True
                            break

                if has_owner_reply:
                    continue

                unreplied.append({
                    "thread_id": thread_id,
                    "comment_id": item["snippet"]["topLevelComment"]["id"],
                    "video_id": vid,
                    "author": author,
                    "text": comment_text,
                    "likes": snippet.get("likeCount", 0),
                    "published_at": snippet.get("publishedAt", ""),
                })

        except Exception as e:
            err_str = str(e)
            if "commentsDisabled" in err_str or "403" in err_str:
                continue
            print(f"[Community] Failed to fetch comments for {vid}: {e}")
            continue

    unreplied.sort(key=lambda x: x["likes"], reverse=True)
    return unreplied


def _filter_worth_replying(comments: list[dict]) -> list[dict]:
    """
    Filter comments down to ones actually worth engaging with.
    Skip spam, single words, emojis, low-effort, and toxic content.
    """
    worth = []
    for c in comments:
        text = c["text"].strip()

        if len(text) < MIN_COMMENT_LENGTH:
            continue

        lower = text.lower()

        # Skip obvious low-effort
        if lower in ("first", "first!", "nice", "cool", "wow", "lol", "same"):
            continue

        # Skip emoji-only
        if all(not ch.isalpha() for ch in text):
            continue

        # Skip self-promotion (links)
        if "http" in lower or "subscribe to" in lower or "check out my" in lower:
            continue

        worth.append(c)

    return worth


def _generate_replies(
    channel_id: str,
    comments: list[dict],
) -> list[dict]:
    """
    Generate thoughtful replies for each comment using the LLM
    in the channel's voice.
    """
    from src.utils.llm import chat_json
    from src.agent.script_generator import _load_content_prompt

    content_prompt = _load_content_prompt(channel_id)

    # Extract just the voice/tone section for efficiency
    voice_section = content_prompt
    if "## Voice & Tone" in content_prompt:
        start = content_prompt.index("## Voice & Tone")
        end = content_prompt.find("\n## ", start + 1)
        voice_section = content_prompt[start:end] if end > 0 else content_prompt[start:]

    # Batch comments for efficiency (up to MAX_REPLIES_PER_SESSION)
    batch = comments[:MAX_REPLIES_PER_SESSION]

    comments_text = "\n\n".join(
        f"[Comment {i+1}] by @{c['author']} ({c['likes']} likes):\n\"{c['text']}\""
        for i, c in enumerate(batch)
    )

    result = chat_json(
        "You are the voice behind a YouTube channel. You reply to viewer comments "
        "in the EXACT same voice and tone as the channel's content.\n\n"
        "Rules for replies:\n"
        "- Stay in character. Match the channel's voice/tone perfectly.\n"
        "- Keep replies SHORT (1-3 sentences). This is YouTube, not an essay.\n"
        "- Be genuine and warm, never generic ('thanks for watching!' is banned).\n"
        "- If a viewer shares a personal thought, acknowledge it specifically.\n"
        "- If they ask a question, give a thoughtful mini-answer.\n"
        "- If they share a related idea, build on it.\n"
        "- NEVER be sarcastic, dismissive, or argumentative.\n"
        "- NEVER use hashtags, emojis, or self-promotion in replies.\n"
        "- If a comment is negative/toxic, respond with: {\"reply\": null, \"reason\": \"toxic\"}\n"
        "- It's okay to NOT reply to some comments — null reply means skip.\n\n"
        f"## Channel Voice\n\n{voice_section}\n\n"
        "Respond with valid JSON:\n"
        "{\n"
        "  \"replies\": [\n"
        "    {\"comment_index\": 1, \"reply\": \"your reply text or null\", \"reason\": \"why this reply\"},\n"
        "    ...\n"
        "  ]\n"
        "}",
        f"## Comments to Reply To\n\n{comments_text}",
        temperature=1.0,
    )

    replies = []
    for r in result.get("replies", []):
        idx = r.get("comment_index", 0) - 1
        if 0 <= idx < len(batch) and r.get("reply"):
            replies.append({
                **batch[idx],
                "reply_text": r["reply"],
                "reply_reason": r.get("reason", ""),
            })

    return replies


def post_replies(
    channel_id: str,
    replies: list[dict],
) -> list[dict]:
    """
    Post the generated replies to YouTube and log them.
    """
    yt = _get_youtube_service(channel_id)
    posted = []

    for r in replies:
        try:
            yt.comments().insert(
                part="snippet",
                body={
                    "snippet": {
                        "parentId": r["comment_id"],
                        "textOriginal": r["reply_text"],
                    }
                },
            ).execute()

            posted.append(r)
            print(
                f"[Community] Replied to @{r['author']}: "
                f"\"{r['reply_text'][:60]}...\""
            )

        except Exception as e:
            print(f"[Community] Failed to reply to {r['comment_id']}: {e}")
            continue

    # Record replied IDs so we don't double-reply
    replied_ids = _load_replied_ids(channel_id)
    for r in posted:
        replied_ids.add(r["thread_id"])
    _save_replied_ids(channel_id, replied_ids)

    return posted


def engage_community(channel_id: str) -> dict:
    """
    Full community engagement pipeline:
    1. Fetch unreplied comments
    2. Filter for quality
    3. Generate replies in channel voice
    4. Post them
    5. Log everything

    Returns summary of actions taken.
    """
    print(f"[Community] Engaging with audience for {channel_id}...")

    # Fetch
    unreplied = fetch_unreplied_comments(channel_id)
    print(f"[Community] Found {len(unreplied)} unreplied comments")

    if not unreplied:
        return {"comments_found": 0, "replies_posted": 0}

    # Filter
    worth = _filter_worth_replying(unreplied)
    print(f"[Community] {len(worth)} worth replying to (filtered from {len(unreplied)})")

    if not worth:
        return {"comments_found": len(unreplied), "worth_replying": 0, "replies_posted": 0}

    # Generate
    replies = _generate_replies(channel_id, worth)
    print(f"[Community] Generated {len(replies)} replies")

    if not replies:
        return {"comments_found": len(unreplied), "worth_replying": len(worth), "replies_posted": 0}

    # Post
    posted = post_replies(channel_id, replies)
    print(f"[Community] Posted {len(posted)} replies")

    # Save engagement log
    _save_engagement_log(channel_id, posted)

    return {
        "comments_found": len(unreplied),
        "worth_replying": len(worth),
        "replies_generated": len(replies),
        "replies_posted": len(posted),
        "replies": [
            {"author": r["author"], "comment": r["text"][:80], "reply": r["reply_text"][:80]}
            for r in posted
        ],
    }


def _load_replied_ids(channel_id: str) -> set:
    path = CHANNELS_DIR / channel_id / "replied_threads.json"
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_replied_ids(channel_id: str, ids: set):
    path = CHANNELS_DIR / channel_id / "replied_threads.json"
    path.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")


def _save_engagement_log(channel_id: str, posted: list[dict]):
    log_path = CHANNELS_DIR / channel_id / "engagement_log.json"
    existing = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    for r in posted:
        existing.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "video_id": r["video_id"],
            "thread_id": r["thread_id"],
            "author": r["author"],
            "comment": r["text"],
            "reply": r["reply_text"],
            "reason": r.get("reply_reason", ""),
            "comment_likes": r["likes"],
        })

    log_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )
