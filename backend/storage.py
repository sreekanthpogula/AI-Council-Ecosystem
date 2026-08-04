"""JSON-based storage for conversations."""

import json
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR

# Conversation IDs are always uuid4 strings we generate ourselves, but since
# they also arrive as untrusted URL path params (GET/PATCH/DELETE), validate
# before building a filesystem path from one - otherwise something like
# "../../etc/passwd" as an id would let a request read or delete arbitrary
# files. This matters more now that delete_conversation() exists.
_CONVERSATION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


def ensure_data_dir():
    """Ensure the data directory exists."""
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_conversation_path(conversation_id: str) -> str:
    """Get the file path for a conversation.

    Raises:
        ValueError: if conversation_id isn't a safe, simple identifier
    """
    if not _CONVERSATION_ID_PATTERN.match(conversation_id):
        raise ValueError(f"Invalid conversation id: {conversation_id}")
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    ensure_data_dir()

    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": []
    }

    # Save to file
    path = get_conversation_path(conversation_id)
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)

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
        path = get_conversation_path(conversation_id)
    except ValueError:
        return None

    if not os.path.exists(path):
        return None

    with open(path, 'r') as f:
        return json.load(f)


def save_conversation(conversation: Dict[str, Any]):
    """
    Save a conversation to storage.

    Args:
        conversation: Conversation dict to save
    """
    ensure_data_dir()

    path = get_conversation_path(conversation['id'])
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only).

    Returns:
        List of conversation metadata dicts
    """
    ensure_data_dir()

    conversations = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            with open(path, 'r') as f:
                data = json.load(f)
                # Return metadata only
                conversations.append({
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "title": data.get("title", "New Conversation"),
                    "message_count": len(data["messages"])
                })

    # Sort by creation time, newest first
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
    Delete a conversation's storage file.

    Args:
        conversation_id: Conversation identifier

    Returns:
        True if a conversation was deleted, False if it didn't exist
    """
    try:
        path = get_conversation_path(conversation_id)
    except ValueError:
        return False

    if not os.path.exists(path):
        return False

    os.remove(path)
    return True
