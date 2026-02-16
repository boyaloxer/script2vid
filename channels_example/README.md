# Channels Directory Structure

Copy this `channels_example/` folder to `channels/` and customize it for your
own YouTube channels.

```
channels/
  your_channel_name/
    default_settings.json   ← pipeline defaults for this channel
    content_prompt.md       ← LLM prompt for generating scripts, titles, descriptions
    youtube_token.json      ← OAuth token for this channel's YouTube account
    workspace/              ← auto-created per-video project folders
      video_project_01/
        audio/  clips/  output/  credits/  overlays/  thumbnails/
```

## Setup

1. **Copy the example:**
   ```
   cp -r channels_example channels
   ```

2. **Rename `example_channel/`** to your channel's slug (e.g. `deep_thoughts`).

3. **Edit `default_settings.json`** to set your preferred pipeline defaults:

   | Setting    | Type    | Description                                      |
   |------------|---------|--------------------------------------------------|
   | `vertical` | boolean | `true` for 9:16 Shorts/Reels, `false` for 16:9   |
   | `captions` | boolean | Burn closed captions into the video               |
   | `overlays` | boolean | Enable text overlays for quotes/stats             |
   | `quality`  | string  | `"final"` for production, `"draft"` for fast test |
   | `fresh`    | boolean | `true` to ignore cached stages and re-run all     |

4. **Edit `content_prompt.md`** to define your channel's creative voice:

   This file is the "character sheet" for your channel — it tells an LLM how to
   generate scripts, titles, and descriptions that match your channel's style.
   Follow the template sections:

   | Section             | Purpose                                              |
   |---------------------|------------------------------------------------------|
   | Channel Identity    | Name, niche, audience, platform                      |
   | Content Format      | Duration, word count, structure                       |
   | Voice & Tone        | Perspective, style, do's and don'ts                   |
   | Script Structure    | How the narrative builds (hook, body, turn, landing)  |
   | Title Guidelines    | Length, style, what to avoid                          |
   | Description Guidelines | Length, hashtag conventions                        |
   | Examples            | 2-3 complete examples (script + title + description)  |

   The more examples you include, the more consistent the LLM output will be.

5. **Register the channel in the calendar:**
   ```
   # Single daily release
   python -m src.publishing.calendar_manager add-channel your_channel \
       --name "Your Channel Name" \
       --days mon,wed,fri --time 14:00 \
       --tz America/New_York \
       --category education \
       --tags "tag1,tag2,tag3"

   # Multiple daily releases (comma-separated times)
   python -m src.publishing.calendar_manager add-channel your_channel \
       --name "Your Channel Name" \
       --days mon,tue,wed,thu,fri,sat,sun --time "12:00,20:00" \
       --tz America/New_York \
       --category education \
       --tags "tag1,tag2,tag3"
   ```

6. **Generate placeholder slots:**
   ```
   python -m src.publishing.calendar_manager generate --weeks 4
   ```

7. **Create a video with the channel switch:**
   ```
   python -m src.main scripts/your_script.txt --channel your_channel \
       --title "Your Video Title" --description "Your description"
   ```
   The pipeline reads `default_settings.json`, renders the video, assigns it
   to the next open calendar slot, uploads to YouTube, and schedules the release
   automatically.

## YouTube Authentication (Per-Channel)

Each channel stores its own `youtube_token.json` so you can upload to different
YouTube accounts. On first publish for a channel, a browser window opens —
**sign into the Google account that owns that YouTube channel**.

The token is saved at `channels/<channel_id>/youtube_token.json` and reused
automatically for future uploads. If the token expires, the browser flow will
re-open.

All channels share the same `client_secrets.json` at the project root (your
Google Cloud OAuth app), but each channel authenticates to its own YouTube account.

## Notes

- CLI flags always override `default_settings.json` — pass `--vertical` or
  `--quality draft` to override for a single run without changing the file.
- The `workspace/` subfolder is auto-created when you run the pipeline. You
  don't need to create it manually.
- Each channel's calendar schedule (days, time, timezone, category, tags) is
  stored in `calendar_data.json` at the project root, managed via the
  `calendar_manager` CLI.
