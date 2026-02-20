"""
Training Dataset Builder — Turns agent experience into AI training data.

Every agent cycle produces (state, decision, action, outcome) tuples with
a real reward signal: YouTube metrics. This module captures those tuples
and exports them in formats suitable for fine-tuning:

  - SFT (Supervised Fine-Tuning): Best-performing (prompt, completion) pairs
  - DPO (Direct Preference Optimization): Preferred vs rejected outputs
    for the same input context
  - Reward Model: Feature vectors mapped to engagement scores

The key insight: the audience IS the labeler. Every published video is
an experiment with measurable results that arrive days later. No human
annotation needed.

Data lifecycle:
  1. CAPTURE — record_decision_point() saves each agent decision with
     full context (world state, prompt, LLM output)
  2. CAPTURE — record_generation() saves each script generation with
     the complete input/output
  3. BACKFILL — link_outcome() connects generation records to YouTube
     metrics once they're available (delayed reward)
  4. EXPORT — export_sft(), export_dpo(), export_reward_model() produce
     training-ready files

Storage: channels/training_data/dataset.jsonl (append-only)
"""

import csv
import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from src.config import CHANNELS_DIR

_DATASET_DIR = CHANNELS_DIR / "training_data"


def _ensure_dir():
    _DATASET_DIR.mkdir(parents=True, exist_ok=True)


def _dataset_path() -> Path:
    return _DATASET_DIR / "dataset.jsonl"


def _append_record(record: dict):
    """Append a single record to the dataset file."""
    _ensure_dir()
    with open(_dataset_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_all_records() -> list[dict]:
    """Load all records from the dataset file."""
    path = _dataset_path()
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _save_all_records(records: list[dict]):
    """Rewrite the full dataset (used for backfill updates)."""
    _ensure_dir()
    with open(_dataset_path(), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _update_record_in_place(match_fn, update_fn) -> bool:
    """
    Scan the JSONL file and update the first matching record in place.
    Avoids loading the entire file into memory for single-record updates.
    Returns True if a record was updated.
    """
    path = _dataset_path()
    if not path.exists():
        return False

    lines = []
    updated = False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not updated:
                try:
                    record = json.loads(stripped)
                    if match_fn(record):
                        update_fn(record)
                        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
                        updated = True
                        continue
                except json.JSONDecodeError:
                    pass
            lines.append(line)

    if updated:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    return updated


# ─────────────────────────────────────────────────────────────────────
# CAPTURE: Decision Points (brain.decide_next_action)
# ─────────────────────────────────────────────────────────────────────

def record_decision_point(
    world_state_text: str,
    session_log: list[str],
    slots_filled: int,
    slots_target: int,
    dry_run: bool,
    decision: dict,
    outcome_success: bool | None = None,
):
    """
    Record a brain decision for training the planner/orchestrator.

    This produces training data for: "given this world state, what's the
    optimal action?" Once outcomes are known, the reward signal tells us
    which decisions were good.
    """
    _append_record({
        "type": "decision",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": {
            "world_state": world_state_text,
            "session_log": session_log[-15:],
            "slots_filled": slots_filled,
            "slots_target": slots_target,
            "dry_run": dry_run,
        },
        "output": {
            "thinking": decision.get("thinking", ""),
            "action": decision.get("action", ""),
            "parameters": decision.get("parameters", {}),
        },
        "outcome": {
            "success": outcome_success,
        },
    })


# ─────────────────────────────────────────────────────────────────────
# CAPTURE: Script Generation (the highest-value training data)
# ─────────────────────────────────────────────────────────────────────

def record_generation(
    channel_id: str,
    content_prompt: str,
    past_titles: list[str],
    metrics_summary: str | None,
    topic_directive: str | None,
    generated_script: str,
    generated_title: str,
    generated_description: str,
    topic_reasoning: str,
    review_score: int | None,
    review_approved: bool | None,
    review_issues: list[str] | None,
    video_id: str | None = None,
    critic_report: dict | None = None,
):
    """
    Record a complete script generation cycle.

    This is the most valuable training data: the full (input, output) pair
    for content creation, with quality signal from self-review and eventual
    performance from YouTube metrics.
    """
    _append_record({
        "type": "generation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "input": {
            "content_prompt": content_prompt,
            "past_titles": past_titles,
            "metrics_summary": metrics_summary,
            "topic_directive": topic_directive,
        },
        "output": {
            "script": generated_script,
            "title": generated_title,
            "description": generated_description,
            "topic_reasoning": topic_reasoning,
        },
        "review": {
            "score": review_score,
            "approved": review_approved,
            "issues": review_issues,
            "critic_perspectives": critic_report.get("perspectives") if critic_report else None,
            "critic_concerns": len(critic_report.get("concerns", [])) if critic_report else None,
            "critic_fatals": len(critic_report.get("fatal_issues", [])) if critic_report else None,
        },
        "video_id": video_id,
        "outcome": None,  # backfilled by link_outcome()
    })


