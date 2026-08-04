"""FastAPI backend for AI-Council."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uuid
import json
import asyncio

from . import storage
from . import gateway
from .council import (
    generate_conversation_title,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
)
from .config import CAPABILITY_POOLS

app = FastAPI(title="AI-Council API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str
    attachment_base64: Optional[str] = None
    attachment_mime_type: Optional[str] = None
    attachment_name: Optional[str] = None


class UpdateConversationRequest(BaseModel):
    """Request to rename a conversation."""
    title: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "AI-Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.patch("/api/conversations/{conversation_id}", response_model=Conversation)
async def rename_conversation(conversation_id: str, request: UpdateConversationRequest):
    """Rename a conversation."""
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title cannot be empty")

    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    storage.update_conversation_title(conversation_id, title)
    return storage.get_conversation(conversation_id)


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation."""
    deleted = storage.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted", "id": conversation_id}


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(
        conversation_id,
        request.content,
        request.attachment_base64,
        request.attachment_mime_type,
        request.attachment_name,
    )

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Route through the gateway and run the capability-scoped council process
    capability, stage1_results, stage2_results, stage3_result, metadata = await gateway.run_gateway_request(
        request.content,
        request.attachment_base64,
        request.attachment_mime_type,
    )

    # Add assistant message with all stages. A multi-minute gateway run's
    # results shouldn't be thrown away just because the save at the very end
    # hit a storage hiccup (e.g. the conversation file disappearing from
    # under us) - log it and still return what the models produced.
    try:
        storage.add_assistant_message(
            conversation_id,
            stage1_results,
            stage2_results,
            stage3_result,
            capability,
        )
    except ValueError as e:
        print(f"Warning: failed to persist assistant message for conversation {conversation_id}: {e}")

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(
                conversation_id,
                request.content,
                request.attachment_base64,
                request.attachment_mime_type,
                request.attachment_name,
            )

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Routing: boss model picks a capability pool
            yield f"data: {json.dumps({'type': 'routing_start'})}\n\n"
            capability = await gateway.classify_capability(request.content, request.attachment_mime_type)
            pool = CAPABILITY_POOLS[capability]
            models = pool["models"]
            yield f"data: {json.dumps({'type': 'routing_complete', 'data': {'capability': capability, 'description': pool['description'], 'models': models}})}\n\n"

            # Stage 1: Collect responses from the routed pool
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(
                request.content, models, request.attachment_base64, request.attachment_mime_type
            )
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            if not stage1_results:
                stage3_result = {"model": "error", "response": "All models in the routed capability pool failed to respond. Please try again."}
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"
                storage.add_assistant_message(conversation_id, [], [], stage3_result, capability)
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            # Stage 2: Collect rankings (skipped when the pool has only one model)
            stage2_results, label_to_model = [], {}
            if len(stage1_results) >= 2:
                yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
                stage2_results, label_to_model = await stage2_collect_rankings(request.content, stage1_results, models)
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(request.content, stage1_results, stage2_results, pool["chairman"])
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message (see non-streaming endpoint for why
            # this is best-effort - don't drop a completed run over a save failure)
            try:
                storage.add_assistant_message(
                    conversation_id,
                    stage1_results,
                    stage2_results,
                    stage3_result,
                    capability,
                )
            except ValueError as e:
                print(f"Warning: failed to persist assistant message for conversation {conversation_id}: {e}")

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
