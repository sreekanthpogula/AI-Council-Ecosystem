# CLAUDE.md - Technical Notes for AI-Council

This file contains technical details, architectural decisions, and important implementation notes for future development sessions.

## Project Overview

**AI-Council™** (branding; formerly "LLM Gateway", itself evolved from "LLM Council" - the repo directory and Python project name are still `llm-council`, only the user-facing brand changed) is a boss-routed, multimodal multi-model deliberation system:

1. A **boss model** classifies each incoming request (text, or text + an image/audio/video attachment) into a **capability**: `reasoning`, `acting`, `vision`, or `audio`.
2. The request is dispatched to that capability's **pool** of worker models, which run the original 3-stage council process scoped to just that pool: parallel individual responses (Stage 1) → anonymized peer ranking (Stage 2, skipped if the pool has <2 models) → chairman synthesis (Stage 3).
3. Anonymized peer review remains the key trust mechanism: models don't know whose response they're grading, which prevents playing favorites.

All models are OpenRouter free-tier (`:free`) models, chosen so the whole gateway runs at no cost - including multimodal ones, since OpenRouter's unified `chat/completions` endpoint already exposes image/audio/video input support on qualifying models (no separate Whisper/TTS/video-gen provider keys needed).

## Architecture

### Backend Structure (`backend/`)

