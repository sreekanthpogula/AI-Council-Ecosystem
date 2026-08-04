"""Conversation storage.

Two backends, selected automatically:
- Upstash Redis (via the `upstash-redis` REST client), when Upstash REST
  credentials are found under any of a few known env var naming conventions
  (see _UPSTASH_ENV_VAR_PAIRS) - this is what Vercel serverless functions
  need, since they have no persistent local filesystem.
- Local JSON files under DATA_DIR, otherwise - keeps local development working
  exactly as before without requiring an Upstash database.

Only the raw get/save/list-ids/delete primitives differ between backends;
everything else (message shape, attachment handling, etc.) is shared.
"""

import json
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR

# Conversation IDs are always uuid4 strings we generate ourselves, but since
# they also arrive as untrusted URL path params (GET/PATCH/DELETE), validate
# before building a filesystem path or Redis key from one - otherwise
# something like "../../etc/passwd" (file backend) or a wildcard/newline
# (Redis backend) as an id could do something unintended.
_CONVERSATION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

# Vercel's Upstash marketplace integration doesn't always name these
# UPSTASH_REDIS_REST_URL/_TOKEN like `Redis.from_env()` expects - depending on
# the prefix chosen (or defaulted to) when connecting the database in the
# Storage tab, it can come out as e.g. UPSTASH_REDIS_REST_KV_REST_API_URL
# (the "KV_REST_API_*" part is the legacy Vercel KV naming the integration
# still uses under the hood). Check known variants rather than assuming one.
_UPSTASH_ENV_VAR_PAIRS = [
    ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"),
    ("KV_REST_API_URL", "KV_REST_API_TOKEN"),
    ("UPSTASH_REDIS_REST_KV_REST_API_URL", "UPSTASH_REDIS_REST_KV_REST_API_TOKEN"),
]


def _find_upstash_credentials() -> Optional[tuple]:
    for url_var, token_var in _UPSTASH_ENV_VAR_PAIRS:
        url, token = os.getenv(url_var), os.getenv(token_var)
        if url and token:
            return url, token
    return None


_USE_REDIS = _find_upstash_credentials() is not None
_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is None:
        from upstash_redis import Redis
        url, token = _find_upstash_credentials()
        _redis_client = Redis(url, token)
    return _redis_client


_CONVERSATION_KEY_PREFIX = "conversation:"
_INDEX_KEY = "conversations:index"  # sorted set: member=id, score=created_at epoch seconds


def _validate_id(conversation_id: str):
    if not _CONVERSATION_ID_PATTERN.match(conversation_id):
        raise ValueError(f"Invalid conversation id: {conversation_id}")


def _epoch(iso_timestamp: str) -> float:
    return datetime.fromisoformat(iso_timestamp).timestamp()


# ---------------------------------------------------------------------------
# Raw backend primitives
# ---------------------------------------------------------------------------

def _raw_get(conversation_id: str) -> Optional[Dict[str, Any]]:
    if _USE_REDIS:
        raw = _redis().get(_CONVERSATION_KEY_PREFIX + conversation_id)
        return json.loads(raw) if raw else None

    path = _file_path(conversation_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def _raw_save(conversation: Dict[str, Any]):
    if _USE_REDIS:
        _redis().set(_CONVERSATION_KEY_PREFIX + conversation["id"], json.dumps(conversation))
        _redis().zadd(_INDEX_KEY, {conversation["id"]: _epoch(conversation["created_at"])})
        return

    ensure_data_dir()
    path = _file_path(conversation["id"])
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)


def _raw_delete(conversation_id: str) -> bool:
    if _USE_REDIS:
        removed = _redis().delete(_CONVERSATION_KEY_PREFIX + conversation_id)
        _redis().zrem(_INDEX_KEY, conversation_id)
        return bool(removed)

    path = _file_path(conversation_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


def _raw_list_ids() -> List[str]:
    if _USE_REDIS:
        # Newest first.
        return _redis().zrange(_INDEX_KEY, 0, -1, rev=True)

    ensure_data_dir()
    return [
        filename[:-len('.json')]
        for filename in os.listdir(DATA_DIR)
        if filename.endswith('.json')
    ]


def _file_path(conversation_id: str) -> str:
    _validate_id(conversation_id)
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


def ensure_data_dir():
    """Ensure the local data directory exists (file backend only)."""
    if not _USE_REDIS:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    _validate_id(conversation_id)

    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": []
    }

    _raw_save(conversation)
    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a conversation from storage.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        Conversation dict or None if not found
    """
    try:
        _validate_id(conversation_id)
    except ValueError:
        return None

    return _raw_get(conversation_id)


def save_conversation(conversation: Dict[str, Any]):
    """
    Save a conversation to storage.

    Args:
        conversation: Conversation dict to save
    """
    _validate_id(conversation['id'])
    _raw_save(conversation)


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only), newest first.

    Returns:
        List of conversation metadata dicts
    """
    conversations = []
    for conversation_id in _raw_list_ids():
        data = _raw_get(conversation_id)
        if data is None:
            continue
        conversations.append({
            "id": data["id"],
            "created_at": data["created_at"],
            "title": data.get("title", "New Conversation"),
            "message_count": len(data["messages"])
        })

    # The Redis backend already returns ids newest-first; the file backend
    # doesn't have an inherent order, so sort explicitly either way.
    conversations.sort(key=lambda x: x["created_at"], reverse=True)

    return conversations


def add_user_message(
    conversation_id: str,
    content: str,
    attachment_base64: Optional[str] = None,
    attachment_mime_type: Optional[str] = None,
    attachment_name: Optional[str] = None,
):
    """
    Add a user message to a conversation.

    Args:
        conversation_id: Conversation identifier
        content: User message content
        attachment_base64: Optional base64-encoded image/audio/video attachment
        attachment_mime_type: MIME type of the attachment, if any
        attachment_name: Original filename of the attachment, if any
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    message = {"role": "user", "content": content}
    if attachment_base64 and attachment_mime_type:
        message["attachment"] = {
            "base64": attachment_base64,
            "mime_type": attachment_mime_type,
            "name": attachment_name,
        }

    conversation["messages"].append(message)

    save_conversation(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    capability: Optional[str] = None,
):
    """
    Add an assistant message with all 3 stages to a conversation.

    Args:
        conversation_id: Conversation identifier
        stage1: List of individual model responses
        stage2: List of model rankings
        stage3: Final synthesized response
        capability: Which gateway capability pool handled this request
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "assistant",
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "capability": capability,
    })

    save_conversation(conversation)


def update_conversation_title(conversation_id: str, title: str):
    """
    Update the title of a conversation.

    Args:
        conversation_id: Conversation identifier
        title: New title for the conversation
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["title"] = title
    save_conversation(conversation)


def delete_conversation(conversation_id: str) -> bool:
    """
    Delete a conversation.

    Args:
        conversation_id: Conversation identifier

    Returns:
        True if a conversation was deleted, False if it didn't exist
    """
    try:
        _validate_id(conversation_id)
    except ValueError:
        return False

    return _raw_delete(conversation_id)
