"""
Shared LLM helper — talks to any OpenAI-compatible chat API.
Used by script_analyzer and timeline_builder.
"""

import json
import requests
from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """
    Send a chat completion request and return the assistant's reply text.
    """
    if not LLM_API_KEY:
        raise RuntimeError(
            "LLM_API_KEY is not set. Add it to your .env file."
        )

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def chat_json(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> dict | list:
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
