"""Configuration for the LLM Gateway."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"

# ---------------------------------------------------------------------------
# LLM Gateway
#
# A "boss" model classifies each incoming request into a capability, then the
# request is routed to that capability's pool of worker models. Workers run
# in parallel (Stage 1), anonymously peer-review each other (Stage 2, skipped
# when a pool has only one model since there's nothing to compare against),
# and a chairman model per-pool synthesizes the final answer (Stage 3) - this
# mirrors the original LLM Council's 3-stage design, just scoped per pool.
#
# All models below are OpenRouter free-tier (":free") models so the gateway
# runs at no cost. Free models are rate-limited (20 req/min, 50 req/day per
# model unless you've bought $10+ in OpenRouter credits, which raises the
# daily cap to 1000) and may be used by the provider for training/logging -
# see https://openrouter.ai/docs/faq#data-policy
# ---------------------------------------------------------------------------

# Boss/router model: classifies which capability pool should handle a request.
# Also doubles as the default chairman for text-based pools.
# NOTE: nvidia/nemotron-3-ultra-550b-a55b:free (the largest free model) was
# tried here first but proved unreliable in testing - intermittent 404s, JSON
# parse errors, and missing 'choices' fields, likely an unstable free-tier
# deployment. nemotron-3-super-120b has been consistently reliable instead.
BOSS_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

# Model used for the (cheap) conversation title generation.
TITLE_MODEL = "openai/gpt-oss-20b:free"

CAPABILITY_POOLS = {
    # General reasoning / Q&A - the "classic" council behavior.
    "reasoning": {
        "description": "General knowledge, reasoning, and open-ended questions",
        "models": [
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "inclusionai/ling-3.0-flash:free",
            "nvidia/nemotron-nano-9b-v2:free",
        ],
        "chairman": BOSS_MODEL,
    },
    # Coding / tool-use / multi-step agentic tasks.
    "acting": {
        "description": "Writing or debugging code, using tools, multi-step tasks",
        "models": [
            "cohere/north-mini-code:free",
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ],
        "chairman": BOSS_MODEL,
    },
    # Image / video understanding.
    "vision": {
        "description": "Understanding images or video content",
        "models": [
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-nano-12b-v2-vl:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        ],
        "chairman": BOSS_MODEL,
    },
    # Speech / audio understanding.
    # Only one free OpenRouter model currently accepts audio input, so this
    # pool runs with a single worker - Stage 2 peer ranking is skipped
    # automatically in that case (see gateway.py).
    # NOTE: in testing this model returned 402 Payment Required specifically
    # when sent an audio attachment (text-only requests to it are fine) -
    # despite the ":free" suffix, OpenRouter doesn't appear to cover audio
    # input for free on this model. Kept as the pool's only option since no
    # free alternative currently exists; the gateway degrades gracefully
    # ("all models failed to respond") rather than crashing when this happens.
    "audio": {
        "description": "Understanding speech or audio content",
        "models": [
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        ],
        "chairman": BOSS_MODEL,
    },
}

# No free OpenRouter model currently supports audio *output* (text-to-speech).
# Set this to a paid model id (e.g. "openai/gpt-audio") to enable spoken
# responses; leave as None to keep the gateway text-only-out and fully free.
AUDIO_OUTPUT_MODEL = None