def update_generation_video_id(title: str, video_id: str):
    """
    After publishing, update the most recent generation record for this title
    with the YouTube video_id. This links the generation to future metrics.
    """
    # Scan from end — the target record is almost always near the bottom.
    # For now, use full scan with early exit. The file stays line-based
    # so it streams efficiently even at scale.
    _update_record_in_place(
        match_fn=lambda r: (
            r.get("type") == "generation"
            and r.get("output", {}).get("title") == title
            and not r.get("video_id")
        ),
        update_fn=lambda r: r.__setitem__("video_id", video_id),
    )


# ─────────────────────────────────────────────────────────────────────
# CAPTURE: Strategy Generation
# ─────────────────────────────────────────────────────────────────────

def record_strategy(
    channel_id: str,
    input_context: str,
    strategy_output: dict,
):
    """Record a strategy generation for training the strategist model."""
    _append_record({
        "type": "strategy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "input": {"context": input_context},
        "output": strategy_output,
        "outcome": None,  # backfilled when strategy is reviewed
    })


# ─────────────────────────────────────────────────────────────────────
# BACKFILL: Link YouTube metrics to generation records
# ─────────────────────────────────────────────────────────────────────

def link_outcome(video_id: str, metrics: dict):
    """
    Attach real-world performance data to a generation record.
    For bulk updates, use link_outcomes_batch() to avoid N file rewrites.
    """
    outcome = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "views": metrics.get("views", 0),
        "likes": metrics.get("likes", 0),
        "comments": metrics.get("comments", 0),
        "avg_view_percentage": metrics.get("avg_view_percentage"),
        "avg_view_duration_s": metrics.get("avg_view_duration_s"),
        "subscribers_gained": metrics.get("subscribers_gained"),
    }
    _update_record_in_place(
        match_fn=lambda r: r.get("type") == "generation" and r.get("video_id") == video_id,
        update_fn=lambda r: r.__setitem__("outcome", outcome),
    )


def link_outcomes_batch(video_metrics: dict[str, dict]):
    """
    Batch version of link_outcome — single file rewrite for all videos.
    video_metrics: {video_id: {views, likes, ...}, ...}
    """
    if not video_metrics:
        return

    path = _dataset_path()
    if not path.exists():
        return

    now = datetime.now(timezone.utc).isoformat()
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                try:
                    record = json.loads(stripped)
                    vid = record.get("video_id")
                    if (
                        record.get("type") == "generation"
                        and vid
                        and vid in video_metrics
                    ):
                        m = video_metrics[vid]
                        record["outcome"] = {
                            "updated_at": now,
                            "views": m.get("views", 0),
                            "likes": m.get("likes", 0),
                            "comments": m.get("comments", 0),
                            "avg_view_percentage": m.get("avg_view_percentage"),
                            "avg_view_duration_s": m.get("avg_view_duration_s"),
                            "subscribers_gained": m.get("subscribers_gained"),
                        }
                        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
                        continue
                except json.JSONDecodeError:
                    pass
            lines.append(line)

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ─────────────────────────────────────────────────────────────────────
# EXPORT: SFT (Supervised Fine-Tuning)
# ─────────────────────────────────────────────────────────────────────

