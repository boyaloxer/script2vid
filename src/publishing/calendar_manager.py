"""
Release Calendar — Schedule, track, and auto-publish videos across channels.

Manages a per-channel release cadence with placeholder slots that get filled
as videos are created.  Integrates with src.publisher for auto-uploading
when a slot's scheduled time approaches.

Data lives in calendar_data.json at the project root.

Quick-start
-----------
    # 1. Define a channel + cadence
    python -m src.publishing.calendar_manager add-channel deep_thoughts \\
        --name "Deep Thoughts For Zen" \\
        --days mon,wed,fri --time 14:00 --tz America/New_York \\
        --category entertainment --short

    # 2. Generate placeholder slots for the next 4 weeks
    python -m src.publishing.calendar_manager generate --weeks 4

    # 3. View the schedule
    python -m src.publishing.calendar_manager status

    # 4. Open the web calendar
    python -m src.publishing.calendar_manager view
"""

import argparse
import json
import sys
import uuid
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Paths ─────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CALENDAR_FILE = _PROJECT_ROOT / "calendar_data.json"

# ── Constants ─────────────────────────────────────────────────────
SLOT_STATUSES = ("placeholder", "assigned", "uploaded", "published")

DAY_MAP = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday",
    "monday": "monday", "tuesday": "tuesday", "wednesday": "wednesday",
    "thursday": "thursday", "friday": "friday", "saturday": "saturday",
    "sunday": "sunday",
}

DAY_NUMBERS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Channel colour palette (for the GUI)
CHANNEL_COLOURS = [
    "#6366f1", "#ec4899", "#f59e0b", "#10b981",
    "#3b82f6", "#ef4444", "#8b5cf6", "#14b8a6",
]


# ════════════════════════════════════════════════════════════════════
#  Persistence
# ════════════════════════════════════════════════════════════════════

def _default_calendar() -> dict:
    return {"channels": {}, "slots": []}


def load_calendar(path: Path | str | None = None) -> dict:
    """Load the calendar JSON (or return an empty default)."""
    path = Path(path or _CALENDAR_FILE)
    if not path.exists():
        return _default_calendar()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure required top-level keys
        data.setdefault("channels", {})
        data.setdefault("slots", [])
        return data
    except (json.JSONDecodeError, OSError):
        return _default_calendar()


