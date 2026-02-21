"""
Agent Runner — Autonomous think-act-observe loop for video production.

Unlike a simple pipeline runner, the agent:
  - Observes the world (APIs, calendar, metrics, pipeline progress)
  - Thinks (asks the LLM brain what to do next)
  - Acts (generates scripts, runs pipelines, uploads)
  - Reflects (reviews scripts before committing, logs outcomes)
  - Loops until all work is done or it decides to stop

Usage:
    # Fill empty slots for a channel (agent decides how many):
    python -m src.agent.runner --channel deep_thoughts

    # Fill up to 5 slots:
    python -m src.agent.runner --channel deep_thoughts --count 5

    # All channels:
    python -m src.agent.runner --all --count 3

    # Dry run — think and generate but don't run pipeline:
    python -m src.agent.runner --channel deep_thoughts --dry-run

    # Daemon mode — run continuously, checking every N minutes:
    python -m src.agent.runner --all --daemon --interval 60
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Force UTF-8 stdout on Windows so LLM-generated unicode doesn't crash prints
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.config import CHANNELS_DIR
from src.agent.observer import build_world_state, world_state_to_text
from src.agent.brain import decide_next_action, review_script
from src.agent.activity_feed import emit as _emit
from src.agent.script_generator import generate_script, _load_content_prompt, _load_past_scripts
from src.agent.analytics import build_metrics_summary, save_metrics_snapshot
from src.agent.strategist import build_strategy, peek_next_topic as _peek_next_topic, consume_topic as _consume_topic
from src.agent.journal import record_video_produced
from src.agent.dataset_builder import record_decision_point, record_generation, update_generation_video_id
from src.publishing.calendar_manager import load_calendar, get_upcoming, auto_assign, update_slot


def _script_slug(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s-]+", "_", slug).strip("_")
    return slug[:80]


def _action_generate(channel_id: str, dry_run: bool, quality: str, session_log: list[str]) -> bool:
    """Execute the generate_and_publish action. Returns True if a slot was filled."""

    # Step 1: Pull metrics for feedback
    session_log.append(f"[{channel_id}] Pulling metrics for feedback loop...")
    try:
        metrics = build_metrics_summary(channel_id)
    except Exception as e:
        session_log.append(f"[{channel_id}] Metrics unavailable: {e}")
        metrics = None

    # Step 1.5: Check if there's a content plan to follow
    # DON'T mark as used yet — only mark after successful pipeline
    topic_directive = None
    next_topic = _peek_next_topic(channel_id)
    if next_topic:
        topic_directive = (
            f"TOPIC DIRECTIVE (from content strategy):\n"
            f"  Topic: {next_topic.get('topic', '')}\n"
            f"  Angle: {next_topic.get('angle', '')}\n"
            f"  Visual notes: {next_topic.get('visual_notes', '')}\n"
            f"Follow this directive closely."
        )
        session_log.append(f"[{channel_id}] Following strategy topic: {next_topic.get('topic')}")
        print(f"  [Agent] Following strategy: {next_topic.get('topic')}")
    else:
        session_log.append(f"[{channel_id}] No content plan — generating freely.")

    # Step 1.75: Check for active experiments
    experiment_assignment = None
    try:
        from src.agent.experiment_engine import get_experiment_instruction
        experiment_assignment = get_experiment_instruction(channel_id)
        if experiment_assignment:
            exp_instruction = experiment_assignment["instruction"]
            if topic_directive:
                topic_directive += f"\n\nEXPERIMENT INSTRUCTION: {exp_instruction}"
            else:
                topic_directive = f"EXPERIMENT INSTRUCTION: {exp_instruction}"
            session_log.append(
                f"[{channel_id}] Experiment: \"{experiment_assignment['hypothesis']}\" "
                f"arm={experiment_assignment['arm']}"
            )
            print(f"  [Agent] Experiment arm: {experiment_assignment['arm']} ({experiment_assignment['variable']})")
    except Exception:
        pass

    # Step 2: Generate script
    session_log.append(f"[{channel_id}] Generating script via LLM...")
    start = time.time()
    try:
        result = generate_script(
            channel_id,
            metrics_summary=metrics,
            topic_directive=topic_directive,
        )
    except Exception as e:
        session_log.append(f"[{channel_id}] Script generation FAILED: {e}")
        return False

    title = result["title"]
    script = result["script"]
    description = result["description"]
    reasoning = result.get("topic_reasoning", "")
    elapsed = time.time() - start

    session_log.append(
        f"[{channel_id}] Generated: \"{title}\" "
        f"({len(script)} chars, {elapsed:.1f}s) — {reasoning}"
    )
    print(f"\n  [Agent] Generated: \"{title}\"")
    print(f"  [Agent] Reasoning: {reasoning}")
    _emit("result", f"Script generated: \"{title}\" ({len(script)} chars, {elapsed:.1f}s)", channel_id=channel_id)

    # Step 3: Multi-perspective critic review
    # Three separate reviewers assess the script from different angles.
    # Only FATAL issues trigger auto-reject. Everything else is advisory —
    # the brain (not the critics) makes the final call.
    score = None
    approved = None
    issues = None
    critic_report = None
    session_log.append(f"[{channel_id}] Running multi-perspective review...")
    try:
        from src.agent.critic import run_critics, critic_report_to_text

        content_prompt = _load_content_prompt(channel_id)
        past_titles = _load_past_scripts(channel_id)
        critic_report = run_critics(script, title, description, content_prompt, past_titles)

        report_text = critic_report_to_text(critic_report)
        session_log.append(f"[{channel_id}] {report_text}")
        print(f"\n  [Agent] {report_text}")

        # Derive score from critic perspectives for backward compatibility
        # (dataset builder, journal, etc. still expect a numeric score)
        vs = critic_report["perspectives"].get("viewer_simulator", {})
        sa = critic_report["perspectives"].get("style_auditor", {})
        tap = vs.get("tap_probability", 0.5) or 0.5
        watch = vs.get("watch_through_rate", 0.5) or 0.5
        compliance = sa.get("compliance_score", 0.7) or 0.7
        score = round((tap * 3 + watch * 4 + compliance * 3))  # weighted 0-10
        approved = not critic_report["auto_reject"]
        issues = [i["detail"] for i in critic_report.get("fatal_issues", []) + critic_report.get("concerns", [])]

        # Only auto-reject on FATAL issues (hard rule violations)
        if critic_report["auto_reject"]:
            session_log.append(
                f"[{channel_id}] FATAL issues found — regenerating."
            )
            print(f"  [Agent] Fatal issues found — regenerating...")
            result = generate_script(channel_id, metrics_summary=metrics)
            title = result["title"]
            script = result["script"]
            description = result["description"]
            session_log.append(f"[{channel_id}] Retry generated: \"{title}\"")
            print(f"  [Agent] Retry: \"{title}\"")

        # Accept title revisions from the critic
        if critic_report.get("revised_title"):
            session_log.append(
                f"[{channel_id}] Title improved: "
                f"\"{title}\" -> \"{critic_report['revised_title']}\""
            )
            title = critic_report["revised_title"]

    except Exception as e:
        session_log.append(f"[{channel_id}] Critic review failed (non-fatal): {e}")
        # Fall back to the old single self-review if critics fail
        try:
            content_prompt = _load_content_prompt(channel_id)
            past_titles = _load_past_scripts(channel_id)
            review = review_script(script, title, description, content_prompt, past_titles)
            score = review.get("score", 0)
            approved = review.get("approved", False)
            issues = review.get("issues", [])
            session_log.append(f"[{channel_id}] Fallback review: score={score}/10")
        except Exception:
            pass

    # Capture generation for training dataset (before pipeline — captures all attempts)
    try:
        record_generation(
            channel_id=channel_id,
            content_prompt=_load_content_prompt(channel_id),
            past_titles=_load_past_scripts(channel_id),
            metrics_summary=metrics,
            topic_directive=topic_directive,
            generated_script=script,
            generated_title=title,
            generated_description=description,
            topic_reasoning=result.get("topic_reasoning", ""),
            review_score=score,
            review_approved=approved,
            review_issues=issues,
            critic_report=critic_report,
        )
    except Exception:
        pass  # dataset capture is best-effort

    if dry_run:
        print(f"\n  [Agent] DRY RUN — script generated but not produced.")
        print(f"\n--- Script ---\n{script}")
        print(f"\n--- Title: {title}")
        print(f"--- Description: {description}")
        session_log.append(f"[{channel_id}] DRY RUN complete for \"{title}\"")
        # Mark topic as used only on success (dry run counts as success)
        if next_topic:
            _consume_topic(channel_id, next_topic.get("topic", ""))
        return True

    # Pre-flight: check YouTube quota before committing to a full pipeline
    try:
        from src.utils.quota_tracker import can_upload_youtube, get_youtube_usage
        yt_usage = get_youtube_usage()
        if not can_upload_youtube():
            session_log.append(
                f"[{channel_id}] YouTube API quota exhausted "
                f"({yt_usage['units_used']}/10,000 units). Cannot upload."
            )
            print(f"  [Agent] YouTube quota exhausted — cannot upload today.")
            return False
    except Exception:
        pass  # quota check failure shouldn't block the pipeline

    # Step 4: Save script and run pipeline
    slug = _script_slug(title)
    project_name = f"{channel_id}_{slug}"
    script_path = Path("scripts") / f"{project_name}.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    session_log.append(f"[{channel_id}] Saved script to {script_path}")

    print(f"\n  [Agent] Running pipeline for \"{title}\"...")
    session_log.append(f"[{channel_id}] Starting pipeline...")
    _emit("act", f"Running pipeline for \"{title}\"...", channel_id=channel_id)

    defaults_path = CHANNELS_DIR / channel_id / "default_settings.json"
    defaults = {}
    if defaults_path.exists():
        try:
            defaults = json.loads(defaults_path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            defaults = {}

    try:
        from src.main import run_pipeline
        import src.config as _cfg

        is_vertical = defaults.get("vertical", False)
        use_captions = defaults.get("captions", False)
        use_overlays = defaults.get("overlays", False)

        if is_vertical:
            _cfg.OUTPUT_WIDTH = 1080
            _cfg.OUTPUT_HEIGHT = 1920

        output_path = run_pipeline(
            script_text=script,
            project_name=project_name,
            quality=quality,
            overlays=use_overlays,
            captions=use_captions,
            vertical=is_vertical,
            channel=channel_id,
        )
        session_log.append(f"[{channel_id}] Pipeline complete: {output_path}")

    except Exception as e:
        session_log.append(f"[{channel_id}] Pipeline FAILED at {type(e).__name__}: {e}")
        print(f"  [Agent] Pipeline failed: {e}")
        return False  # topic NOT consumed — can be retried

    # Step 5: Publish — only assign calendar slot AFTER pipeline succeeds
    session_log.append(f"[{channel_id}] Publishing...")
    try:
        is_vertical = defaults.get("vertical", False)
        yt_tags = (
            [t.strip() for t in defaults.get("tags", "").split(",")]
            if defaults.get("tags") else []
        )

        assigned_slot = auto_assign(
            channel_id=channel_id,
            video_path=str(output_path),
            title=title,
            description=description,
            tags=yt_tags or None,
            workspace=str(output_path.parent.parent),
            is_vertical=is_vertical,
        )

        should_publish = defaults.get("publish", False)
        if should_publish:
            from src.publishing.publisher import upload_to_youtube

            yt_result = upload_to_youtube(
                video_path=output_path,
                title=title,
                description=description,
                tags=yt_tags,
                category=defaults.get("category", "people"),
                privacy="public",
                is_short=is_vertical,
                contains_synthetic_media=False,
                channel_id=channel_id,
            )

            if assigned_slot:
                update_slot(
                    assigned_slot["id"],
                    status="uploaded",
                    youtube_video_id=yt_result["video_id"],
                    youtube_url=yt_result["url"],
                )

            project_dir = output_path.parent.parent
            (project_dir / "6_published.json").write_text(json.dumps({
                "slot_id": assigned_slot["id"] if assigned_slot else None,
                "published_immediately": True,
                "youtube_video_id": yt_result["video_id"],
                "youtube_url": yt_result["url"],
                "title": title,
                "output_path": str(output_path),
            }, indent=2), encoding="utf-8")

            session_log.append(
                f"[{channel_id}] Published LIVE: {yt_result['url']}"
            )
            print(f"  [Agent] Published LIVE: {yt_result['url']}")
            _emit("result", f"Published LIVE: {yt_result['url']}", channel_id=channel_id)

            try:
                update_generation_video_id(title, yt_result["video_id"])
            except Exception:
                pass

            try:
                from src.agent.memory import record_episode
                exp_note = f", experiment={experiment_assignment['arm']}" if experiment_assignment else ""
                record_episode(
                    channel_id,
                    f"Published LIVE \"{title}\" ({yt_result['video_id']}){exp_note}",
                    significance="normal",
                )
            except Exception:
                pass
        else:
            session_log.append(f"[{channel_id}] Rendered but auto-publish disabled.")

    except Exception as e:
        session_log.append(f"[{channel_id}] Publishing FAILED: {e}")
        print(f"  [Agent] Publish failed: {e}")
        # Pipeline succeeded but publish failed — still consume topic
        # (the video exists, we just couldn't upload it)

    # Mark topic as used only after pipeline success
    if next_topic:
        _consume_topic(channel_id, next_topic.get("topic", ""))

    # Record in performance journal for long-term learning
    try:
        strategy_path = CHANNELS_DIR / channel_id / "content_strategy.json"
        strategy_analysis = None
        if strategy_path.exists():
            strat = json.loads(strategy_path.read_text(encoding="utf-8"))
            strategy_analysis = strat.get("analysis")

        video_id = None
        scheduled_time = None
        try:
            pub_path = output_path.parent.parent / "6_published.json"
            if pub_path.exists():
                pub = json.loads(pub_path.read_text(encoding="utf-8"))
                video_id = pub.get("youtube_video_id")
                scheduled_time = pub.get("scheduled_time")
        except Exception:
            pass

        record_video_produced(
            channel_id=channel_id,
            title=title,
            video_id=video_id,
            strategy_topic=next_topic.get("topic") if next_topic else None,
            strategy_analysis=strategy_analysis,
            review_score=score,
            scheduled_time=scheduled_time,
        )
    except Exception as e:
        session_log.append(f"[{channel_id}] Journal write failed (non-fatal): {e}")

    # Record experiment assignment if this video was part of an experiment
    if experiment_assignment:
        try:
            from src.agent.experiment_engine import record_video_assignment
            vid = None
            try:
                pub_path = output_path.parent.parent / "6_published.json"
                if pub_path.exists():
                    vid = json.loads(pub_path.read_text(encoding="utf-8")).get("youtube_video_id")
            except Exception:
                pass
            record_video_assignment(
                channel_id=channel_id,
                experiment_id=experiment_assignment["experiment_id"],
                arm=experiment_assignment["arm"],
                video_id=vid,
                title=title,
            )
        except Exception:
            pass

    return True


def _action_analyze(channel_id: str, session_log: list[str]):
    """Pull and analyze metrics for a channel."""
    try:
        save_metrics_snapshot(channel_id)
        summary = build_metrics_summary(channel_id)
        if summary:
            session_log.append(f"[{channel_id}] Metrics analyzed:\n{summary}")
            print(f"\n  [Agent] Metrics for {channel_id}:\n{summary}")
        else:
            session_log.append(f"[{channel_id}] No metrics available yet.")
    except Exception as e:
        session_log.append(f"[{channel_id}] Metrics analysis failed: {e}")


def _action_plan_strategy(channel_id: str, session_log: list[str]):
    """Build a content strategy for a channel."""
    session_log.append(f"[{channel_id}] Building content strategy...")
    try:
        metrics = None
        try:
            metrics = build_metrics_summary(channel_id)
        except Exception:
            pass

        strategy = build_strategy(channel_id, metrics_summary=metrics)
        analysis = strategy.get("analysis", "N/A")
        plan_count = len(strategy.get("content_plan", []))
        session_log.append(
            f"[{channel_id}] Strategy complete: {analysis} "
            f"({plan_count} topics planned)"
        )
    except Exception as e:
        session_log.append(f"[{channel_id}] Strategy planning FAILED: {e}")
        print(f"  [Agent] Strategy failed: {e}")


def _action_scout_trends(channel_id: str, session_log: list[str]):
    """Discover rising topics in the channel's niche."""
    session_log.append(f"[{channel_id}] Scouting trends...")
    try:
        metrics = None
        try:
            metrics = build_metrics_summary(channel_id)
        except Exception:
            pass

        from src.agent.trend_scout import scout_trends
        analysis = scout_trends(channel_id, metrics_summary=metrics)

        themes = analysis.get("rising_themes", [])
        angles = analysis.get("content_angles", [])
        mood = analysis.get("audience_mood", "")
        session_log.append(
            f"[{channel_id}] Trend scout complete: "
            f"{len(themes)} rising themes, {len(angles)} content angles. "
            f"Audience mood: {mood}. "
            f"Overview: {analysis.get('analysis', 'N/A')}"
        )
        print(f"  [Agent] Trends scouted: {len(themes)} rising themes, {len(angles)} content angles")
    except Exception as e:
        session_log.append(f"[{channel_id}] Trend scouting FAILED: {e}")
        print(f"  [Agent] Trend scouting failed: {e}")


