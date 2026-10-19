"""
ai_service.py - the ONLY module that talks to the Gemini API.
The API key is read from Streamlit secrets or an environment variable,
never from the source code.
"""
import json
import os
import re

import streamlit as st

# Tried in this order. You can force a model by adding GEMINI_MODEL = "..." to Secrets.
DEFAULT_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]


class AIError(Exception):
    """Raised with a short, human-readable message when an AI call fails."""


# ---------------------------------------------------------------- configuration
def _secret(name):
    try:
        value = st.secrets.get(name)
    except Exception:          # no secrets file / not configured
        value = None
    return value or os.environ.get(name)


def get_api_key():
    return _secret("GEMINI_API_KEY")


def has_api_key():
    return bool(get_api_key())


def _model_candidates():
    custom = _secret("GEMINI_MODEL")
    models = ([custom] if custom else []) + DEFAULT_MODELS
    unique = []
    for m in models:
        if m and m not in unique:
            unique.append(m)
    return unique


def _client():
    key = get_api_key()
    if not key:
        raise AIError("Gemini API key not found. Add GEMINI_API_KEY in "
                      "Streamlit Cloud: Settings -> Secrets.")
    try:
        from google import genai
    except ImportError:
        raise AIError("The google-genai package is not installed. Check that "
                      "requirements.txt contains google-genai, then reboot the app.")
    return genai.Client(api_key=key)


def _friendly(message):
    low = message.lower()
    if "api key not valid" in low or "api_key_invalid" in low or "invalid api key" in low:
        return "The API key is not valid. Create a new key in Google AI Studio and update Secrets."
    if "429" in low or "quota" in low or "resource_exhausted" in low or "rate" in low:
        return "Rate or daily limit reached on the free tier. Wait a minute and try again."
    if "403" in low or "permission" in low:
        return "This API key does not have permission for that model. Try the model list button."
    if "not found" in low or "404" in low:
        return ("None of the configured models are available for this key. Use 'List models' "
                "and set GEMINI_MODEL in Secrets to one of them.")
    return "AI request failed: " + message[:300]


# ---------------------------------------------------------------- calls
def generate_text(prompt, system=None, temperature=0.5, json_mode=False):
    """Returns (text, model_used). Falls back to the next model if one is unavailable."""
    from google.genai import types

    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json" if json_mode else None,
    )

    last_error = "No response received."
    for model in _model_candidates():
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=config)
            text = response.text
            if text:
                return text, model
            last_error = "The AI returned an empty response."
        except Exception as e:      # noqa: BLE001 - we convert every failure to AIError
            last_error = str(e)
            low = last_error.lower()
            if ("not found" in low or "404" in low or "no longer available" in low
                    or "not supported" in low):
                continue            # try the next model
            break                   # key / quota / network problem: retrying other models won't help
    raise AIError(_friendly(last_error))


def parse_json(text):
    """Parse JSON from a model reply, tolerating ```json fences."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def generate_json(prompt, system=None, temperature=0.4):
    """Returns parsed JSON (dict or list). Raises AIError if the reply is not valid JSON."""
    text, _model = generate_text(prompt, system, temperature, json_mode=True)
    try:
        return parse_json(text)
    except json.JSONDecodeError:
        raise AIError("The AI returned an invalid JSON response. Please try again.")


def list_models():
    """Names of Gemini models this API key can see (for troubleshooting)."""
    client = _client()
    try:
        names = [m.name.replace("models/", "") for m in client.models.list()]
    except Exception as e:          # noqa: BLE001
        raise AIError(_friendly(str(e)))
    return sorted(n for n in names if n.startswith("gemini"))
