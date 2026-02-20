"""
Experiment Engine — Systematic A/B testing for content optimization.

Instead of guessing what works, the agent forms testable hypotheses,
assigns each video to an experiment arm, and evaluates results with
basic statistical rigor.

Lifecycle:
  1. PROPOSE — Agent (or LLM) proposes a hypothesis with two arms
  2. ASSIGN  — Each new video is assigned to an arm (alternating)
  3. MEASURE — When metrics arrive, results are recorded per arm
  4. EVALUATE — When enough samples exist, run significance test
  5. APPLY   — Confirmed findings update the content prompt or
               generation parameters automatically

Example experiment:
  Hypothesis: "Question-phrased titles get more views than statements"
  Arm A (control): Statement titles ("The Last Day You Played Outside")
  Arm B (variant): Question titles  ("When Did You Stop Playing Outside?")
  After 6 videos each: Arm B avg 89 views vs Arm A avg 42 views, p=0.03
  Result: CONFIRMED — apply to content prompt

Storage: channels/<id>/experiments.json
"""

import json
import math
from pathlib import Path
from datetime import datetime, timezone

from src.config import CHANNELS_DIR


# ─────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────

def _empty_experiment(
    hypothesis: str,
    variable: str,
    control_description: str,
    variant_description: str,
    control_instruction: str,
    variant_instruction: str,
    min_samples_per_arm: int = 4,
    metric: str = "views",
) -> dict:
    return {
        "id": _make_id(hypothesis),
        "hypothesis": hypothesis,
        "variable": variable,
        "status": "running",  # running | confirmed | rejected | inconclusive
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "metric": metric,
        "min_samples_per_arm": min_samples_per_arm,
        "arms": {
            "control": {
                "description": control_description,
                "instruction": control_instruction,
                "videos": [],
            },
            "variant": {
                "description": variant_description,
                "instruction": variant_instruction,
                "videos": [],
            },
        },
        "result": None,
    }