def _action_engage_community(channel_id: str, session_log: list[str]):
    """Reply to unreplied YouTube comments in the channel's voice."""
    session_log.append(f"[{channel_id}] Engaging with community...")
    try:
        from src.agent.community import engage_community
        result = engage_community(channel_id)

        found = result.get("comments_found", 0)
        posted = result.get("replies_posted", 0)
        session_log.append(
            f"[{channel_id}] Community engagement: {found} comments found, "
            f"{posted} replies posted"
        )
        if result.get("replies"):
            for r in result["replies"][:3]:
                session_log.append(
                    f"[{channel_id}]   @{r['author']}: \"{r['comment']}\" -> \"{r['reply']}\""
                )
        print(f"  [Agent] Replied to {posted} comments")
    except Exception as e:
        session_log.append(f"[{channel_id}] Community engagement FAILED: {e}")
        print(f"  [Agent] Community engagement failed: {e}")


def _action_optimize_published(channel_id: str, session_log: list[str]):
    """Check recently published videos and optimize underperformers."""
    session_log.append(f"[{channel_id}] Checking recently published videos for optimization...")
    try:
        from src.agent.optimizer import evaluate_and_optimize
        actions = evaluate_and_optimize(channel_id)

        if actions:
            for a in actions:
                session_log.append(
                    f"[{channel_id}] Optimized \"{a.get('original_title', '?')}\" -> "
                    f"\"{a.get('new_title', a.get('original_title', '?'))}\" "
                    f"({a.get('views_at_check', 0)} views at {a.get('hours_live', '?')}h)"
                )
            print(f"  [Agent] Optimized {len(actions)} video(s)")
        else:
            session_log.append(f"[{channel_id}] No optimizations needed.")
            print(f"  [Agent] No optimizations needed")
    except Exception as e:
        session_log.append(f"[{channel_id}] Post-publish optimization FAILED: {e}")
        print(f"  [Agent] Optimization failed: {e}")


