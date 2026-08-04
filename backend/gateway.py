"""LLM Gateway: boss-model routing into capability-scoped councils.

Flow for every request:
1. classify_capability() - the boss model (or a deterministic attachment
   check) decides which capability pool should handle the request:
   reasoning, acting, vision, or audio.
2. The chosen pool's models run the same 3-stage council process as the
   original LLM Council (stage1 individual responses -> stage2 anonymized
   peer rankings -> stage3 chairman synthesis), scoped to just that pool.
   Stage 2 is skipped when the pool has fewer than 2 models, since there's
   nothing to compare.
"""

from typing import Any, Dict, List, Optional, Tuple

from . import council
from .config import CAPABILITY_POOLS, BOSS_MODEL
from .openrouter import query_model

DEFAULT_CAPABILITY = "reasoning"

ROUTING_PROMPT = """You are the routing boss of a multi-model AI gateway. Classify the user's request into exactly one capability below, based on what kind of work it needs:

{pool_descriptions}

Respond with ONLY the single capability word - nothing else.

User request: {query}"""


def detect_attachment_capability(attachment_mime_type: Optional[str]) -> Optional[str]:
    """
    Attachments deterministically pin the capability - no need to ask the
    boss model when we already know the request involves an image/video/audio
    file (and doing it deterministically avoids an extra API call + the
    possibility the boss misclassifies an obviously visual/audio request).
    """
    if not attachment_mime_type:
        return None
    category = attachment_mime_type.partition('/')[0]
    if category in ("image", "video"):
        return "vision"
    if category == "audio":
        return "audio"
    return None


async def classify_capability(
    user_query: str,
    attachment_mime_type: Optional[str] = None,
) -> str:
    """Decide which capability pool should handle this request."""
    forced = detect_attachment_capability(attachment_mime_type)
    if forced:
        return forced

    pool_descriptions = "\n".join(
        f"- {name}: {pool['description']}" for name, pool in CAPABILITY_POOLS.items()
    )
    prompt = ROUTING_PROMPT.format(pool_descriptions=pool_descriptions, query=user_query)
    response = await query_model(BOSS_MODEL, [{"role": "user", "content": prompt}], timeout=30.0)

    if response is None:
        return DEFAULT_CAPABILITY

    choice = (response.get("content") or "").strip().lower()
    for capability in CAPABILITY_POOLS:
        if capability in choice:
            return capability

    return DEFAULT_CAPABILITY


async def run_gateway_request(
    user_query: str,
    attachment_base64: Optional[str] = None,
    attachment_mime_type: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Route a request through the gateway and run the capability-scoped council.

    Returns:
        Tuple of (capability, stage1_results, stage2_results, stage3_result, metadata)
    """
    capability = await classify_capability(user_query, attachment_mime_type)
    pool = CAPABILITY_POOLS[capability]
    models = pool["models"]

    stage1_results = await council.stage1_collect_responses(
        user_query, models, attachment_base64, attachment_mime_type
    )

    if not stage1_results:
        return capability, [], [], {
            "model": "error",
            "response": "All models in the routed capability pool failed to respond. Please try again."
        }, {"capability": capability}

    stage2_results: List[Dict[str, Any]] = []
    label_to_model: Dict[str, str] = {}
    if len(stage1_results) >= 2:
        stage2_results, label_to_model = await council.stage2_collect_rankings(
            user_query, stage1_results, models
        )

    aggregate_rankings = council.calculate_aggregate_rankings(stage2_results, label_to_model)

    stage3_result = await council.stage3_synthesize_final(
        user_query, stage1_results, stage2_results, pool["chairman"]
    )

    metadata = {
        "capability": capability,
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings,
    }

    return capability, stage1_results, stage2_results, stage3_result, metadata