**`config.py`**
- `CAPABILITY_POOLS`: dict of `{capability_name: {description, models, chairman}}` - the routing table. Currently: `reasoning`, `acting`, `vision`, `audio`.
- `BOSS_MODEL`: routes requests to a capability, and doubles as the default chairman for every pool - currently `nvidia/nemotron-3-super-120b-a12b:free` (see the Rate Limits/reliability section below for why the largest free model isn't used here)
- `TITLE_MODEL`: separate free model for conversation-title generation (previously hardcoded to a paid model - fixed since that broke the "run for free" goal)
- `AUDIO_OUTPUT_MODEL`: `None` by default - no free OpenRouter model does text-to-speech yet; set to a paid model id to enable spoken responses
- Uses environment variable `OPENROUTER_API_KEY` from `.env`
- Backend runs on **port 8001** (NOT 8000 - user had another app on 8000)

**`openrouter.py`**
- `query_model()`: Single async model query
- `query_models_parallel()`: Parallel queries using `asyncio.gather()`
- `build_user_content()`: Builds the multimodal `content` array for a user message - `image_url` (data URI) for images, `video_url` (data URI) for video, `input_audio` (raw base64 + format) for audio. Falls back to a plain string when there's no attachment.
- Returns dict with 'content' and optional 'reasoning_details'
- Graceful degradation: returns None on failure, continues with successful responses

**`gateway.py`** - The Boss/Router
- `detect_attachment_capability()`: deterministic shortcut - an image/video attachment always routes to `vision`, audio always routes to `audio`. Avoids an extra API call and avoids the boss model misclassifying an obviously visual/audio request.
- `classify_capability()`: for plain text requests, asks `BOSS_MODEL` to pick one of the capabilities described in `CAPABILITY_POOLS`
- `run_gateway_request()`: classifies, then runs the pool-scoped 3-stage council (used by the non-streaming endpoint)

**`council.py`** - Pool-Agnostic Council Logic
- Deliberately takes `models`/`chairman_model` as parameters now (no longer reads hardcoded module-level constants) so the same functions serve every capability pool
- `stage1_collect_responses(user_query, models, attachment_base64, attachment_mime_type)`: parallel queries, attaches multimodal content when present
- `stage2_collect_rankings(user_query, stage1_results, models)`:
  - Anonymizes responses as "Response A, B, C, etc."
  - Creates `label_to_model` mapping for de-anonymization
  - Prompts models to evaluate and rank (with strict format requirements)
  - Returns tuple: (rankings_list, label_to_model_dict)
  - Each ranking includes both raw text and `parsed_ranking` list
  - **Callers skip this stage entirely when a pool has fewer than 2 models** (currently true for `audio`, which has only one free OpenRouter model that accepts audio input) - there's nothing to peer-review
- `stage3_synthesize_final(..., chairman_model)`: Chairman synthesizes from all responses + rankings (rankings section is omitted from the prompt when Stage 2 was skipped)
- `parse_ranking_from_text()`: Extracts "FINAL RANKING:" section, handles both numbered lists and plain format
- `calculate_aggregate_rankings()`: Computes average rank position across all peer evaluations

**`storage.py`** - Dual-Backend (File / Redis)
- **Two backends, chosen automatically at import time**: Upstash Redis (via the `upstash-redis` REST client) when `UPSTASH_REDIS_REST_URL` is set in the environment, otherwise local JSON files under `DATA_DIR`. This is what makes the same codebase work both for local dev (file backend, exactly as before) and for a Vercel deployment (serverless functions have no persistent filesystem, so they need Redis) without an `if VERCEL` special case anywhere else in the app - `main.py`/`gateway.py`/`council.py` all just call the same `storage.*` functions regardless of which backend is active.
- Redis layout: each conversation is a JSON blob at key `conversation:{id}`; a sorted set `conversations:index` (member=id, score=`created_at` as epoch seconds) tracks all conversation ids for `list_conversations()`, sorted newest-first via `zrange(..., rev=True)`.
- `Redis.from_env()` (from the `upstash-redis` package) reads `UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN` directly - these are exactly the env var names Vercel's Upstash integration sets when you connect a Redis database to a project via the Storage tab, so no manual mapping is needed.
- Each conversation: `{id, created_at, title, messages[]}`
- User messages: `{role, content, attachment?: {base64, mime_type, name}}` - attachment is persisted in full so history reloads can re-render it
- Assistant messages: `{role, stage1, stage2, stage3, capability}`
- Note: metadata (label_to_model, aggregate_rankings) is NOT persisted to storage, only returned via API
- Full CRUD: `create_conversation`, `get_conversation`/`list_conversations`, `update_conversation_title` (rename), `delete_conversation` (returns bool)
- Conversation IDs are validated against `^[a-zA-Z0-9_-]+$` before touching either backend (`_validate_id`) - conversation IDs are self-generated uuid4s, but they also arrive as untrusted URL path params on every read/rename/delete call, so this closes a path-traversal hole (file backend: an id of `../../etc/passwd`) and a Redis-key-injection hole (e.g. wildcards/newlines) that `delete_conversation` in particular would otherwise open up. `get_conversation` catches the `ValueError` and returns `None` (preserves the existing 404 behavior) rather than letting it bubble up.

**`main.py`**
- FastAPI app with CORS: always allows `localhost:5173`/`localhost:3000` for local dev, plus whatever's in the comma-separated `ALLOWED_ORIGINS` env var (needed for the deployed frontend's origin - see Vercel section below)
- `SendMessageRequest` accepts optional `attachment_base64` / `attachment_mime_type` / `attachment_name`
- POST `/api/conversations/{id}/message`: routes via `gateway.run_gateway_request()`, returns `{stage1, stage2, stage3, metadata}` where metadata includes `capability`, `label_to_model`, `aggregate_rankings`
- POST `/api/conversations/{id}/message/stream`: same flow but staged as SSE events, with a `routing_start`/`routing_complete` pair emitted before `stage1_start` so the UI can show which capability/pool was chosen
- `PATCH /api/conversations/{id}`: rename (body: `{title}`), 422 if title is blank after trimming, 404 if conversation doesn't exist
- `DELETE /api/conversations/{id}`: delete, 404 if it didn't exist

### Frontend Structure (`frontend/src/`)

**`App.jsx`**
- Main orchestration: manages conversations list and current conversation
- Handles message sending (now takes an optional `attachment` object) and metadata storage
- Handles the new `routing_start`/`routing_complete` SSE events the same way it handles `stage1/2/3_*`
- `handleRenameConversation`/`handleDeleteConversation`: call the API then patch local state directly (`setConversations`) rather than re-fetching the whole list. Deleting the currently-open conversation clears `currentConversationId`/`currentConversation` back to the empty state.
- `handleGoHome`: same as deleting-the-current-conversation's reset logic (clears both id and object back to `null`) - this is what "navigating home" means in this app. There's no `react-router` and no URL change; it's a plain state reset, wired to the sidebar logo's `onClick`. If real URL-based routing is ever wanted (e.g. shareable links to a specific conversation), that's a bigger addition (`react-router-dom` + route params), not implemented here.
- Important: metadata is stored in the UI state for display but not persisted to backend JSON