def _action_analyze_schedule(channel_id: str, session_log: list[str]):
    """Analyze posting schedule performance and recommend optimal times."""
    session_log.append(f"[{channel_id}] Analyzing posting schedule...")
    try:
        from src.agent.scheduler import analyze_schedule, apply_schedule_change
        analysis = analyze_schedule(channel_id)

        summary = analysis.get("analysis", "N/A")
        session_log.append(f"[{channel_id}] Schedule analysis: {summary}")

        if analysis.get("schedule_change_recommended"):
            rec = analysis.get("recommended_cadence", {})
            session_log.append(
                f"[{channel_id}] Schedule change recommended: "
                f"{rec.get('days', [])} at {rec.get('times', [])}"
            )
            applied = apply_schedule_change(channel_id)
            if applied:
                session_log.append(f"[{channel_id}] Schedule updated!")
                print(f"  [Agent] Schedule updated to optimal times")
            else:
                session_log.append(f"[{channel_id}] Schedule change not applied.")
        else:
            session_log.append(f"[{channel_id}] Current schedule is adequate.")
            print(f"  [Agent] Current schedule is fine")
    except Exception as e:
        session_log.append(f"[{channel_id}] Schedule analysis FAILED: {e}")
        print(f"  [Agent] Schedule analysis failed: {e}")


