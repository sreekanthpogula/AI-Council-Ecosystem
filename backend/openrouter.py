"""OpenRouter API client for making LLM requests."""

import httpx
from typing import List, Dict, Any, Optional
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL

# Audio format OpenRouter accepts per mime subtype for the `input_audio` content part.
_AUDIO_MIME_TO_FORMAT = {
    "wav": "wav", "x-wav": "wav",
    "mpeg": "mp3", "mp3": "mp3",
    "aiff": "aiff", "x-aiff": "aiff",
    "aac": "aac",
    "ogg": "ogg",
    "flac": "flac",
    "mp4": "m4a", "m4a": "m4a", "x-m4a": "m4a",
}


def build_user_content(
    text: str,
    attachment_base64: Optional[str] = None,
    attachment_mime_type: Optional[str] = None,
) -> Any:
    """
    Build the `content` value for a user message, attaching an image, audio,
    or video file as an OpenRouter multimodal content part alongside the text.

    Args:
        text: The user's text prompt
        attachment_base64: Raw base64-encoded file data (no data: URI prefix)
        attachment_mime_type: MIME type of the attachment, e.g. "image/png"

    Returns:
        A plain string (no attachment) or a multimodal content part list
    """
    if not attachment_base64 or not attachment_mime_type:
        return text

    category, _, subtype = attachment_mime_type.partition('/')
    parts = [{"type": "text", "text": text}]

    if category == "image":
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{attachment_mime_type};base64,{attachment_base64}"},
        })
    elif category == "video":
        parts.append({
            "type": "video_url",
            "video_url": {"url": f"data:{attachment_mime_type};base64,{attachment_base64}"},
        })
    elif category == "audio":
        audio_format = _AUDIO_MIME_TO_FORMAT.get(subtype, subtype)
        parts.append({
            "type": "input_audio",
            "input_audio": {"data": attachment_base64, "format": audio_format},
        })
    else:
        # Unknown attachment type - fall back to text-only.
        return text

    return parts


# HTTP status codes where retrying is pointless - the request will fail the
# same way every time (bad/unavailable model, no access, wrong payload).
_NON_RETRYABLE_STATUS_CODES = {400, 401, 402, 404, 422}


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    retries: int = 1,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API. Free-tier models are flaky in
    practice (intermittent 5xx/429s, connection resets, malformed bodies), so
    transient failures get one short-backoff retry; permanent failures
    (missing credits, unknown model, bad request) fail fast instead.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds
        retries: Number of retry attempts after a transient failure

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    import asyncio

    attempts = retries + 1
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()

                data = response.json()
                message = data['choices'][0]['message']

                return {
                    'content': message.get('content'),
                    'reasoning_details': message.get('reasoning_details')
                }

        except httpx.HTTPStatusError as e:
            print(f"Error querying model {model}: {e}")
            if e.response.status_code in _NON_RETRYABLE_STATUS_CODES:
                return None
        except Exception as e:
            print(f"Error querying model {model}: {e}")

        if attempt < attempts - 1:
            await asyncio.sleep(1.5 * (attempt + 1))

    return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
