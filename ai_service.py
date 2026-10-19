"""
ai_service.py - the ONLY module that talks to the Gemini API.
The API key is read from Streamlit secrets or an environment variable,
never from the source code.
"""
import json
import os
import re
import time

import streamlit as st

# Tried in this order. You can force a model by adding GEMINI_MODEL = "..." to Secrets.
DEFAULT_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
ATTEMPTS_PER_MODEL = 2      # one retry when the server is temporarily busy
MAX_OUTPUT_TOKENS = 8192    # long explanations and quizzes must not be cut off
FENCE = chr(96) * 3         # three backtick characters (markdown code fence)


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


# ---------------------------------------------------------------- error classification
def _is_missing(low):
    return ("not found" in low or "404" in low or "no longer available" in low
            or "not supported" in low)


def _is_quota(low):
    return "429" in low or "quota" in low or "resource_exhausted" in low


def _is_transient(low):
    return ("503" in low or "502" in low or "504" in low or "unavailable" in low
            or "overloaded" in low or "high demand" in low
            or "deadline_exceeded" in low or "timed out" in low or "timeout" in low)


def _friendly(message):
    low = message.lower()
    if "api key not valid" in low or "api_key_invalid" in low or "invalid api key" in low:
        return "The API key is not valid. Create a new key in Google AI Studio and update Secrets."
    if _is_transient(low):
        return ("Gemini is busy right now (temporary high demand on Google's servers). "
                "Wait about 30 seconds and click the button again.")
    if _is_quota(low):
        return "Rate or daily limit reached on the free tier. Wait a minute and try again."
    if "403" in low or "permission" in low:
        return "This API key does not have permission for that model. Try the model list button."
    if _is_missing(low):
        return ("None of the configured models are available for this key. Use 'List models' "
                "and set GEMINI_MODEL in Secrets to one of them.")
    return "AI request failed: " + message[:300]


# ---------------------------------------------------------------- calls
def generate_text(prompt, system=None, temperature=0.5, json_mode=False):
    """Returns (text, model_used).
    Busy server  -> retry once, then try the next model.
    Quota / model not found -> try the next model.
    Bad key / permission -> stop immediately."""
    from google.genai import types

    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        response_mime_type="application/json" if json_mode else None,
    )

    last_error = "No response received."
    fatal = False
    for model in _model_candidates():
        for attempt in range(ATTEMPTS_PER_MODEL):
            try:
                response = client.models.generate_content(
                    model=model, contents=prompt, config=config)
                text = response.text
                if text:
                    return text, model
                last_error = "The AI returned an empty response."
            except Exception as e:      # noqa: BLE001 - every failure becomes AIError below
                last_error = str(e)
                low = last_error.lower()
                if _is_missing(low) or _is_quota(low):
                    break               # this model will not work: try the next one
                if _is_transient(low):
                    if attempt < ATTEMPTS_PER_MODEL - 1:
                        time.sleep(2 * (attempt + 1))
                        continue        # retry the same model once
                    break               # still busy: try the next model
                fatal = True            # key / permission / unknown problem
                break
        if fatal:
            break
    raise AIError(_friendly(last_error))


def parse_json(text):
    """Parse JSON from a model reply. Tolerates markdown fences, raw line breaks inside
    strings, and extra words before or after the JSON. Raises json.JSONDecodeError."""
    cleaned = text.strip()
    cleaned = re.sub("^" + FENCE + r"(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*" + FENCE + "$", "", cleaned)
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass
    # Fallback: take the outermost {...} or [...] block
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(open_c), cleaned.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1], strict=False)
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("No valid JSON found", cleaned, 0)


def generate_json(prompt, system=None, temperature=0.4):
    """Returns parsed JSON (dict or list). Asks again once if the reply is unreadable.
    Raises AIError if it still fails."""
    for _attempt in range(2):
        text, _model = generate_text(prompt, system, temperature, json_mode=True)
        try:
            return parse_json(text)
        except json.JSONDecodeError:
            continue
    raise AIError("The AI returned an invalid JSON response twice. Please try again.")


def list_models():
    """Names of Gemini models this API key can see (for troubleshooting)."""
    client = _client()
    try:
        names = [m.name.replace("models/", "") for m in client.models.list()]
    except Exception as e:          # noqa: BLE001
        raise AIError(_friendly(str(e)))
    return sorted(n for n in names if n.startswith("gemini"))
