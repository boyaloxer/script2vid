"""
Shared LLM helper — talks to any OpenAI-compatible chat API.
Used by script_analyzer and timeline_builder.
"""

import json
import requests
from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


def chat(system_prompt: str, user_prompt: str, temperature: float | None = None) -> str:
    """
    Send a chat completion request and return the assistant's reply text.

    Note: Kimi K2.5 only accepts temperature=0.6 (non-thinking) or 1.0 (thinking).
    If temperature is None, it is omitted so the API uses its default.
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
    }

    # Only include temperature if explicitly set (Kimi K2.5 rejects arbitrary values)
    if temperature is not None:
        body["temperature"] = temperature

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


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
