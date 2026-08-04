# AI-Council™

![ai-council](header.jpg)

AI-Council is a boss-routed, multimodal multi-model AI advisory board. Instead of asking one LLM provider, a **boss model** first figures out what kind of request you're making - reasoning, coding/tool-use, vision, or audio - and routes it to a pool of models suited to that capability. That pool then deliberates the same way the original council did: every model answers, the models anonymously review and rank each other, and a chairman model produces the final synthesized response.

In a bit more detail, here's what happens when you submit a query:

1. **Routing**: an image/audio/video attachment routes deterministically by file type; a plain-text question is classified by the boss model into `reasoning`, `acting`, `vision`, or `audio`.
2. **Stage 1: First opinions**. The query goes to every model in the routed capability's pool, and responses are collected into a tab view so you can inspect each one.
3. **Stage 2: Peer review**. Each model in the pool reviews the others' (anonymized) responses and ranks them - skipped automatically if the pool has only one model, since there's nothing to compare.
4. **Stage 3: Final response**. The pool's chairman model synthesizes everything into one final answer.

All models are OpenRouter free-tier (`:free`) models by default, so the whole thing runs at no cost - see `backend/config.py` for the exact routing table.

## Setup

### 1. Install Dependencies

The project uses [uv](https://docs.astral.sh/uv/) for project management.

**Backend:**
```bash
uv sync
```

**Frontend:**
```bash
cd frontend
npm install
cd ..
```

### 2. Configure API Key

Create a `.env` file in the project root:

```bash
OPENROUTER_API_KEY=sk-or-v1-...
```

Get your API key at [openrouter.ai](https://openrouter.ai/). The default model configuration only uses free-tier models, so no credits are required - though free models are rate-limited (20 req/min, 50-1000 req/day depending on account credit).

### 3. Configure Capability Pools (Optional)

Edit `backend/config.py` to customize which models handle each capability:

```python
CAPABILITY_POOLS = {
    "reasoning": {"models": [...], "chairman": BOSS_MODEL},
    "acting":    {"models": [...], "chairman": BOSS_MODEL},
    "vision":    {"models": [...], "chairman": BOSS_MODEL},
    "audio":     {"models": [...], "chairman": BOSS_MODEL},
}

BOSS_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"  # routes requests + default chairman
```

## Running the Application

**Option 1: Use the start script**
```bash
./start.sh
```

**Option 2: Run manually**

Terminal 1 (Backend):
```bash
uv run python -m backend.main
```

Terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```

Then open http://localhost:5173 in your browser.

## Tech Stack

- **Backend:** FastAPI (Python 3.10+), async httpx, OpenRouter API
- **Frontend:** React + Vite, react-markdown for rendering, lucide-react for icons
- **Storage:** JSON files in `data/conversations/` locally; Upstash Redis when `UPSTASH_REDIS_REST_URL` is set (e.g. on Vercel) - see `backend/storage.py`
- **Package Management:** uv for Python, npm for JavaScript

## Deploying to Vercel

The backend and frontend deploy as **two separate Vercel projects** from this same repo (Vercel supports linking multiple projects to one GitHub repo, each with its own Root Directory).

### Backend project

- **Root Directory:** `.` (the repo root - leave it as-is)
- Vercel auto-detects `api/index.py` as a Python serverless function; it imports and serves the FastAPI app from `backend/main.py` directly. `requirements.txt` at the repo root is what Vercel installs from (kept separate from `pyproject.toml`/`uv.lock`, which are for local dev via `uv`).
- `vercel.json` rewrites every path to that one function, so FastAPI's own router handles `/`, `/api/conversations`, etc. exactly as it does locally.
- **Environment variables to set** (Vercel dashboard → Settings → Environment Variables):
  - `OPENROUTER_API_KEY` - required
  - `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` - required for persistence. Easiest path: Vercel dashboard → Storage → Create Database → pick the Upstash/Redis integration → connect it to this project, which sets both automatically. (Without these, the app falls back to local JSON files - which don't persist on Vercel's serverless filesystem, so conversations would vanish between requests.)
  - `ALLOWED_ORIGINS` - the frontend project's deployed URL(s), comma-separated (e.g. `https://ai-council-frontend.vercel.app`)
- **Function timeout:** the gateway's multi-stage pipeline can take anywhere from ~1 to several minutes on free-tier OpenRouter models, especially under rate limiting. Vercel's default function duration caps vary by plan - raise it as high as your plan allows by adding to `vercel.json`:
  ```json
  { "functions": { "api/index.py": { "maxDuration": 60 } } }
  ```
  Even with this raised, a very slow/rate-limited run can still exceed the limit and the request will fail with no partial result saved - a known tradeoff of running this pipeline as a single serverless request (see CLAUDE.md for the alternative job-queue design if that becomes a problem).

### Frontend project

- **Root Directory:** `frontend`
- Framework preset: Vite (auto-detected)
- **Environment variable:** `VITE_API_BASE` = the backend project's deployed URL (e.g. `https://ai-council-backend.vercel.app`)

## Credits

AI-Council is inspired by and evolved from [karpathy/llm-council](https://github.com/karpathy/llm-council) - Andrej Karpathy's original weekend-hack project exploring multi-model deliberation with anonymized peer review. The core 3-stage idea (individual responses → anonymized peer ranking → chairman synthesis) is his; the boss-routed multimodal gateway, capability pools, CRUD conversation management, and the rest of the product built on top of it are new.

Built by **Sreekanth Pogula**.
