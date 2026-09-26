"""
Ollama client — the AI Copilot's local-LLM gateway.

Calls a local Ollama server (host/model from app.core.config Settings:
OLLAMA_HOST / OLLAMA_MODEL) for exactly two purposes:

  * classify_intent()  — decide whether a free-text input is COMMAND-like
    (an actionable instruction for the virtual org) or QUERY-like (a
    question about infrastructure/ITSM state). This ASSISTS the existing
    deterministic keyword routing in command_center_service; it never
    replaces it as the sole decision maker.

  * summarize_context() — turn a context dict (CommandCenterOverview or
    repo query results) into natural language. The system prompt hard-
    forbids inventing facts/numbers not present in the context.

Failure policy: every HTTP call is wrapped. On connection errors,
timeouts, non-200s, or malformed/JSON-invalid model output the functions
return a clearly-marked fallback value (fallback_used=True / deterministic
fallback string). NOTHING here raises into the route layer, and NOTHING
here fabricates domain facts when the LLM is unavailable.

Timeouts: intent classification is short (12s); summarization gets longer
(90s) because the context payload can be large on a busy tenant.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Literal

# NOTE: `httpx` is intentionally still imported even though this module no
# longer performs HTTP calls directly (the AI Gateway's Ollama adapter does).
# The existing test suite patches `ollama_client.httpx.AsyncClient`, which is
# the shared httpx module object — that patch also covers the adapter's
# call-time `httpx.AsyncClient(...)` lookup. Removing this import would break
# the established mocking contract of test_ollama_model_routing.py /
# test_ollama_timeout.py.
import httpx  # noqa: F401  (backward-compat patch point for tests)
from pydantic import BaseModel, Field

from app.core.config import get_settings

# AI Gateway integration — ollama_client now delegates to the
# provider-agnostic gateway while preserving all existing public behavior.
from app.ai import get_gateway
from app.ai.schemas import AITier, AIMessage, AIRequest
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Timeouts are generous by design: qwen3.6:latest is a ~36B MoE (~24GB on
# disk) that may be cold-loaded from disk on first request, and on a
# 12GB-VRAM box most weights run on CPU. A missed-but-inflight first call
# is much better than a guaranteed timeout storm on every cold start.
#
# Effective values read from Settings (OLLAMA_TIMEOUT_SECONDS /
# OLLAMA_SUMMARY_TIMEOUT_SECONDS) so operators can tune for their host.
def _intent_timeout() -> float:
    s = get_settings()
    return getattr(s, "ollama_timeout_seconds", 60.0) or 60.0


def _summary_timeout() -> float:
    s = get_settings()
    # Prefer the summary-specific setting, falling back to the generic one,
    # then a safe default. Kept below the frontend 3-minute client deadline.
    summary = getattr(s, "ollama_summary_timeout_seconds", None)
    if summary:
        return float(summary)
    generic = getattr(s, "ollama_timeout_seconds", None)
    return float(generic) if generic else 150.0

# Output-token caps (num_predict). qwen3.6 is a reasoning ("thinking")
# model: on a CPU-bound host it generates at only ~10-15 tok/s, and with no
# cap it burns 1000+ thinking tokens before the JSON/answer — measured
# 1288 tokens / 88.7s for a 34-token intent prompt. Capping generation is
# what actually keeps both calls inside their deadlines (the system prompt
# already demands concise output, so this aligns with the existing contract;
# 128 tokens is far more than the intent schema needs, 512 keeps summaries
# short-but-complete).
_INTENT_NUM_PREDICT = 128
_SUMMARY_NUM_PREDICT = 512


class AIModelTier(str, Enum):
    """Which capability tier an existing AI request belongs to.

    The tier is derived from DETERMINISTIC application metadata (the call
    site's own purpose / the request's existing intent type) - never from
    another LLM call and never by re-classifying arbitrary user prompts:

      UTILITY  - small, structured, latency-sensitive work: intent
                 classification, concise status/summary responses,
                 formatting/normalization. Uses AI_FAST_MODEL when configured.
      STANDARD - ordinary generation that is neither tiny nor deep. Uses
                 AI_STANDARD_MODEL when configured.
      DEEP     - root-cause analysis, complex multi-alert correlation
                 explanations, topology/troubleshooting reasoning, multi-step
                 diagnosis. Uses AI_DEEP_MODEL, falling back to the existing
                 configured `ollama_model` (qwen3.6:latest) - the deep/default
                 capability is preserved unchanged.
    """

    UTILITY = "utility"
    STANDARD = "standard"
    DEEP = "deep"


def select_model(tier: AIModelTier) -> tuple[str, bool]:
    """Resolve the model for a tier. Returns (model, fell_back).

    Deterministic configuration lookup only - no I/O and no LLM call.
    Precedence per tier:
      UTILITY  -> AI_FAST_MODEL, else default
      STANDARD -> AI_STANDARD_MODEL, else default
      DEEP     -> AI_DEEP_MODEL, else default
    `default` is the existing configured OLLAMA_MODEL (deep/default
    capability), so an unconfigured tier or environment preserves today's
    behaviour exactly (fell_back=False).
    """
    settings = get_settings()
    default = settings.ollama_model
    configured: str | None = None
    if tier is AIModelTier.UTILITY:
        configured = settings.ai_fast_model
    elif tier is AIModelTier.STANDARD:
        configured = settings.ai_standard_model
    elif tier is AIModelTier.DEEP:
        configured = settings.ai_deep_model

    configured = (configured or "").strip()
    if configured and configured != default:
        # A tier-specific model is configured; the default remains the
        # in-process fallback target if that model is unavailable (see _chat).
        return configured, False
    # Not configured (or identical to default) -> existing behaviour.
    return default, False


IntentKind = Literal["command", "query"]


class IntentResult(BaseModel):
    """Typed result of LLM intent classification.

    fallback_used=True means the LLM was unreachable/unparseable and the
    caller MUST fall back to the deterministic keyword heuristic instead
    of trusting this result.
    """

    intent: IntentKind = "query"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # True when the text is too ambiguous for the RoutingEngine's keyword
    # match alone (e.g. no department keyword present at all).
    needs_disambiguation: bool = False
    # Optional department hint drawn from the same vocabulary the
    # CommandCenterService keyword heuristic uses (noc/soc/cloud/data/
    # infra/it). Informational only - the caller decides what to do with it.
    department_hint: str | None = None
    reason: str = ""
    fallback_used: bool = False


_INTENT_SYSTEM_PROMPT = """You classify a user message for an infrastructure operations assistant.
Answer with ONLY a JSON object, no other text, using exactly these keys:
{"intent": "command" | "query", "confidence": <0.0-1.0>, "needs_disambiguation": <true|false>, "department_hint": <one of "noc","soc","cloud","data","infra","it" or null>, "reason": "<max 15 words>"}