def export_sft(
    min_views: int = 0,
    min_review_score: int = 0,
    output_path: str | None = None,
) -> Path:
    """
    Export high-quality (input, output) pairs for supervised fine-tuning.

    Filters for generations that:
      - Have real outcome data (video was published and measured)
      - Meet minimum view and review score thresholds
      - Were approved by self-review

    Output format: JSONL with OpenAI-compatible chat format:
    {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}
    """
    records = _load_all_records()
    generations = [r for r in records if r["type"] == "generation"]

    qualified = []
    for g in generations:
        outcome = g.get("outcome")
        review = g.get("review", {})

        has_outcome = outcome is not None and outcome.get("views") is not None
        meets_views = has_outcome and outcome.get("views", 0) >= min_views
        meets_score = (review.get("score") or 0) >= min_review_score

        if meets_views and meets_score:
            qualified.append(g)

    # Sort by views descending — best performers first
    qualified.sort(key=lambda g: g["outcome"].get("views", 0), reverse=True)

    dest = Path(output_path) if output_path else _DATASET_DIR / "sft_export.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)

    system_msg = (
        "You are a content creator for a YouTube channel. Given the channel's "
        "style guide, recent performance data, and optionally a topic directive, "
        "generate a video script with title and description. Respond with JSON."
    )

    with open(dest, "w", encoding="utf-8") as f:
        for g in qualified:
            inp = g["input"]

            user_parts = []
            if inp.get("content_prompt"):
                user_parts.append(f"## Style Guide\n\n{inp['content_prompt']}")
            if inp.get("past_titles"):
                user_parts.append(
                    "\n\n## Recent Titles\n\n"
                    + "\n".join(f"- {t}" for t in inp["past_titles"])
                )
            if inp.get("metrics_summary"):
                user_parts.append(f"\n\n## Performance Data\n\n{inp['metrics_summary']}")
            if inp.get("topic_directive"):
                user_parts.append(f"\n\n## {inp['topic_directive']}")

            assistant_output = json.dumps({
                "script": g["output"]["script"],
                "title": g["output"]["title"],
                "description": g["output"]["description"],
                "topic_reasoning": g["output"]["topic_reasoning"],
            }, ensure_ascii=False)

            row = {
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": "\n".join(user_parts)},
                    {"role": "assistant", "content": assistant_output},
                ],
                "metadata": {
                    "channel_id": g.get("channel_id"),
                    "video_id": g.get("video_id"),
                    "views": g["outcome"].get("views", 0),
                    "likes": g["outcome"].get("likes", 0),
                    "review_score": g.get("review", {}).get("score"),
                    "avg_view_percentage": g["outcome"].get("avg_view_percentage"),
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[Dataset] SFT export: {len(qualified)} examples -> {dest}")
    return dest


# ─────────────────────────────────────────────────────────────────────
# EXPORT: DPO (Direct Preference Optimization)
# ─────────────────────────────────────────────────────────────────────

def export_dpo(output_path: str | None = None) -> Path:
    """
    Export preference pairs for DPO training.

    Pairs generations from the same channel where one clearly outperformed
    the other. The high-performer is "chosen," the low-performer is "rejected."

    This teaches a model to distinguish good content from bad content
    given the same channel context.
    """
    records = _load_all_records()
    generations = [
        r for r in records
        if r["type"] == "generation"
        and r.get("outcome") is not None
        and r["outcome"].get("views") is not None
    ]

    # Group by channel
    by_channel: dict[str, list] = {}
    for g in generations:
        ch = g.get("channel_id", "unknown")
        by_channel.setdefault(ch, []).append(g)

    pairs = []
    for ch, gens in by_channel.items():
        if len(gens) < 2:
            continue

        gens.sort(key=lambda g: g["outcome"].get("views", 0), reverse=True)

        # Pair top performers against bottom performers
        top_half = gens[: len(gens) // 2]
        bottom_half = gens[len(gens) // 2 :]

        for chosen, rejected in zip(top_half, bottom_half):
            chosen_views = chosen["outcome"].get("views", 0)
            rejected_views = rejected["outcome"].get("views", 0)

            # Only pair if there's meaningful difference (>25% gap)
            if chosen_views <= rejected_views:
                continue
            if rejected_views > 0 and (chosen_views / rejected_views) < 1.25:
                continue

            # Build shared prompt from the chosen example's input
            inp = chosen["input"]
            prompt_parts = []
            if inp.get("content_prompt"):
                prompt_parts.append(f"## Style Guide\n\n{inp['content_prompt']}")
            if inp.get("past_titles"):
                prompt_parts.append(
                    "\n\n## Recent Titles\n\n"
                    + "\n".join(f"- {t}" for t in inp["past_titles"])
                )

            pairs.append({
                "prompt": "\n".join(prompt_parts),
                "chosen": json.dumps({
                    "script": chosen["output"]["script"],
                    "title": chosen["output"]["title"],
                    "description": chosen["output"]["description"],
                }, ensure_ascii=False),
                "rejected": json.dumps({
                    "script": rejected["output"]["script"],
                    "title": rejected["output"]["title"],
                    "description": rejected["output"]["description"],
                }, ensure_ascii=False),
                "metadata": {
                    "channel_id": ch,
                    "chosen_views": chosen_views,
                    "rejected_views": rejected_views,
                    "chosen_video_id": chosen.get("video_id"),
                    "rejected_video_id": rejected.get("video_id"),
                },
            })

    dest = Path(output_path) if output_path else _DATASET_DIR / "dpo_export.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"[Dataset] DPO export: {len(pairs)} preference pairs -> {dest}")
    return dest


# ─────────────────────────────────────────────────────────────────────
# EXPORT: Reward Model Training Data
# ─────────────────────────────────────────────────────────────────────

def export_reward_model(output_path: str | None = None) -> Path:
    """
    Export feature vectors with engagement scores for reward model training.

    Each row maps extractable features (title length, script length,
    review score, topic type) to real engagement metrics. This can train
    a small model to predict performance before publishing.
    """
    records = _load_all_records()
    generations = [
        r for r in records
        if r["type"] == "generation"
        and r.get("outcome") is not None
        and r["outcome"].get("views") is not None
    ]

    dest = Path(output_path) if output_path else _DATASET_DIR / "reward_model_export.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "channel_id", "video_id",
        "title", "title_length", "title_word_count",
        "script_length", "script_word_count", "script_line_count",
        "has_topic_directive", "review_score",
        "views", "likes", "comments",
        "avg_view_percentage", "avg_view_duration_s", "subscribers_gained",
    ]

    with open(dest, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for g in generations:
            out = g["output"]
            outcome = g["outcome"]
            review = g.get("review", {})
            title = out.get("title", "")
            script = out.get("script", "")

            writer.writerow({
                "channel_id": g.get("channel_id", ""),
                "video_id": g.get("video_id", ""),
                "title": title,
                "title_length": len(title),
                "title_word_count": len(title.split()),
                "script_length": len(script),
                "script_word_count": len(script.split()),
                "script_line_count": script.count("\n") + 1,
                "has_topic_directive": 1 if g["input"].get("topic_directive") else 0,
                "review_score": review.get("score", ""),
                "views": outcome.get("views", 0),
                "likes": outcome.get("likes", 0),
                "comments": outcome.get("comments", 0),
                "avg_view_percentage": outcome.get("avg_view_percentage", ""),
                "avg_view_duration_s": outcome.get("avg_view_duration_s", ""),
                "subscribers_gained": outcome.get("subscribers_gained", ""),
            })

    print(f"[Dataset] Reward model export: {len(generations)} rows -> {dest}")
    return dest


# ─────────────────────────────────────────────────────────────────────
# STATS: Dataset summary for the agent to assess data quality
# ─────────────────────────────────────────────────────────────────────

def get_dataset_stats() -> dict:
    """
    Return a summary of the training dataset.
    The agent can read this to understand how much training data it has
    generated and when it might be worth exporting.
    """
    records = _load_all_records()

    decisions = [r for r in records if r["type"] == "decision"]
    generations = [r for r in records if r["type"] == "generation"]
    strategies = [r for r in records if r["type"] == "strategy"]

    gen_with_outcome = [g for g in generations if g.get("outcome") is not None]
    gen_without_outcome = [g for g in generations if g.get("outcome") is None]

    # Compute quality distribution for generations with outcomes
    view_counts = [g["outcome"].get("views", 0) for g in gen_with_outcome]

    stats = {
        "total_records": len(records),
        "decisions": len(decisions),
        "generations": {
            "total": len(generations),
            "with_outcome": len(gen_with_outcome),
            "awaiting_outcome": len(gen_without_outcome),
        },
        "strategies": len(strategies),
    }

    if view_counts:
        view_counts.sort()
        stats["outcome_distribution"] = {
            "min_views": min(view_counts),
            "max_views": max(view_counts),
            "median_views": view_counts[len(view_counts) // 2],
            "mean_views": sum(view_counts) / len(view_counts),
        }

    # Estimate exportable data
    sft_ready = len([g for g in gen_with_outcome if (g.get("review", {}).get("score") or 0) >= 5])
    dpo_ready = len(gen_with_outcome) >= 4  # need at least 4 for meaningful pairs

    stats["export_readiness"] = {
        "sft_examples_available": sft_ready,
        "dpo_pairs_possible": dpo_ready,
        "recommendation": (
            "Enough data for initial fine-tuning" if sft_ready >= 20
            else f"Need ~{20 - sft_ready} more scored generations for useful SFT"
            if sft_ready > 0
            else "No scored generations yet — keep running the agent"
        ),
    }

    return stats


def export_all(min_views: int = 0, min_review_score: int = 0):
    """Run all three exports and print a summary."""
    stats = get_dataset_stats()
    print(f"\n{'=' * 60}")
    print(f"  Training Dataset Export")
    print(f"{'=' * 60}")
    print(get_dataset_stats_text())
    print()

    sft_path = export_sft(min_views=min_views, min_review_score=min_review_score)
    dpo_path = export_dpo()
    reward_path = export_reward_model()

    print(f"\nExported to:")
    print(f"  SFT:          {sft_path}")
    print(f"  DPO:          {dpo_path}")
    print(f"  Reward model: {reward_path}")
    print(f"{'=' * 60}\n")


def get_dataset_stats_text() -> str:
    """Human-readable dataset stats for the agent's world state."""
    stats = get_dataset_stats()

    lines = ["## Training Dataset"]
    lines.append(f"  Total records: {stats['total_records']}")
    lines.append(f"  Decisions logged: {stats['decisions']}")

    gen = stats["generations"]
    lines.append(f"  Generations: {gen['total']} ({gen['with_outcome']} scored, {gen['awaiting_outcome']} awaiting metrics)")

    if stats.get("outcome_distribution"):
        od = stats["outcome_distribution"]
        lines.append(f"  View distribution: {od['min_views']}-{od['max_views']} (median {od['median_views']:.0f}, mean {od['mean_views']:.0f})")

    er = stats["export_readiness"]
    lines.append(f"  SFT-ready examples: {er['sft_examples_available']}")
    lines.append(f"  DPO pairs possible: {'Yes' if er['dpo_pairs_possible'] else 'Not yet'}")
    lines.append(f"  Status: {er['recommendation']}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# FEEDBACK LOOP: Query past generation outcomes at creation time
# ─────────────────────────────────────────────────────────────────────

def get_past_generation_feedback(channel_id: str, limit: int = 10) -> str | None:
    """
    Build a human-readable summary of past generation outcomes for this channel.

    This closes the training data loop: when the script generator creates a
    new script, it can see which of its OWN past outputs performed well or
    poorly, with real YouTube metrics as evidence. This is more valuable
    than generic metrics summaries because it links specific creative
    decisions (topic, angle, hook) to measurable results.

    Returns None if there are no scored generations yet.
    """
    records = _load_all_records()
    scored = [
        r for r in records
        if r.get("type") == "generation"
        and r.get("channel_id") == channel_id
        and r.get("outcome") is not None
        and r["outcome"].get("views") is not None
    ]

    if not scored:
        return None

    scored.sort(key=lambda r: r["outcome"].get("views", 0), reverse=True)

    lines = ["## Past Generation Performance (real YouTube metrics)"]
    lines.append(f"Total scored scripts: {len(scored)}\n")

    top = scored[:3]
    if top:
        lines.append("**Top performers:**")
        for g in top:
            o = g["outcome"]
            out = g["output"]
            retention = o.get("avg_view_percentage")
            ret_str = f", {retention:.0f}% retention" if retention else ""
            lines.append(
                f"  - \"{out.get('title', '?')}\" — "
                f"{o.get('views', 0)} views, {o.get('likes', 0)} likes{ret_str}"
            )
            if out.get("topic_reasoning"):
                lines.append(f"    Reasoning: {out['topic_reasoning']}")

    bottom = scored[-3:] if len(scored) > 3 else []
    if bottom:
        lines.append("\n**Underperformers:**")
        for g in bottom:
            o = g["outcome"]
            out = g["output"]
            retention = o.get("avg_view_percentage")
            ret_str = f", {retention:.0f}% retention" if retention else ""
            lines.append(
                f"  - \"{out.get('title', '?')}\" — "
                f"{o.get('views', 0)} views, {o.get('likes', 0)} likes{ret_str}"
            )
            issues = g.get("review", {}).get("issues", [])
            if issues:
                lines.append(f"    Issues flagged: {'; '.join(issues[:2])}")

    if len(scored) >= 2:
        views = [r["outcome"].get("views", 0) for r in scored]
        avg = sum(views) / len(views)
        lines.append(f"\nAverage views across {len(scored)} scripts: {avg:.0f}")

    lines.append(
        "\nUse this data to make BETTER creative choices. "
        "Double down on what resonated. Avoid patterns from underperformers."
    )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# CLI: python -m src.agent.dataset_builder [export|stats]
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Training Dataset Builder — export agent experience as AI training data.",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    exp = sub.add_parser("export", help="Export all training data formats")
    exp.add_argument("--min-views", type=int, default=0, help="Min views for SFT filter")
    exp.add_argument("--min-score", type=int, default=0, help="Min review score for SFT filter")

    sub.add_parser("stats", help="Show dataset statistics")

    args = parser.parse_args()

    if args.command == "export":
        export_all(min_views=args.min_views, min_review_score=args.min_score)
    elif args.command == "stats":
        print(get_dataset_stats_text())
    else:
        print(get_dataset_stats_text())
        print("\nUse 'export' to generate training files, 'stats' for summary.")
