"""
Shared LLM helper — talks to any OpenAI-compatible chat API.
Used by script_analyzer, timeline_builder, agent brain, and script generator.
"""

import json
import requests
from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL  # noqa: E402
from src.utils.retry import retry


def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float | None = None,
    max_tokens: int = 32768,
) -> str:
    """
    Send a chat completion request and return the assistant's reply text.

    Note: Kimi K2.5 only accepts temperature=0.6 (non-thinking) or 1.0 (thinking).
    If temperature is None, it is omitted so the API uses its default.

    max_tokens prevents silent output truncation — Kimi K2.5 may default to a
    very low output cap if not set explicitly. 16384 is safe for most responses.
    """
    if not LLM_API_KEY:
        raise RuntimeError(
            "LLM_API_KEY is not set. Add it to your .env file."
        )

    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
    }

    # Only include temperature if explicitly set (Kimi K2.5 rejects arbitrary values)
    if temperature is not None:
        body["temperature"] = temperature

    return _post_chat(body)


@retry(max_attempts=3, base_delay=5.0, max_delay=60.0,
       exceptions=(requests.RequestException, KeyError, json.JSONDecodeError))
def _post_chat(body: dict) -> str:
    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=600,
    )
    response.raise_for_status()
    data = response.json()

    # Track token usage if the API returns it
    usage = data.get("usage")
    if usage:
        try:
            from src.utils.quota_tracker import record_llm_tokens
            record_llm_tokens(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
        except Exception:
            pass  # quota tracking should never crash the pipeline

    return data["choices"][0]["message"]["content"]


def chat_json(system_prompt: str, user_prompt: str, temperature: float | None = None) -> dict | list:
    """
    Same as chat(), but parses the response as JSON.
    The system prompt should instruct the model to respond with valid JSON only.
    """
    raw = chat(system_prompt, user_prompt, temperature)

    # Strip markdown code fences if the model wraps its output
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (```json or ```)
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    return json.loads(cleaned)