Rules:
- "command": an imperative asking the system to DO something (investigate, fix, restart, deploy, run, check-and-resolve).
- "query": a question or request for information/status (what is, show me, how many, why, health of).
- needs_disambiguation=true only when intent is "command" and no operations department is clearly implied.
- department_hint must be null unless the message clearly names a domain (network/security/cloud/data/infrastructure/ITSM tickets).
"""

_SUMMARY_SYSTEM_PROMPT = """You are an infrastructure operations copilot explaining real monitoring/ITSM data to an engineer.

STRICT RULES - violating any of these makes your output unusable:
1. Summarize and explain ONLY what is present in the provided context data.
2. NEVER state a number, percentage, count, timestamp, name, or status that is not literally present in the context.
3. If the context shows no issues, say so plainly (e.g. "No open critical alerts are recorded").
4. If the context is empty or shows no data for something the user asked about, say that plainly instead of guessing.
5. Do not speculate, do not give advice beyond describing what the data shows, do not invent causes.
6. Keep it concise: short paragraphs or a compact bullet list. Plain text only, no markdown headers.
"""


async def _chat(
    *,
    system_prompt: str,
    user_content: str,
    timeout: float,
    json_mode: bool = False,
    num_predict: int | None = None,
    disable_thinking: bool = False,
    tier: AIModelTier = AIModelTier.DEEP,
) -> str | None:
    """One non-streaming chat call. Returns the assistant message content,
    or None on ANY failure (logged, never raised).

    Thin backward-compatibility wrapper over the AI Gateway: the model is
    chosen by `tier` (deterministic config lookup, no extra LLM call) and the
    gateway's Ollama adapter preserves the original bounded fallback (tier
    model -> configured default model on 404, max two attempts), the
    `think`-field compatibility retry, num_predict/keep_alive handling, and
    the controlled-failure contract (returns None, never raises).
    """
    # AIModelTier and AITier are str enums with identical values
    # ("utility"/"standard"/"deep"); convert explicitly rather than relying
    # on pydantic value coercion so any future value drift fails fast here.
    gateway_tier = AITier(tier.value)

    gateway = get_gateway()
    request = AIRequest(
        messages=[
            AIMessage(role="system", content=system_prompt),
            AIMessage(role="user", content=user_content),
        ],
        tier=gateway_tier,
        max_tokens=num_predict,
        timeout=timeout,
        response_format="json" if json_mode else "text",
        metadata={
            "disable_thinking": disable_thinking,
        },
    )
    response = await gateway.chat(request)
    if response.content and str(response.content).strip():
        return str(response.content).strip()
    return None


async def classify_intent(text: str) -> IntentResult:
    """Classify free text as command-like or query-like.

    Never raises. On any failure returns a fallback_used=True result whose
    intent is "query" with confidence 0.0 - callers must treat that as
    "LLM unavailable, use the deterministic keyword heuristic".
    """
    if not text or not text.strip():
        return IntentResult(
            intent="query",
            confidence=0.0,
            needs_disambiguation=False,
            department_hint=None,
            reason="empty input",
            fallback_used=True,
        )

    raw = await _chat(
        system_prompt=_INTENT_SYSTEM_PROMPT,
        user_content=text.strip()[:1000],
        timeout=_intent_timeout(),
        json_mode=True,
        num_predict=_INTENT_NUM_PREDICT,
        disable_thinking=True,
        # Existing request intent metadata: this call IS a simple
        # classification, so it routes to the utility tier (fast model when
        # configured). No extra LLM call is involved in deciding this.
        tier=AIModelTier.UTILITY,
    )
    if raw is None:
        return IntentResult(
            intent="query",
            confidence=0.0,
            needs_disambiguation=False,
            department_hint=None,
            reason="llm unavailable",
            fallback_used=True,
        )

    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("intent payload is not an object")
    except ValueError as exc:
        logger.warning("Ollama intent output was not valid JSON: %s", exc)
        return IntentResult(
            intent="query",
            confidence=0.0,
            needs_disambiguation=False,
            department_hint=None,
            reason="llm returned unparseable output",
            fallback_used=True,
        )

    intent = data.get("intent")
    if intent not in ("command", "query"):
        intent = "query"
    hint = data.get("department_hint")
    if hint not in ("noc", "soc", "cloud", "data", "infra", "it"):
        hint = None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0

    return IntentResult(
        intent=intent,  # type: ignore[arg-type]
        confidence=confidence,
        needs_disambiguation=bool(data.get("needs_disambiguation", False)),
        department_hint=hint,
        reason=str(data.get("reason", ""))[:120],
        fallback_used=False,
    )


async def summarize_context(context: dict) -> str:
    """Explain the given context dict in natural language.

    Never raises. On any failure returns a deterministic fallback string
    that makes clear the AI summary is unavailable - it never invents a
    plausible-sounding summary when the LLM is down.
    """
    if not context:
        return "No context data was available to summarize."

    try:
        context_text = json.dumps(context, default=str)
    except (TypeError, ValueError):
        context_text = str(context)

    raw = await _chat(
        system_prompt=_SUMMARY_SYSTEM_PROMPT,
        user_content=f"Context data:\n{context_text}",
        timeout=_summary_timeout(),
        num_predict=_SUMMARY_NUM_PREDICT,
        disable_thinking=True,
        # Existing request intent metadata: this call IS a concise Copilot
        # status/summary response (its system prompt mandates it), so it
        # routes to the utility tier. Deep/RCA callers keep the default
        # AIModelTier.DEEP (qwen3.6) unchanged.
        tier=AIModelTier.UTILITY,
    )
    if raw is None:
        return (
            "The AI summary is currently unavailable (the local language model "
            "could not be reached). The underlying data was retrieved "
            "successfully and is included in this response's context_used field."
        )
    return raw
