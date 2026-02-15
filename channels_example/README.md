# Channels Directory Structure

Copy this `channels_example/` folder to `channels/` and customize it for your
own YouTube channels.

```
channels/
  your_channel_name/
    default_settings.json   ← pipeline defaults for this channel
    workspace/              ← auto-created per-video project folders
      video_project_01/
        audio/  clips/  output/  credits/  overlays/
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

4. **Register the channel in the calendar:**
   ```
   python -m src.calendar_manager add-channel your_channel \
       --name "Your Channel Name" \
       --days mon,wed,fri --time 14:00 \
       --tz America/New_York \
       --category education \
       --tags "tag1,tag2,tag3"
   ```

5. **Generate placeholder slots:**
   ```
   python -m src.calendar_manager generate --weeks 4
   ```

6. **Create a video with the channel switch:**
   ```
   python -m src.main scripts/your_script.txt --channel your_channel \
       --title "Your Video Title" --description "Your description"
   ```
   The pipeline reads `default_settings.json`, renders the video, assigns it
   to the next open calendar slot, uploads to YouTube, and schedules the release
   automatically.

## Notes

- CLI flags always override `default_settings.json` — pass `--vertical` or
  `--quality draft` to override for a single run without changing the file.
- The `workspace/` subfolder is auto-created when you run the pipeline. You
  don't need to create it manually.
- Each channel's calendar schedule (days, time, timezone, category, tags) is
  stored in `calendar_data.json` at the project root, managed via the
  `calendar_manager` CLI.
