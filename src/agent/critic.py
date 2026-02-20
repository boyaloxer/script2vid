"""
Multi-Perspective Critic — Adversarial quality review from distinct viewpoints.

Unlike the old single self-review (one LLM politely grading its own work),
this runs THREE separate review perspectives in parallel, each with a
different goal and persona:

  1. Devil's Advocate — Actively tries to find problems. Asks "why would
     someone scroll past this?" Flags weak hooks, cliche language, and
     structural issues.

  2. Viewer Simulator — Predicts audience reaction. "Would an 18-25 year old
     actually watch this to the end?" Focuses on retention, shareability,
     and emotional resonance.

  3. Style Auditor — Enforces the content prompt ruthlessly. Checks every
     rule in the style guide and flags violations with specific line refs.

The critics DO NOT make the final decision. They produce structured feedback
with severity levels:
  - fatal: Wrong language, plagiarism, hard rule violation → auto-reject
  - concern: Weak element that might hurt performance → brain decides
  - nit: Minor suggestion → informational only

The brain receives all critic feedback and decides whether to ship, revise,
or regenerate based on the full picture (critics + calendar + experiments).
"""

from src.utils.llm import chat_json


def _build_script_context(
    script: str,
    title: str,
    description: str,
    content_prompt: str,
    past_titles: list[str],
) -> str:
    """Build the shared context block all critics receive."""
    parts = [
        f"## Title\n{title}\n",
        f"\n## Script\n{script}\n",
        f"\n## Description\n{description}\n",
    ]
    if past_titles:
        parts.append("\n## Recent Titles (already published)\n")
        for t in past_titles:
            parts.append(f"- {t}\n")
    parts.append(f"\n## Channel Style Guide\n\n{content_prompt}")
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Perspective 1: Devil's Advocate
# ─────────────────────────────────────────────────────────────────────

_DEVILS_ADVOCATE_PROMPT = """\
You are a ruthlessly honest content critic. Your job is to find PROBLEMS
with this script — not to praise it. You are the last line of defense
before this goes live to real viewers.

Think about:
- Would you actually stop scrolling to watch this? Why or why not?
- Is the opening hook strong enough for the first 2 seconds?
- Does the "turn" actually surprise, or is it predictable?
- Is the landing line genuinely memorable, or just trying to be?
- Is there any dead weight — sentences that don't earn their place?
- Would this feel derivative or fresh if you saw it in your feed?

Be specific. Quote the exact words that are weak.

Respond with valid JSON:
{
  "issues": [
    {
      "severity": "fatal|concern|nit",
      "element": "hook|body|turn|landing|title|description|structure|originality",
      "detail": "Specific description of the problem",
      "suggestion": "How to fix it (or null if you just want to flag it)"
    }
  ],
  "overall_impression": "1-2 sentence gut reaction as a viewer",
  "would_you_watch": true/false,
  "confidence": 0.0-1.0
}
"""


# ─────────────────────────────────────────────────────────────────────
# Perspective 2: Viewer Simulator
# ─────────────────────────────────────────────────────────────────────

_VIEWER_SIM_PROMPT = """\
You are simulating a real YouTube viewer — specifically an 18-30 year old
who follows philosophical/introspective short-form content. You're scrolling
through Shorts at midnight. You see this title and start watching.

Predict your honest reaction at each stage:
- First impression of the title (would you tap?)
- First 3 seconds (would you keep watching or swipe?)
- Middle (does it hold attention or does your mind wander?)
- Ending (do you feel something, or was it forgettable?)
- After watching (would you like, comment, share, or follow?)

Don't be generous. Most content is forgettable. Is this one of the rare
ones that would actually make someone pause?

Respond with valid JSON:
{
  "tap_probability": 0.0-1.0,
  "watch_through_rate": 0.0-1.0,
  "emotional_impact": "none|mild|moderate|strong",
  "shareability": "none|low|medium|high",
  "predicted_reaction": "1-2 sentence reaction as a real viewer would think",
  "issues": [
    {
      "severity": "fatal|concern|nit",
      "element": "hook|retention|emotion|title|replay_value",
      "detail": "What would cause a viewer to leave or not engage"
    }
  ],
  "confidence": 0.0-1.0
}
"""


# ─────────────────────────────────────────────────────────────────────
# Perspective 3: Style Auditor
# ─────────────────────────────────────────────────────────────────────

_STYLE_AUDITOR_PROMPT = """\
You are a strict style guide auditor. The content prompt IS the spec.
Every rule in it is a hard requirement, not a suggestion.

Go through the style guide section by section and check compliance:
- Voice & Tone: Is it second person? Conversational? No academic words?
- Structure: Opening hook → expansion → turn → landing?
- Topic: Does it fit the themes that work? Does it avoid themes that don't?
- Title: Correct length? Title case? No questions? Declarative/provocative?
- Description: 1-2 sentences + hashtags? No CTAs?
- Word count: Within 80-110 words?
- Don't rules: No preaching? No motivational language? No resolved tension?
  No lists? Doesn't start with a question?

Be pedantic. If the style guide says "4-6 words" for titles and this one
has 7, flag it. Rules are rules.

Respond with valid JSON:
{
  "violations": [
    {
      "severity": "fatal|concern|nit",
      "rule": "Which style guide rule was violated",
      "detail": "Specific violation description",
      "quote": "The offending text (or null)"
    }
  ],
  "word_count": N,
  "title_word_count": N,
  "compliance_score": 0.0-1.0,
  "confidence": 0.0-1.0
}
"""