def _action_analyze_audience(channel_id: str, session_log: list[str]):
    """Analyze YouTube comments for audience feedback."""
    session_log.append(f"[{channel_id}] Analyzing audience comments...")
    try:
        from src.agent.audience import analyze_audience
        analysis = analyze_audience(channel_id)

        requests = analysis.get("requests", [])
        sentiment = analysis.get("sentiment", "unknown")
        session_log.append(
            f"[{channel_id}] Audience analysis complete: "
            f"sentiment={sentiment}, {len(requests)} topic requests. "
            f"Overview: {analysis.get('analysis', 'N/A')}"
        )
        print(f"  [Agent] Audience sentiment: {sentiment}, {len(requests)} requests")
    except Exception as e:
        session_log.append(f"[{channel_id}] Audience analysis FAILED: {e}")
        print(f"  [Agent] Audience analysis failed: {e}")


def _action_propose_experiments(channel_id: str, session_log: list[str]):
    """Propose A/B experiments for a channel."""
    session_log.append(f"[{channel_id}] Proposing experiments...")
    try:
        metrics = None
        try:
            metrics = build_metrics_summary(channel_id)
        except Exception:
            pass

        from src.agent.experiment_engine import propose_experiments_via_llm
        new_exps = propose_experiments_via_llm(channel_id, metrics_summary=metrics)
        if new_exps:
            for exp in new_exps:
                session_log.append(
                    f"[{channel_id}] New experiment: \"{exp['hypothesis']}\" "
                    f"({exp['variable']})"
                )
            print(f"  [Agent] Proposed {len(new_exps)} experiment(s)")
        else:
            session_log.append(f"[{channel_id}] No new experiments proposed.")
    except Exception as e:
        session_log.append(f"[{channel_id}] Experiment proposal FAILED: {e}")
        print(f"  [Agent] Experiment proposal failed: {e}")