**`api.js`**
- `fileToBase64()`: reads a `File` into a base64 string (no data-URI prefix)
- `sendMessage()` / `sendMessageStream()` take an optional `attachment: {base64, mimeType, name}` and fold it into the JSON body as `attachment_base64`/`attachment_mime_type`/`attachment_name`
- `renameConversation(id, title)` (PATCH) / `deleteConversation(id)` (DELETE)

**`components/Sidebar.jsx`**
- Full CRUD on conversations: click to select, pencil icon to rename inline (turns the title into a text input; Enter/blur saves, Escape cancels), trash icon to delete (behind a `window.confirm` - deliberately not a custom modal, this is a small local app and the browser confirm is enough)
- Rename/delete action icons are absolutely positioned top-right of each `.conversation-item`, hidden until hover (`.conversation-actions { opacity: 0 }` → `1` on `:hover`)
- Icons are `lucide-react` throughout (see below), not emoji
- Brand/logo (`AI-Council™`) is a `<button>`, not a div - clicking it calls `onGoHome` (passed down from `App.jsx`) to reset to the empty/welcome state. Kept as a plain button-with-reset-styles rather than an `<a>` since there's no actual route to link to.
- App version is read directly from `frontend/package.json`'s `version` field via `import { version } from '../../package.json'` (Vite supports JSON imports out of the box - no build config needed) and rendered under the Karpathy attribution line as `AI-Council™ v{version}`. **Bump `frontend/package.json`'s version (and ideally `pyproject.toml`'s, for consistency) when cutting a new version** - nothing does this automatically.
- Footer: attribution link to [karpathy/llm-council](https://github.com/karpathy/llm-council) (the project this whole app is inspired by/evolved from) - uses `GitFork`, not `Github`, because current `lucide-react` doesn't ship a literal GitHub brand mark, only generic git-themed icons

**`components/ChatInterface.jsx`**
- Multiline textarea (3 rows, resizable) + a `Paperclip`-icon attach button (accepts image/audio/video) that reads the file client-side and holds it as `pendingAttachment` until send
- Enter to send, Shift+Enter for new line
- Renders an inline `<img>`/`<video>`/`<audio>` preview for a user message's attachment
- Note: the input form only renders when `conversation.messages.length === 0` - conversations are single-turn by design (one question per conversation; start a new conversation for a new question)
- `CreditFooter` (local component, not its own file): renders "Built by **Sreekanth Pogula**" bold and centered, gradient-text on the name. Rendered as the last element in both the no-conversation branch and the main return, so it's always visible at the bottom of the chat pane regardless of whether the input form or the input-less (post-first-message) layout is showing.

**`components/RoutingBanner.jsx`** (new)
- Shows which capability the boss model picked (`reasoning`/`acting`/`vision`/`audio`), its description, and which models it routed to
- Falls back to reconstructing a minimal banner from `msg.capability` + `msg.stage1` when reloading a conversation from storage (since the live `routing` SSE payload isn't persisted)

**`components/Stage1.jsx`**
- Tab view of individual model responses
- ReactMarkdown rendering with markdown-content wrapper

**`components/Stage2.jsx`**
- **Critical Feature**: Tab view showing RAW evaluation text from each model
- De-anonymization happens CLIENT-SIDE for display (models receive anonymous labels)
- Shows "Extracted Ranking" below each evaluation so users can validate parsing
- Aggregate rankings shown with average position and vote count
- Explanatory text clarifies that boldface model names are for readability only
- Simply doesn't render when `stage2` is null/empty (single-model pools)

**`components/Stage3.jsx`**
- Final synthesized answer from chairman
- Green-tinted background (#f0fff0) to highlight conclusion

**Styling (`*.css`)** - redesigned for a "web4"/glassmorphic look
- CSS custom properties defined once in `index.css` `:root` (`--accent-1/2/3`, `--gradient-brand`, `--surface-glass`, `--radius-*`, etc.) - every component references these instead of hardcoded colors, so the whole palette can be re-themed from one file
- Dark gradient sidebar (deep indigo/violet) contrasted against a light glass main content area with a subtle animated mesh-gradient body background
- `header.jpg` (copied into `frontend/public/`) is used as a full-bleed hero image with a dark gradient overlay on both empty states (`.hero-empty-state` in `ChatInterface.jsx`) - not used as a small logo, the sidebar brand mark is just a gradient `Zap` icon badge instead since the image is landscape-oriented
- Fonts: Inter (body) + Sora (headings/display), loaded via Google Fonts `<link>` in `index.html`
- Icons: `lucide-react` everywhere (not emoji) - `Brain`/`Wrench`/`Eye`/`Headphones` per capability in `RoutingBanner`, `Pencil`/`Trash2`/`Check`/`X` for the sidebar's rename/delete controls, `Paperclip`/`SendHorizontal` on the composer, `Crown`/`Trophy`/`Medal`/`Vote`/`MessagesSquare` on the stage headers. Before adding a new icon, verify the exact export name exists in the installed package (`node -e "console.log(!!require('lucide-react').SomeName)"`) - names don't always match assumptions (e.g. there's no plain `Github` export, only `GitFork`/`GitBranch`/etc.)
- `RoutingBanner` gets a per-capability gradient badge color (violet=reasoning, pink=vision, gold=audio, cyan/green=acting)
- Stage 3 (chairman) gets a gold/green gradient treatment to visually mark it as the "final answer"; Stage 2's aggregate rankings show a colored `Medal` icon (gold/silver/bronze via `.rank-medal-0/1/2`) for the top 3
- `fade-in-up` keyframe animation (defined in `index.css`) applied to each new message group and stage card as it appears
- Global markdown styling in `index.css` with `.markdown-content` class; code blocks are dark-themed regardless of the rest of the light theme
- 12px padding on all markdown content to prevent cluttered appearance

## Key Design Decisions

### Capability Pools Instead Of A Single Fixed Council
The original council queried the same 4 models for every request regardless of content. The gateway instead maintains separate pools per capability so, e.g., a code question hits code-capable models and an image gets routed to vision-capable models - without needing separate provider integrations, since OpenRouter model metadata (`architecture.input_modalities`) already tells you which models accept image/audio/video.

### Attachments Deterministically Pin The Capability
`gateway.detect_attachment_capability()` is a plain mime-type check, not an LLM call - if there's an image/video attachment, route to `vision`; if audio, route to `audio`. Only plain-text requests go through the boss model's actual classification call. This is cheaper, faster, and removes a whole failure mode (the boss misclassifying an obviously visual request).

### Stage 2 Prompt Format
The Stage 2 prompt is very specific to ensure parseable output:
```
1. Evaluate each response individually first
2. Provide "FINAL RANKING:" header
3. Numbered list format: "1. Response C", "2. Response A", etc.
4. No additional text after ranking section
```

This strict format allows reliable parsing while still getting thoughtful evaluations.

### De-anonymization Strategy
- Models receive: "Response A", "Response B", etc.
- Backend creates mapping: `{"Response A": "openai/gpt-oss-20b:free", ...}`
- Frontend displays model names in **bold** for readability
- Users see explanation that original evaluation used anonymous labels
- This prevents bias while maintaining transparency

### Error Handling Philosophy
- Continue with successful responses if some models fail (graceful degradation)
- Never fail the entire request due to single model failure
- Log errors but don't expose to user unless all models in the routed pool fail

### UI/UX Transparency
- All raw outputs are inspectable via tabs
- Parsed rankings shown below raw text for validation
- Routing banner shows exactly which capability + models were chosen, so routing decisions are auditable too
- Users can verify system's interpretation of model outputs
- This builds trust and allows debugging of edge cases

## Important Implementation Details

### Relative Imports
All backend modules use relative imports (e.g., `from .config import ...`) not absolute imports. This is critical for Python's module system to work correctly when running as `python -m backend.main`.

### Port Configuration
- Backend: 8001 (changed from 8000 to avoid conflict)
- Frontend: 5173 (Vite default)
- Update both `backend/main.py` and `frontend/src/api.js` if changing

### Markdown Rendering
All ReactMarkdown components must be wrapped in `<div className="markdown-content">` for proper spacing. This class is defined globally in `index.css`.

### Model Configuration
Models are hardcoded per-pool in `backend/config.py`'s `CAPABILITY_POOLS`. Every pool's chairman currently defaults to `BOSS_MODEL`, but can be overridden per pool.

### Free-Tier Model Constraints
- Free OpenRouter models (`:free` suffix) are rate-limited: 20 req/min and 50 req/day per model unless you've bought $10+ in credits (raises the daily cap to 1000). A single gateway request can burn through several calls per pool model (stage1 + stage2 + boss classification), so heavy use can hit 429s - handled via the existing graceful-degradation path.
- Only one free model (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`) currently accepts audio input, and **it 402s (Payment Required) specifically on audio-attachment requests** even though it's nominally free - see the `audio` pool comment in config.py. Text-only requests to that model are fine; audio input on it isn't actually free in practice. No free OpenRouter model does audio output (TTS) either - see `AUDIO_OUTPUT_MODEL`.
- `nvidia/nemotron-3-ultra-550b-a55b:free` (the largest free model) was the original `BOSS_MODEL` choice but proved unreliable in testing: intermittent 404s, `'choices'`-missing responses, and JSON parse errors across repeated calls. Swapped to `nvidia/nemotron-3-super-120b-a12b:free`, which was consistently reliable across every test. If you see `BOSS_MODEL`/chairman failures again, suspect the specific model's free-tier deployment stability first, not the routing/council code.
- `query_model()` in `openrouter.py` now retries once (short backoff) on transient failures, but treats 400/401/402/404/422 as permanent and fails fast - retrying a 402 or 404 just wastes time since it'll fail identically.
- **`print()` output is invisible until process exit unless you run with unbuffered output** (`uv run python -u -m backend.main`, or set `PYTHONUNBUFFERED=1`) - Python block-buffers stdout when it's redirected to a file/pipe (as opposed to a TTY), so `Error querying model...` lines can silently sit in a buffer for the life of the process instead of showing up in your log tail. Cost real debugging time before this was caught - always use `-u` when redirecting backend output to a log file.
- Check `https://openrouter.ai/api/v1/models` periodically; free-tier lineup changes often, and modality support (`architecture.input_modalities`/`output_modalities` in that response) is how you find replacements.

### Rate Limits Are The Most Likely Failure Mode Day-To-Day
During the build/test session, heavy testing plus concurrent real usage on the same OpenRouter account exhausted the free-tier rate limit (20 req/min) across every pool model simultaneously - every model returned `429 Too Many Requests` at once, including on the 1-retry backoff. This is not a bug: it's the expected behavior of sharing a free-tier quota across simultaneous testing + real traffic. If stage2/stage3 come back empty/failed right after a successful stage1, check the log for `429` before suspecting the routing/council code.

### Storage Resilience
`main.py`'s `send_message`/`send_message_stream` wrap the final `storage.add_assistant_message()` call in a try/except - a multi-minute gateway run (several sequential free-tier LLM calls) shouldn't 500 and discard its results just because saving at the very end hit a storage hiccup (e.g. the conversation file being gone by the time the request finishes). This was observed once during testing with no clear cause (no delete feature exists in the app) - possibly external interference (antivirus/sync/concurrent process) rather than app logic, but the endpoint is now resilient to it either way.

## Deployment (Vercel)

The repo deploys as **two separate Vercel projects** from the same GitHub repo (`sreekanthpogula/AI-Council-Ecosystem`), each with a different Root Directory - see README.md's "Deploying to Vercel" section for the exact dashboard steps and env vars. Notes specific to *why* it's built this way:

- **`api/index.py`** (repo root) is the only file Vercel's Python runtime needs - it just does `from backend.main import app`. Vercel auto-detects the ASGI `app` object and serves it; no WSGI adapter (e.g. Mangum) is needed for FastAPI on Vercel specifically.
- **`vercel.json`** rewrites every path to that one function (`"/(.*)"` → `/api/index`), so FastAPI's own router still handles `/`, `/api/conversations`, etc. exactly as it does locally - Vercel's filesystem routing alone would only map `api/index.py` to `/api`/`/api/index`, not to the app's other routes, without this rewrite.
- **`requirements.txt`** (repo root) is what Vercel's Python builder installs from - kept deliberately separate from `pyproject.toml`/`uv.lock` (used for local dev via `uv sync`) since Vercel doesn't read uv's files. `uvicorn` is intentionally omitted from it (only needed for local `python -m backend.main`, not for how Vercel invokes the ASGI app) - if you add a new backend dependency, add it to **both** files.
- **Why two Vercel projects instead of one monorepo project**: it maps cleanly onto each side's own framework auto-detection (Vite for `frontend/`, Python for the repo root) without needing a `builds`/legacy-routes array to teach one project about both halves. The tradeoff is CORS is real (different origins) instead of same-origin - that's what `ALLOWED_ORIGINS`/`VITE_API_BASE` are for.
- **The chosen tradeoff on the pipeline/timeout question**: this deployment keeps the existing single-long-request SSE model rather than rewriting to a job-queue + polling design. That means a slow/rate-limited run can still get killed by Vercel's function timeout with no partial result saved - functionally similar to the existing "all models failed" degradation path, just with a different trigger. If this turns out to be a frequent problem in practice (not just theoretical), the fix is to decouple: `POST /message` returns a job id immediately, a background function advances stage1→2→3 writing progress into Redis as it goes, and the frontend polls instead of holding one SSE connection open. That's a substantially bigger change (new job model, polling replaces the SSE handler in `App.jsx`) - deliberately not done up front since it wasn't clear yet whether timeouts would actually bite in practice.

## Common Gotchas

1. **Module Import Errors**: Always run backend as `python -m backend.main` from project root, not from backend directory
2. **CORS Issues**: Frontend must match allowed origins in `main.py` CORS middleware
3. **Ranking Parse Failures**: If models don't follow format, fallback regex extracts any "Response X" patterns in order
4. **Missing Metadata**: Metadata (label_to_model, aggregate_rankings) is ephemeral (not persisted), only available in API responses
5. **Single-model pools**: Don't assume `stage2_results`/`aggregate_rankings` are non-empty - the `audio` pool currently has only one model, so Stage 2 is skipped and the chairman prompt adapts automatically
6. **Config changes require a backend restart**: `config.py` is only read at process start, so editing `CAPABILITY_POOLS`/model ids needs a `uv run python -m backend.main` restart to take effect - it's easy to edit config and then test against the still-running old process

## Future Enhancement Ideas

- Configurable capability pools/chairman via UI instead of config file
- A dedicated `code_execution`/tool-calling loop for the `acting` pool (currently it's still just single-turn text generation, not real tool execution)
- Export conversations to markdown/PDF
- Model performance analytics over time (which capability gets routed to most, which pool's chairman gets overridden most)
- Text-to-speech output once a free OpenRouter audio-output model exists (or wire up `AUDIO_OUTPUT_MODEL` to a paid one)
- Multi-turn conversations (currently intentionally single-turn per conversation)

## Testing Notes

Use `test_openrouter.py` to verify API connectivity and test different model identifiers before adding to a pool. The script tests both streaming and non-streaming modes.

To sanity-check routing without the UI, POST directly to the API:
```bash
curl -X POST http://localhost:8001/api/conversations/<id>/message \
  -H "Content-Type: application/json" \
  -d '{"content": "..."}'
```
Add `attachment_base64`/`attachment_mime_type` to the body to test `vision`/`audio` routing.

## Data Flow Summary

```
User Query (+ optional image/audio/video attachment)
    ↓
Boss Routing: attachment present? → deterministic capability
              else → BOSS_MODEL classifies → capability
    ↓
Capability Pool selected (reasoning / acting / vision / audio)
    ↓
Stage 1: Parallel queries to pool models (multimodal content if attached) → [individual responses]
    ↓
Stage 2 (skipped if pool has <2 models): Anonymize → Parallel ranking queries → [evaluations + parsed rankings]
    ↓
Aggregate Rankings Calculation → [sorted by avg position]
    ↓
Stage 3: Pool chairman synthesis with full context (rankings section omitted if Stage 2 was skipped)
    ↓
Return: {capability, stage1, stage2, stage3, metadata}
    ↓
Frontend: Routing banner + Display with tabs + validation UI
```

The entire flow is async/parallel where possible to minimize latency.