# ─────────────────────────────────────────────────────────────────────
# Run all critics
# ─────────────────────────────────────────────────────────────────────

def run_critics(
    script: str,
    title: str,
    description: str,
    content_prompt: str,
    past_titles: list[str],
) -> dict:
    """
    Run all three critic perspectives and aggregate their feedback.

    Returns a structured report the brain can use to decide whether to
    ship, revise, or regenerate.
    """
    context = _build_script_context(script, title, description, content_prompt, past_titles)

    # Run all three perspectives (sequential — could be parallelized later)
    devils_advocate = _safe_critic_call("devils_advocate", _DEVILS_ADVOCATE_PROMPT, context)
    viewer_sim = _safe_critic_call("viewer_simulator", _VIEWER_SIM_PROMPT, context)
    style_audit = _safe_critic_call("style_auditor", _STYLE_AUDITOR_PROMPT, context)

    # Aggregate all issues by severity
    all_issues = []

    for issue in devils_advocate.get("issues", []):
        issue["source"] = "devils_advocate"
        all_issues.append(issue)

    for issue in viewer_sim.get("issues", []):
        issue["source"] = "viewer_simulator"
        all_issues.append(issue)

    for violation in style_audit.get("violations", []):
        all_issues.append({
            "source": "style_auditor",
            "severity": violation.get("severity", "concern"),
            "element": violation.get("rule", "style"),
            "detail": violation.get("detail", ""),
            "quote": violation.get("quote"),
        })

    fatal_issues = [i for i in all_issues if i["severity"] == "fatal"]
    concerns = [i for i in all_issues if i["severity"] == "concern"]
    nits = [i for i in all_issues if i["severity"] == "nit"]

    # Auto-reject only on fatal issues (hard rule violations)
    auto_reject = len(fatal_issues) > 0

    # Suggested title revision from devil's advocate
    revised_title = None
    for issue in devils_advocate.get("issues", []):
        if issue.get("element") == "title" and issue.get("suggestion"):
            revised_title = issue["suggestion"]
            break

    return {
        "auto_reject": auto_reject,
        "fatal_count": len(fatal_issues),
        "concern_count": len(concerns),
        "nit_count": len(nits),
        "fatal_issues": fatal_issues,
        "concerns": concerns,
        "nits": nits,
        "perspectives": {
            "devils_advocate": {
                "would_watch": devils_advocate.get("would_you_watch"),
                "impression": devils_advocate.get("overall_impression"),
                "confidence": devils_advocate.get("confidence"),
            },
            "viewer_simulator": {
                "tap_probability": viewer_sim.get("tap_probability"),
                "watch_through_rate": viewer_sim.get("watch_through_rate"),
                "emotional_impact": viewer_sim.get("emotional_impact"),
                "shareability": viewer_sim.get("shareability"),
                "predicted_reaction": viewer_sim.get("predicted_reaction"),
                "confidence": viewer_sim.get("confidence"),
            },
            "style_auditor": {
                "compliance_score": style_audit.get("compliance_score"),
                "word_count": style_audit.get("word_count"),
                "title_word_count": style_audit.get("title_word_count"),
                "confidence": style_audit.get("confidence"),
            },
        },
        "revised_title": revised_title,
    }


def _safe_critic_call(name: str, prompt: str, context: str) -> dict:
    """Run a single critic, returning empty dict on failure."""
    try:
        return chat_json(prompt, context, temperature=1.0)
    except Exception as e:
        print(f"  [Critic:{name}] Failed: {e}")
        return {}


def critic_report_to_text(report: dict) -> str:
    """Convert a critic report to human-readable text for the session log and brain."""
    lines = [
        f"Critic Report: {report['fatal_count']} fatal, "
        f"{report['concern_count']} concerns, {report['nit_count']} nits"
    ]

    if report["auto_reject"]:
        lines.append("VERDICT: AUTO-REJECT (fatal issues found)")
        for issue in report["fatal_issues"]:
            lines.append(f"  FATAL [{issue['source']}] {issue['detail']}")
    else:
        lines.append("VERDICT: No fatal issues — brain decides")

    da = report["perspectives"]["devils_advocate"]
    if da.get("impression"):
        watch = "would watch" if da.get("would_watch") else "would NOT watch"
        lines.append(f"  Devil's Advocate ({watch}): {da['impression']}")

    vs = report["perspectives"]["viewer_simulator"]
    if vs.get("predicted_reaction"):
        lines.append(
            f"  Viewer Sim (tap={vs.get('tap_probability', '?')}, "
            f"watch={vs.get('watch_through_rate', '?')}, "
            f"impact={vs.get('emotional_impact', '?')}): "
            f"{vs['predicted_reaction']}"
        )

    sa = report["perspectives"]["style_auditor"]
    if sa.get("compliance_score") is not None:
        lines.append(f"  Style Auditor: {sa['compliance_score']:.0%} compliant")

    if report["concerns"]:
        lines.append(f"\n  Top concerns:")
        for c in report["concerns"][:5]:
            lines.append(f"    [{c['source']}] {c['detail']}")

    return "\n".join(lines)