def run_agent_loop(
    channels: list[str],
    count: int = 1,
    dry_run: bool = False,
    quality: str = "final",
    continuous: bool = False,
):
    """
    The main agent loop: observe → think → act → repeat.
    """
    session_log: list[str] = []
    slots_filled = 0

    print(f"\n{'#' * 60}")
    print(f"# Agent starting — channels: {', '.join(channels)}")
    print(f"# Target: {count} slot(s) per channel")
    print(f"{'#' * 60}")
    _emit("info", f"Agent session started — channels: {', '.join(channels)}, target: {count} slots each")

    max_iterations = count * len(channels) * 5  # safety cap (strategy + analyze + generate per slot)
    iteration = 0

    while continuous or iteration < max_iterations:
        iteration += 1
        print(f"\n{'-' * 60}")
        print(f"  Think-Act cycle #{iteration}")
        print(f"{'-' * 60}")

        # OBSERVE
        print("  [Agent] Observing world state...")
        _emit("observe", f"Cycle #{iteration} — scanning world state...")
        state = build_world_state(channel_filter=channels)
        state_text = world_state_to_text(state)

        # Check for hard blockers before asking the brain
        all_apis_ok = all(
            info.get("status") == "ok"
            for info in state["apis"].values()
        )
        if not all_apis_ok:
            down = [
                name for name, info in state["apis"].items()
                if info.get("status") != "ok"
            ]
            print(f"  [Agent] API issues detected: {down}")
            session_log.append(f"API health check: issues with {down}")

        # THINK
        print("  [Agent] Thinking...")
        try:
            decision = decide_next_action(
                world_state_text=state_text,
                session_log=session_log,
                slots_filled=slots_filled,
                slots_target=count * len(channels),
                dry_run=dry_run,
                channel_ids=channels,
            )
        except Exception as e:
            session_log.append(f"Brain FAILED: {e}")
            print(f"  [Agent] Brain error: {e}")
            print(f"  [Agent] Waiting 2m before retrying...")
            _emit("error", f"Brain error: {e} — retrying in 2m")
            time.sleep(120)
            continue

        action = decision.get("action", "stop")
        thinking = decision.get("thinking", "")
        params = decision.get("parameters", {}) if isinstance(decision.get("parameters"), dict) else {}

        print(f"  [Agent] Thinking: {thinking}")
        print(f"  [Agent] Action: {action} {params}")
        session_log.append(f"Brain decided: {action} — {thinking}")
        ch_hint = params.get("channel_id", channels[0] if channels else None)
        _emit("think", thinking, channel_id=ch_hint, action=action)

        # ACT
        outcome_success = None
        if action == "generate_and_publish":
            ch = params.get("channel_id", channels[0])
            if ch not in channels:
                session_log.append(f"Brain chose invalid channel '{ch}', skipping.")
                continue
            success = _action_generate(ch, dry_run, quality, session_log)
            outcome_success = success
            if success:
                slots_filled += 1
                print(f"\n  [Agent] Progress: {slots_filled}/{count * len(channels)} slots filled")
                _emit("result", f"Slot filled ({slots_filled}/{count * len(channels)})", channel_id=ch)
            else:
                _emit("error", "generate_and_publish failed", channel_id=ch)

        elif action == "analyze_metrics":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Analyzing metrics...", channel_id=ch)
            _action_analyze(ch, session_log)
            outcome_success = True

        elif action == "plan_strategy":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Planning content strategy...", channel_id=ch)
            _action_plan_strategy(ch, session_log)
            outcome_success = True

        elif action == "propose_experiments":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Proposing A/B experiments...", channel_id=ch)
            _action_propose_experiments(ch, session_log)
            outcome_success = True

        elif action == "scout_trends":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Scouting rising topics...", channel_id=ch)
            _action_scout_trends(ch, session_log)
            outcome_success = True

        elif action == "analyze_audience":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Analyzing audience comments...", channel_id=ch)
            _action_analyze_audience(ch, session_log)
            outcome_success = True

        elif action == "optimize_published":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Optimizing recently published videos...", channel_id=ch)
            _action_optimize_published(ch, session_log)
            outcome_success = True

        elif action == "analyze_schedule":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Analyzing posting schedule...", channel_id=ch)
            _action_analyze_schedule(ch, session_log)
            outcome_success = True

        elif action == "engage_community":
            ch = params.get("channel_id", channels[0])
            _emit("act", "Engaging with community comments...", channel_id=ch)
            _action_engage_community(ch, session_log)
            outcome_success = True

        elif action == "execute_command":
            cmd_id = params.get("command_id")
            interpretation = params.get("interpretation", "")
            ch = params.get("channel_id", channels[0])
            _emit("act", f"Executing user command #{cmd_id}: {interpretation}", channel_id=ch)
            session_log.append(f"Executing user command #{cmd_id}: {interpretation}")
            if cmd_id:
                try:
                    from src.agent.command_queue import mark_done
                    mark_done(cmd_id, result=interpretation)
                except Exception:
                    pass
            outcome_success = True

        elif action == "wait":
            wait_min = params.get("wait_minutes", 5)
            reason = params.get("reason", "no reason given")
            print(f"  [Agent] Waiting {wait_min}m — {reason}")
            session_log.append(f"Waiting {wait_min}m: {reason}")
            _emit("info", f"Idle — {reason} (checking back in {wait_min}m)")
            time.sleep(wait_min * 60)
            outcome_success = True

        elif action == "stop":
            reason = params.get("reason", "work complete")
            wait_min = params.get("check_back_minutes", 30)
            print(f"\n  [Agent] Going idle: {reason}")
            session_log.append(f"Idle: {reason}")
            _emit("info", f"Going idle: {reason}")
            try:
                record_decision_point(
                    world_state_text=state_text,
                    session_log=session_log,
                    slots_filled=slots_filled,
                    slots_target=count * len(channels),
                    dry_run=dry_run,
                    decision=decision,
                    outcome_success=True,
                )
            except Exception:
                pass
            if continuous:
                print(f"  [Agent] Resting {wait_min}m then resuming...")
                _emit("info", f"Resting {wait_min}m before next check...")
                time.sleep(wait_min * 60)
                session_log.clear()
                slots_filled = 0
                iteration = 0
                continue
            break

        else:
            session_log.append(f"Unknown action '{action}', stopping.")
            break

        # Record every decision point for training data
        try:
            record_decision_point(
                world_state_text=state_text,
                session_log=session_log,
                slots_filled=slots_filled,
                slots_target=count * len(channels),
                dry_run=dry_run,
                decision=decision,
                outcome_success=outcome_success,
            )
        except Exception:
            pass  # dataset capture is best-effort

    # Session summary
    print(f"\n{'=' * 60}")
    print(f"[Agent] Session complete.")
    print(f"[Agent] Slots filled: {slots_filled}")
    print(f"[Agent] Iterations: {iteration}")
    print(f"{'=' * 60}")
    _emit("info", f"Session complete — {slots_filled} slots filled in {iteration} iterations")

    # End-of-session reflection — update persistent memory
    print("  [Agent] Reflecting on session...")
    try:
        from src.agent.memory import reflect_on_session
        final_state = build_world_state(channel_filter=channels)
        final_state_text = world_state_to_text(final_state)
        for ch in channels:
            reflect_on_session(
                channel_id=ch,
                session_log=session_log,
                slots_filled=slots_filled,
                world_state_text=final_state_text,
            )
        print("  [Agent] Memory updated.")
    except Exception as e:
        print(f"  [Agent] Reflection failed (non-fatal): {e}")

    # Persist session log to disk
    _save_session_log(session_log, slots_filled, iteration, channels)

    return {
        "slots_filled": slots_filled,
        "iterations": iteration,
        "log": session_log,
    }