def save_calendar(data: dict, path: Path | str | None = None) -> None:
    """Persist the calendar JSON to disk."""
    path = Path(path or _CALENDAR_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ════════════════════════════════════════════════════════════════════
#  Channel CRUD
# ════════════════════════════════════════════════════════════════════

def add_channel(
    channel_id: str,
    name: str,
    days: list[str],
    time_str: str,
    timezone: str = "America/New_York",
    category: str = "people",
    tags: list[str] | None = None,
) -> dict:
    """Add (or update) a channel with a release cadence."""
    cal = load_calendar()

    # Normalise day names
    normalised = []
    for d in days:
        key = d.strip().lower()
        if key not in DAY_MAP:
            raise ValueError(
                f"Invalid day '{d}'. "
                "Use mon/tue/wed/thu/fri/sat/sun (or full names)."
            )
        normalised.append(DAY_MAP[key])

    # Validate timezone
    try:
        ZoneInfo(timezone)
    except KeyError:
        raise ValueError(f"Unknown timezone: {timezone}")

    # Assign a colour
    idx = len(cal["channels"]) % len(CHANNEL_COLOURS)
    existing = cal["channels"].get(channel_id)
    colour = existing["colour"] if existing and "colour" in existing else CHANNEL_COLOURS[idx]

    # Support multiple times per day (comma-separated or single value)
    times = [t.strip() for t in time_str.split(",")]

    cal["channels"][channel_id] = {
        "name": name,
        "cadence": {
            "days": normalised,
            "times": times,
            "timezone": timezone,
        },
        "default_category": category,
        "default_tags": tags or [],
        "colour": colour,
    }
    save_calendar(cal)
    times_str = " & ".join(times)
    print(f"[Calendar] Channel '{name}' ({channel_id}) saved.")
    print(f"  Schedule : {', '.join(normalised)} at {times_str} {timezone}")
    return cal["channels"][channel_id]


def remove_channel(channel_id: str) -> bool:
    """Remove a channel and all its slots."""
    cal = load_calendar()
    if channel_id not in cal["channels"]:
        print(f"[Calendar] Channel '{channel_id}' not found.")
        return False
    name = cal["channels"][channel_id]["name"]
    del cal["channels"][channel_id]
    before = len(cal["slots"])
    cal["slots"] = [s for s in cal["slots"] if s["channel_id"] != channel_id]
    removed = before - len(cal["slots"])
    save_calendar(cal)
    print(f"[Calendar] Removed '{name}' and {removed} slot(s).")
    return True


def list_channels() -> dict:
    """Return the channels dict."""
    return load_calendar()["channels"]


# ════════════════════════════════════════════════════════════════════
#  Slot generation & management
# ════════════════════════════════════════════════════════════════════

def generate_slots(channel_id: str | None = None, weeks: int = 4) -> list[dict]:
    """
    Generate placeholder slots for the next *weeks* weeks.

    If channel_id is given, only that channel is processed.
    Existing slots at the same time are not duplicated.
    """
    cal = load_calendar()
    targets = (
        {channel_id: cal["channels"][channel_id]}
        if channel_id and channel_id in cal["channels"]
        else cal["channels"]
    )

    if not targets:
        print("[Calendar] No channels defined. Use 'add-channel' first.")
        return []

    # Build a set of existing (channel, time) pairs to avoid dupes
    existing = {(s["channel_id"], s["scheduled_time"]) for s in cal["slots"]}

    new_slots: list[dict] = []
    now = datetime.now()

    for ch_id, ch in targets.items():
        cadence = ch["cadence"]
        tz = ZoneInfo(cadence["timezone"])
        # Support both legacy "time" (single string) and new "times" (list)
        raw_times = cadence.get("times") or [cadence.get("time", "12:00")]
        time_pairs = [tuple(map(int, t.split(":"))) for t in raw_times]
        target_weekdays = [DAY_NUMBERS[d] for d in cadence["days"]]

        cursor = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = cursor + timedelta(weeks=weeks)

        while cursor <= end:
            if cursor.weekday() in target_weekdays:
                for hour, minute in time_pairs:
                    slot_dt = cursor.replace(hour=hour, minute=minute)
                    slot_aware = slot_dt.replace(tzinfo=tz)

                    # Skip past slots
                    if slot_aware <= datetime.now(tz):
                        continue

                    iso = slot_aware.isoformat()
                    if (ch_id, iso) not in existing:
                        new_slots.append({
                            "id": uuid.uuid4().hex[:8],
                            "channel_id": ch_id,
                            "scheduled_time": iso,
                            "status": "placeholder",
                            "video_path": None,
                            "title": None,
                            "description": None,
                            "tags": None,
                            "workspace": None,
                            "is_vertical": False,
                            "youtube_video_id": None,
                            "youtube_url": None,
                        })
                        existing.add((ch_id, iso))

            cursor += timedelta(days=1)

    cal["slots"].extend(new_slots)
    cal["slots"].sort(key=lambda s: s["scheduled_time"])
    save_calendar(cal)

    print(f"[Calendar] Generated {len(new_slots)} new placeholder slot(s).")
    for s in new_slots[:8]:
        ch_name = targets.get(s["channel_id"], {}).get("name", s["channel_id"])
        print(f"  {s['scheduled_time']}  {ch_name}")
    if len(new_slots) > 8:
        print(f"  ... and {len(new_slots) - 8} more")
    return new_slots


def auto_assign(
    channel_id: str,
    video_path: str | Path,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    workspace: str | Path | None = None,
    is_vertical: bool = False,
) -> dict | None:
    """
    Assign a video to the next available placeholder slot for *channel_id*.
    Returns the updated slot dict, or None if no open slots exist.
    """
    cal = load_calendar()

    for slot in cal["slots"]:
        if slot["channel_id"] == channel_id and slot["status"] == "placeholder":
            slot["status"] = "assigned"
            slot["video_path"] = str(video_path)
            slot["title"] = title
            slot["description"] = description
            slot["tags"] = tags
            slot["workspace"] = str(workspace) if workspace else None
            slot["is_vertical"] = is_vertical
            save_calendar(cal)
            print(f"[Calendar] Assigned to slot {slot['id']} "
                  f"({slot['scheduled_time']})")
            return slot

    print(f"[Calendar] No open slots for channel '{channel_id}'. "
          f"Run 'generate' to create more.")
    return None


def assign_to_slot(
    slot_id: str,
    video_path: str | Path,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    workspace: str | Path | None = None,
) -> dict | None:
    """Assign a video to a specific slot by ID."""
    cal = load_calendar()
    for slot in cal["slots"]:
        if slot["id"] == slot_id:
            slot["status"] = "assigned"
            slot["video_path"] = str(video_path)
            if title is not None:
                slot["title"] = title
            if description is not None:
                slot["description"] = description
            if tags is not None:
                slot["tags"] = tags
            if workspace is not None:
                slot["workspace"] = str(workspace)
            save_calendar(cal)
            print(f"[Calendar] Slot {slot_id} updated → assigned.")
            return slot
    print(f"[Calendar] Slot '{slot_id}' not found.")
    return None


def update_slot(slot_id: str, **fields) -> dict | None:
    """Update arbitrary fields on a slot."""
    cal = load_calendar()
    for slot in cal["slots"]:
        if slot["id"] == slot_id:
            for k, v in fields.items():
                if k in slot:
                    slot[k] = v
            save_calendar(cal)
            return slot
    return None


def delete_slot(slot_id: str) -> bool:
    """Remove a single slot."""
    cal = load_calendar()
    before = len(cal["slots"])
    cal["slots"] = [s for s in cal["slots"] if s["id"] != slot_id]
    if len(cal["slots"]) < before:
        save_calendar(cal)
        return True
    return False


# ════════════════════════════════════════════════════════════════════
#  Publishing helpers
# ════════════════════════════════════════════════════════════════════

def get_due_slots(hours_ahead: int = 48) -> list[dict]:
    """Return assigned slots whose scheduled_time is within *hours_ahead*."""
    cal = load_calendar()
    now = datetime.now().astimezone()
    cutoff = now + timedelta(hours=hours_ahead)

    due = []
    for slot in cal["slots"]:
        if slot["status"] != "assigned" or not slot.get("video_path"):
            continue
        try:
            slot_time = datetime.fromisoformat(slot["scheduled_time"])
            if not slot_time.tzinfo:
                continue
            if now <= slot_time <= cutoff:
                due.append(slot)
        except (ValueError, TypeError):
            continue
    return due


def publish_due(hours_ahead: int = 48) -> list[dict]:
    """Upload + schedule all videos that are due within *hours_ahead*."""
    from src.publishing.publisher import upload_to_youtube  # avoid circular

    cal = load_calendar()
    due = get_due_slots(hours_ahead)

    if not due:
        print("[Calendar] No videos due for publishing in the "
              f"next {hours_ahead} hours.")
        return []

    print(f"[Calendar] {len(due)} video(s) due for upload.\n")
    results = []

    for slot in due:
        ch = cal["channels"].get(slot["channel_id"], {})
        title = slot.get("title") or "Untitled"
        tags = slot.get("tags") or ch.get("default_tags", [])
        category = ch.get("default_category", "people")

        print(f"[Calendar] Uploading: {title}")
        print(f"[Calendar] -> scheduled for {slot['scheduled_time']}")

        try:
            result = upload_to_youtube(
                video_path=slot["video_path"],
                title=title,
                description=slot.get("description") or "",
                tags=tags,
                category=category,
                privacy="private",
                publish_at=slot["scheduled_time"],
                is_short=slot.get("is_vertical", False),
                channel_id=slot["channel_id"],
            )
            # Mark uploaded
            update_slot(slot["id"],
                        status="uploaded",
                        youtube_video_id=result["video_id"],
                        youtube_url=result["url"])
            results.append({"slot_id": slot["id"], "result": result, "error": None})

        except Exception as e:
            print(f"[Calendar] Upload failed: {e}")
            results.append({"slot_id": slot["id"], "result": None, "error": str(e)})

    successes = sum(1 for r in results if r["error"] is None)
    print(f"\n[Calendar] Done — {successes}/{len(results)} uploaded successfully.")
    return results


# ════════════════════════════════════════════════════════════════════
#  Status / display helpers
# ════════════════════════════════════════════════════════════════════

def get_upcoming(channel_id: str | None = None, limit: int = 20) -> list[dict]:
    """Return the next *limit* future slots, optionally filtered by channel."""
    cal = load_calendar()
    now = datetime.now().astimezone()
    upcoming = []
    for slot in cal["slots"]:
        if channel_id and slot["channel_id"] != channel_id:
            continue
        try:
            st = datetime.fromisoformat(slot["scheduled_time"])
            if st >= now:
                upcoming.append(slot)
        except (ValueError, TypeError):
            continue
    return upcoming[:limit]


def print_status(channel_id: str | None = None) -> None:
    """Pretty-print the upcoming schedule to the console."""
    cal = load_calendar()
    channels = cal["channels"]
    slots = cal["slots"]

    if not channels:
        print("[Calendar] No channels configured.")
        print("  Use 'add-channel' to set one up.")
        return

    # Channels summary
    print("\n" + "=" * 56)
    print("         script2vid -- Release Calendar")
    print("=" * 56 + "\n")

    print("Channels:")
    for ch_id, ch in channels.items():
        cad = ch["cadence"]
        days_str = ", ".join(d[:3].title() for d in cad["days"])
        print(f"  * {ch['name']} ({ch_id})")
        times = cad.get("times") or [cad.get("time", "12:00")]
        times_str = " & ".join(times)
        print(f"    {days_str} at {times_str} {cad['timezone']}")

    # Upcoming slots
    upcoming = get_upcoming(channel_id, limit=20)
    if not upcoming:
        print("\nNo upcoming slots. Run 'generate' to create placeholders.")
        return

    STATUS_ICONS = {
        "placeholder": "[ ]",
        "assigned":    "[*]",
        "uploaded":    "[^]",
        "published":   "[+]",
    }

    print(f"\nUpcoming ({len(upcoming)} slots):")
    print("-" * 56)
    for slot in upcoming:
        icon = STATUS_ICONS.get(slot["status"], "?")
        ch_name = channels.get(slot["channel_id"], {}).get("name", slot["channel_id"])
        title = slot.get("title") or "(empty)"
        # Parse and format the time nicely
        try:
            dt = datetime.fromisoformat(slot["scheduled_time"])
            date_str = dt.strftime("%a %b %d, %I:%M %p")
        except (ValueError, TypeError):
            date_str = slot["scheduled_time"]

        print(f"  {icon} {date_str:<26} {ch_name}")
        if slot["status"] != "placeholder":
            print(f"       -> {title}")
            if slot.get("youtube_url"):
                print(f"          {slot['youtube_url']}")

    # Status counts
    counts = {}
    for s in slots:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    parts = [f"{STATUS_ICONS.get(k, '?')} {v} {k}" for k, v in counts.items()]
    print("-" * 56)
    print("  " + "  |  ".join(parts))
    print()


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.publishing.calendar_manager",
        description="script2vid Release Calendar",
    )
    sub = parser.add_subparsers(dest="command")

    # ── add-channel ──
    add = sub.add_parser("add-channel", help="Add or update a channel schedule.")
    add.add_argument("channel_id", help="Short slug (e.g. 'deep_thoughts').")
    add.add_argument("--name", required=True, help="Display name.")
    add.add_argument("--days", required=True,
                     help="Comma-separated days (e.g. 'mon,wed,fri').")
    add.add_argument("--time", required=True, dest="time_str",
                     help="Publish time in HH:MM (e.g. '14:00').")
    add.add_argument("--tz", default="America/New_York",
                     help="Timezone (default: America/New_York).")
    add.add_argument("--category", default="people",
                     help="YouTube category (e.g. 'education').")
    add.add_argument("--tags", default="",
                     help="Comma-separated default tags.")

    # ── remove-channel ──
    rm = sub.add_parser("remove-channel", help="Remove a channel and its slots.")
    rm.add_argument("channel_id")

    # ── generate ──
    gen = sub.add_parser("generate",
                         help="Generate placeholder slots for the next N weeks.")
    gen.add_argument("--channel", default=None, dest="channel_id",
                     help="Only generate for this channel.")
    gen.add_argument("--weeks", type=int, default=4,
                     help="Number of weeks ahead (default: 4).")

    # ── status ──
    sub.add_parser("status", help="Print the upcoming schedule.")

    # ── assign ──
    asgn = sub.add_parser("assign", help="Assign a video to a slot.")
    asgn.add_argument("slot_id", help="Slot ID (from 'status' output).")
    asgn.add_argument("video_path", help="Path to .mp4 file.")
    asgn.add_argument("--title", default=None)
    asgn.add_argument("--description", default=None)
    asgn.add_argument("--tags", default=None,
                      help="Comma-separated tags.")

    # ── publish-due ──
    pub = sub.add_parser("publish-due",
                         help="Upload + schedule all videos due soon.")
    pub.add_argument("--hours", type=int, default=48,
                     help="Look-ahead window in hours (default: 48).")

    # ── view ──
    sub.add_parser("view", help="Open the web calendar GUI.")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "add-channel":
        days = [d.strip() for d in args.days.split(",")]
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        add_channel(
            channel_id=args.channel_id,
            name=args.name,
            days=days,
            time_str=args.time_str,
            timezone=args.tz,
            category=args.category,
            tags=tags,
        )

    elif args.command == "remove-channel":
        remove_channel(args.channel_id)

    elif args.command == "generate":
        generate_slots(channel_id=args.channel_id, weeks=args.weeks)

    elif args.command == "status":
        print_status()

    elif args.command == "assign":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        assign_to_slot(
            slot_id=args.slot_id,
            video_path=args.video_path,
            title=args.title,
            description=args.description,
            tags=tags,
        )

    elif args.command == "publish-due":
        publish_due(hours_ahead=args.hours)

    elif args.command == "view":
        from src.web.calendar_server import start_server
        start_server()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