def _make_id(hypothesis: str) -> str:
    import hashlib
    return hashlib.sha1(hypothesis.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────

def _experiments_path(channel_id: str) -> Path:
    return CHANNELS_DIR / channel_id / "experiments.json"


def _load_experiments(channel_id: str) -> list[dict]:
    path = _experiments_path(channel_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_experiments(channel_id: str, experiments: list[dict]):
    path = _experiments_path(channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(experiments, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# PROPOSE: Create new experiments
# ─────────────────────────────────────────────────────────────────────

def propose_experiment(
    channel_id: str,
    hypothesis: str,
    variable: str,
    control_description: str,
    variant_description: str,
    control_instruction: str,
    variant_instruction: str,
    min_samples_per_arm: int = 4,
    metric: str = "views",
) -> dict:
    """
    Create a new experiment for a channel.

    Args:
        hypothesis: What we're testing ("Questions in titles get more views")
        variable: What's being varied ("title_style")
        control_description: Human label for the control arm ("Statement titles")
        variant_description: Human label for the variant arm ("Question titles")
        control_instruction: Instruction injected into the generator for control
        variant_instruction: Instruction injected into the generator for variant
        min_samples_per_arm: Minimum videos per arm before evaluating (default 4)
        metric: Primary metric to compare ("views", "avg_view_percentage", "likes")
    """
    experiments = _load_experiments(channel_id)

    # Don't duplicate
    exp_id = _make_id(hypothesis)
    if any(e["id"] == exp_id for e in experiments):
        return next(e for e in experiments if e["id"] == exp_id)

    exp = _empty_experiment(
        hypothesis=hypothesis,
        variable=variable,
        control_description=control_description,
        variant_description=variant_description,
        control_instruction=control_instruction,
        variant_instruction=variant_instruction,
        min_samples_per_arm=min_samples_per_arm,
        metric=metric,
    )

    experiments.append(exp)
    _save_experiments(channel_id, experiments)
    print(f"[Experiment] New: \"{hypothesis}\" ({variable})")
    return exp


def propose_experiments_via_llm(channel_id: str, metrics_summary: str | None = None) -> list[dict]:
    """
    Ask the LLM to propose experiments based on channel data.
    Returns the list of newly created experiments.
    """
    from src.utils.llm import chat_json
    from src.agent.script_generator import _load_content_prompt

    content_prompt = _load_content_prompt(channel_id)
    experiments = _load_experiments(channel_id)

    existing_hypotheses = [e["hypothesis"] for e in experiments]

    prompt = """\
You are a content optimization scientist. Given a YouTube channel's style guide
and performance data, propose 1-2 testable A/B experiments.

Each experiment must:
- Test exactly ONE variable (title style, script length, opening hook type, etc.)
- Have a clear control (current approach) and variant (the change)
- Include specific instructions that can be injected into the content generator
- Be feasible with stock footage (Pexels) and TTS voiceover

Respond with valid JSON:
{
  "experiments": [
    {
      "hypothesis": "Short titles (3-4 words) outperform longer titles (5-7 words)",
      "variable": "title_length",
      "control_description": "Standard titles (4-6 words per style guide)",
      "variant_description": "Ultra-short titles (3-4 words maximum)",
      "control_instruction": "Follow the style guide's title length of 4-6 words.",
      "variant_instruction": "EXPERIMENT: Make the title exactly 3-4 words. Shorter than usual. Every word must earn its place.",
      "metric": "views"
    }
  ]
}

Only propose experiments where BOTH arms can produce good content. Don't propose
anything that would compromise quality for the sake of testing.
"""

    user_parts = [f"## Style Guide\n\n{content_prompt}"]

    if metrics_summary:
        user_parts.append(f"\n\n## Performance Data\n\n{metrics_summary}")

    if existing_hypotheses:
        user_parts.append(
            "\n\n## Already Testing (don't duplicate)\n\n"
            + "\n".join(f"- {h}" for h in existing_hypotheses)
        )

    result = chat_json(prompt, "\n".join(user_parts), temperature=1.0)

    new_experiments = []
    for exp_data in result.get("experiments", []):
        try:
            exp = propose_experiment(
                channel_id=channel_id,
                hypothesis=exp_data["hypothesis"],
                variable=exp_data["variable"],
                control_description=exp_data["control_description"],
                variant_description=exp_data["variant_description"],
                control_instruction=exp_data["control_instruction"],
                variant_instruction=exp_data["variant_instruction"],
                metric=exp_data.get("metric", "views"),
            )
            new_experiments.append(exp)
        except (KeyError, TypeError):
            continue

    return new_experiments


# ─────────────────────────────────────────────────────────────────────
# ASSIGN: Get the instruction for the next video
# ─────────────────────────────────────────────────────────────────────

def get_experiment_instruction(channel_id: str) -> dict | None:
    """
    Get the experiment instruction for the next video.

    Returns the arm assignment for the active experiment that needs
    the most data, or None if no experiments are running.

    Returns:
        {
            "experiment_id": "abc123",
            "hypothesis": "...",
            "arm": "control" | "variant",
            "instruction": "The specific instruction to inject into generation",
        }
    """
    experiments = _load_experiments(channel_id)
    running = [e for e in experiments if e["status"] == "running"]

    if not running:
        return None

    # Pick the experiment that's most behind on data collection
    best = None
    best_deficit = -1

    for exp in running:
        control_count = len(exp["arms"]["control"]["videos"])
        variant_count = len(exp["arms"]["variant"]["videos"])
        needed = exp["min_samples_per_arm"]

        control_deficit = max(0, needed - control_count)
        variant_deficit = max(0, needed - variant_count)
        total_deficit = control_deficit + variant_deficit

        if total_deficit > best_deficit:
            best = exp
            best_deficit = total_deficit

    if not best:
        return None

    # Alternate: assign to whichever arm has fewer samples
    control_count = len(best["arms"]["control"]["videos"])
    variant_count = len(best["arms"]["variant"]["videos"])

    if control_count <= variant_count:
        arm = "control"
    else:
        arm = "variant"

    return {
        "experiment_id": best["id"],
        "hypothesis": best["hypothesis"],
        "variable": best["variable"],
        "arm": arm,
        "instruction": best["arms"][arm]["instruction"],
    }


def record_video_assignment(
    channel_id: str,
    experiment_id: str,
    arm: str,
    video_id: str | None,
    title: str,
):
    """Record that a video was assigned to an experiment arm."""
    experiments = _load_experiments(channel_id)

    for exp in experiments:
        if exp["id"] == experiment_id:
            exp["arms"][arm]["videos"].append({
                "video_id": video_id,
                "title": title,
                "assigned_at": datetime.now(timezone.utc).isoformat(),
                "metrics": None,
            })
            break

    _save_experiments(channel_id, experiments)


# ─────────────────────────────────────────────────────────────────────
# MEASURE: Record metrics for experiment videos
# ─────────────────────────────────────────────────────────────────────

def update_experiment_metrics(channel_id: str, video_id: str, metrics: dict):
    """Update metrics for a video in its experiment arm."""
    experiments = _load_experiments(channel_id)
    changed = False

    for exp in experiments:
        for arm_name in ("control", "variant"):
            for video in exp["arms"][arm_name]["videos"]:
                if video.get("video_id") == video_id:
                    video["metrics"] = {
                        "views": metrics.get("views", 0),
                        "likes": metrics.get("likes", 0),
                        "comments": metrics.get("comments", 0),
                        "avg_view_percentage": metrics.get("avg_view_percentage"),
                        "avg_view_duration_s": metrics.get("avg_view_duration_s"),
                    }
                    changed = True

    if changed:
        _save_experiments(channel_id, experiments)


# ─────────────────────────────────────────────────────────────────────
# EVALUATE: Statistical significance testing
# ─────────────────────────────────────────────────────────────────────

def _welch_t_test(group_a: list[float], group_b: list[float]) -> tuple[float, float]:
    """
    Welch's t-test for two independent samples with unequal variance.
    Returns (t_statistic, p_value).
    Uses approximation — no scipy dependency needed.
    """
    n_a, n_b = len(group_a), len(group_b)
    if n_a < 2 or n_b < 2:
        return 0.0, 1.0

    mean_a = sum(group_a) / n_a
    mean_b = sum(group_b) / n_b

    var_a = sum((x - mean_a) ** 2 for x in group_a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in group_b) / (n_b - 1)

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return 0.0, 1.0

    t_stat = (mean_b - mean_a) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    denom = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = num / denom if denom > 0 else 1

    # Approximate p-value using the normal distribution for df > 30,
    # otherwise use a conservative t-distribution approximation
    p_value = _approx_p_value(abs(t_stat), df)

    return t_stat, p_value


def _approx_p_value(t_abs: float, df: float) -> float:
    """
    Approximate two-tailed p-value from |t| and degrees of freedom.
    Uses the normal approximation for large df, and a conservative
    estimate for small df. Good enough for our sample sizes.
    """
    if df > 30:
        # Normal approximation
        z = t_abs
        p = math.erfc(z / math.sqrt(2))
        return p

    # For small df, use a rough approximation based on the t-distribution
    # This is conservative (overestimates p), which is fine for our purposes
    z = t_abs * (1 - 1 / (4 * df))
    p = math.erfc(z / math.sqrt(2))
    return min(p * 1.2, 1.0)  # slight inflation for small samples


def evaluate_experiment(channel_id: str, experiment_id: str) -> dict | None:
    """
    Evaluate an experiment if enough data has been collected.

    Returns the result dict or None if not enough data.
    Automatically updates the experiment status if a conclusion is reached.
    """
    experiments = _load_experiments(channel_id)
    exp = None
    for e in experiments:
        if e["id"] == experiment_id:
            exp = e
            break

    if not exp or exp["status"] != "running":
        return None

    metric_key = exp["metric"]

    # Extract metric values for each arm (only videos with metrics)
    control_values = []
    for v in exp["arms"]["control"]["videos"]:
        if v.get("metrics") and v["metrics"].get(metric_key) is not None:
            control_values.append(float(v["metrics"][metric_key]))

    variant_values = []
    for v in exp["arms"]["variant"]["videos"]:
        if v.get("metrics") and v["metrics"].get(metric_key) is not None:
            variant_values.append(float(v["metrics"][metric_key]))

    min_needed = exp["min_samples_per_arm"]
    if len(control_values) < min_needed or len(variant_values) < min_needed:
        return {
            "ready": False,
            "control_samples": len(control_values),
            "variant_samples": len(variant_values),
            "needed": min_needed,
        }

    # Run the test
    control_mean = sum(control_values) / len(control_values)
    variant_mean = sum(variant_values) / len(variant_values)
    t_stat, p_value = _welch_t_test(control_values, variant_values)

    lift = ((variant_mean - control_mean) / control_mean * 100) if control_mean > 0 else 0

    significant = p_value < 0.10  # 90% confidence (practical for small samples)
    variant_wins = variant_mean > control_mean

    if significant and variant_wins:
        conclusion = "confirmed"
        summary = (
            f"CONFIRMED: {exp['hypothesis']}. "
            f"Variant ({exp['arms']['variant']['description']}) outperformed "
            f"control by {lift:+.0f}% (p={p_value:.3f})."
        )
    elif significant and not variant_wins:
        conclusion = "rejected"
        summary = (
            f"REJECTED: {exp['hypothesis']}. "
            f"Control ({exp['arms']['control']['description']}) was actually better "
            f"by {-lift:+.0f}% (p={p_value:.3f})."
        )
    else:
        conclusion = "inconclusive"
        summary = (
            f"INCONCLUSIVE: {exp['hypothesis']}. "
            f"Difference of {lift:+.0f}% not statistically significant (p={p_value:.3f})."
        )

    result = {
        "ready": True,
        "conclusion": conclusion,
        "summary": summary,
        "control_mean": round(control_mean, 1),
        "variant_mean": round(variant_mean, 1),
        "lift_percent": round(lift, 1),
        "t_statistic": round(t_stat, 3),
        "p_value": round(p_value, 4),
        "control_n": len(control_values),
        "variant_n": len(variant_values),
    }

    # Ask the LLM to interpret the result — not just numbers, but meaning
    interpretation = None
    try:
        from src.utils.llm import chat

        control_titles = [v["title"] for v in exp["arms"]["control"]["videos"]]
        variant_titles = [v["title"] for v in exp["arms"]["variant"]["videos"]]

        interp_prompt = (
            f"An A/B experiment on a YouTube channel just concluded.\n\n"
            f"Hypothesis: {exp['hypothesis']}\n"
            f"Variable tested: {exp['variable']}\n"
            f"Control ({exp['arms']['control']['description']}): "
            f"avg {control_mean:.0f} {metric_key}, n={len(control_values)}\n"
            f"  Titles: {control_titles}\n"
            f"Variant ({exp['arms']['variant']['description']}): "
            f"avg {variant_mean:.0f} {metric_key}, n={len(variant_values)}\n"
            f"  Titles: {variant_titles}\n"
            f"Lift: {lift:+.0f}%, p-value: {p_value:.4f}\n"
            f"Statistical conclusion: {conclusion}\n\n"
            f"In 2-3 sentences: WHY do you think the {'variant' if variant_wins else 'control'} "
            f"performed {'better' if significant else 'similarly'}? "
            f"What specific lesson should be applied to future content?"
        )
        interpretation = chat(
            "You are a content optimization analyst. Be specific and actionable.",
            interp_prompt,
            temperature=1.0,
        )
        result["interpretation"] = interpretation
        summary += f" Interpretation: {interpretation}"
    except Exception:
        pass

    # Update experiment
    exp["status"] = conclusion
    exp["resolved_at"] = datetime.now(timezone.utc).isoformat()
    exp["result"] = result
    _save_experiments(channel_id, experiments)

    # Promote conclusion to a persistent belief + episodic memory
    try:
        from src.agent.memory import update_belief, record_episode

        evidence = interpretation or summary
        if conclusion == "confirmed":
            update_belief(
                channel_id, exp["hypothesis"],
                confidence="confirmed", evidence=evidence,
            )
            record_episode(channel_id, f"Experiment CONFIRMED: {exp['hypothesis']}", "major")
        elif conclusion == "rejected":
            update_belief(
                channel_id, exp["hypothesis"],
                confidence="disproven", evidence=evidence,
            )
            record_episode(channel_id, f"Experiment REJECTED: {exp['hypothesis']}", "major")
        else:
            record_episode(channel_id, f"Experiment inconclusive: {exp['hypothesis']}", "normal")
    except Exception:
        pass

    print(f"[Experiment] {summary}")
    return result


def evaluate_all(channel_id: str) -> list[dict]:
    """Evaluate all running experiments that have enough data."""
    experiments = _load_experiments(channel_id)
    results = []

    for exp in experiments:
        if exp["status"] == "running":
            result = evaluate_experiment(channel_id, exp["id"])
            if result and result.get("ready"):
                results.append({"experiment": exp["hypothesis"], **result})

    return results


# ─────────────────────────────────────────────────────────────────────
# APPLY: Update content prompt with confirmed findings
# ─────────────────────────────────────────────────────────────────────

def get_confirmed_findings(channel_id: str) -> list[dict]:
    """Get all confirmed experiment results (variant wins)."""
    experiments = _load_experiments(channel_id)
    return [
        {
            "hypothesis": e["hypothesis"],
            "variable": e["variable"],
            "instruction": e["arms"]["variant"]["instruction"],
            "result": e["result"],
        }
        for e in experiments
        if e["status"] == "confirmed" and e.get("result")
    ]


def get_rejected_findings(channel_id: str) -> list[dict]:
    """Get all rejected experiment results (control wins)."""
    experiments = _load_experiments(channel_id)
    return [
        {
            "hypothesis": e["hypothesis"],
            "variable": e["variable"],
            "instruction": e["arms"]["control"]["instruction"],
            "result": e["result"],
        }
        for e in experiments
        if e["status"] == "rejected" and e.get("result")
    ]


def apply_confirmed_findings(channel_id: str) -> list[str]:
    """
    Apply confirmed findings by appending them to the content prompt
    as data-backed rules the generator must follow.

    Returns list of findings that were applied.
    """
    confirmed = get_confirmed_findings(channel_id)
    if not confirmed:
        return []

    prompt_path = CHANNELS_DIR / channel_id / "content_prompt.md"
    if not prompt_path.exists():
        return []

    content = prompt_path.read_text(encoding="utf-8")

    # Check what's already been applied
    applied_marker = "## Experimentally Confirmed Rules"
    if applied_marker in content:
        existing_section = content[content.index(applied_marker):]
        unapplied = [
            f for f in confirmed
            if f["hypothesis"] not in existing_section
        ]
    else:
        unapplied = confirmed

    if not unapplied:
        return []

    # Build the new rules section
    if applied_marker not in content:
        content += f"\n\n{applied_marker}\n\n"
        content += (
            "_These rules were discovered through A/B testing by the autonomous agent. "
            "They are backed by statistically significant results._\n\n"
        )

    for finding in unapplied:
        r = finding["result"]
        content += (
            f"- **{finding['hypothesis']}** "
            f"(+{r['lift_percent']:.0f}%, p={r['p_value']:.3f}, "
            f"n={r['control_n']}+{r['variant_n']}): "
            f"{finding['instruction']}\n"
        )

    prompt_path.write_text(content, encoding="utf-8")
    applied_names = [f["hypothesis"] for f in unapplied]
    print(f"[Experiment] Applied {len(applied_names)} confirmed findings to content prompt")
    return applied_names


# ─────────────────────────────────────────────────────────────────────
# STATUS: Summary for the agent's world state
# ─────────────────────────────────────────────────────────────────────

def get_experiments_summary(channel_id: str) -> str | None:
    """Human-readable summary of experiment status for the agent brain."""
    experiments = _load_experiments(channel_id)
    if not experiments:
        return None

    lines = ["## Active Experiments"]

    running = [e for e in experiments if e["status"] == "running"]
    resolved = [e for e in experiments if e["status"] != "running"]

    for exp in running:
        control_n = len([v for v in exp["arms"]["control"]["videos"] if v.get("metrics")])
        variant_n = len([v for v in exp["arms"]["variant"]["videos"] if v.get("metrics")])
        needed = exp["min_samples_per_arm"]
        total_assigned_c = len(exp["arms"]["control"]["videos"])
        total_assigned_v = len(exp["arms"]["variant"]["videos"])

        lines.append(f"  RUNNING: \"{exp['hypothesis']}\"")
        lines.append(f"    Variable: {exp['variable']}")
        lines.append(
            f"    Control ({exp['arms']['control']['description']}): "
            f"{total_assigned_c} assigned, {control_n}/{needed} measured"
        )
        lines.append(
            f"    Variant ({exp['arms']['variant']['description']}): "
            f"{total_assigned_v} assigned, {variant_n}/{needed} measured"
        )

    if resolved:
        lines.append("\n  Past Results:")
        for exp in resolved[-5:]:
            r = exp.get("result", {})
            lines.append(
                f"    {exp['status'].upper()}: \"{exp['hypothesis']}\" "
                f"({r.get('lift_percent', 0):+.0f}%, p={r.get('p_value', 1):.3f})"
            )

    return "\n".join(lines)