def _save_session_log(
    session_log: list[str], slots_filled: int, iterations: int, channels: list[str],
):
    """Write session log to channels/agent_sessions/ for debugging and audit."""
    from datetime import datetime as _dt
    log_dir = CHANNELS_DIR / "agent_sessions"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"session_{timestamp}.json"

    try:
        log_path.write_text(json.dumps({
            "timestamp": _dt.now().isoformat(),
            "channels": channels,
            "slots_filled": slots_filled,
            "iterations": iterations,
            "log": session_log,
        }, indent=2), encoding="utf-8")
        print(f"[Agent] Session log saved: {log_path}")
    except OSError as e:
        print(f"[Agent] WARNING: Could not save session log: {e}")


def run_daemon(
    channels: list[str],
    count: int = 1,
    quality: str = "final",
    interval_minutes: int = 60,
):
    """
    Persistent daemon mode — runs the agent loop on a schedule.
    Checks for empty slots every interval_minutes and fills them.
    """
    print(f"\n[Agent] Daemon mode — checking every {interval_minutes}m")
    print(f"[Agent] Channels: {', '.join(channels)}")
    print(f"[Agent] Press Ctrl+C to stop.\n")

    while True:
        try:
            run_agent_loop(channels, count=count, quality=quality, continuous=False)
        except KeyboardInterrupt:
            print("\n[Agent] Daemon stopped by user.")
            break
        except Exception as e:
            print(f"\n[Agent] Error in agent loop: {e}")
            print(f"[Agent] Will retry in {interval_minutes}m...")

        print(f"\n[Agent] Sleeping {interval_minutes}m until next check...")
        try:
            time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            print("\n[Agent] Daemon stopped by user.")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Agent Runner — autonomous video production for YouTube channels.",
    )
    parser.add_argument(
        "--channel", type=str, default=None,
        help="Channel ID to generate content for.",
    )
    parser.add_argument(
        "--all", action="store_true", dest="all_channels",
        help="Run for all configured channels.",
    )
    parser.add_argument(
        "--count", type=int, default=1,
        help="Number of slots to fill per channel (default: 1).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate scripts only — don't run pipeline or upload.",
    )
    parser.add_argument(
        "--quality", choices=["draft", "final"], default="final",
        help="Render quality (default: final).",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Run continuously, checking for empty slots on a schedule.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single session then exit (default: run continuously).",
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Minutes between daemon checks (default: 60).",
    )
    args = parser.parse_args()

    if not args.channel and not args.all_channels:
        print("Error: specify --channel <id> or --all")
        parser.print_help()
        sys.exit(1)

    if args.all_channels:
        from src.publishing.calendar_manager import list_channels
        channels = list(list_channels().keys())
    else:
        channels = [args.channel]

    if args.daemon:
        run_daemon(
            channels, count=args.count,
            quality=args.quality, interval_minutes=args.interval,
        )
    else:
        run_agent_loop(
            channels, count=args.count,
            dry_run=args.dry_run, quality=args.quality,
            continuous=not args.once,
        )


if __name__ == "__main__":
    main()
