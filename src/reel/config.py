"""Decode and retry configuration for the planner.

**This module is the one item in Phase B-runtime that is NEW rather than carried**
(`proposals/exon-migration.md`, Phase B amendment; `mosaic-demo-small` OpenSpec
`extract-exon-runtime-to-reel` design.md Decision 3).

In the prototype these five names lived in `exon/planner.py` — the legacy
`QueryPlan` emitter, which is retired and must never be carried. Every carried
module imported them from there, so copying `planner.py` across to satisfy five
imports would have dragged the whole retired emitter into Reel and undone the
point of splitting Phase B. They move here instead.

The values are unchanged by the move. Only the environment-variable prefix
changes, `REEL_*` → `REEL_*`, per Phase B-runtime step 7 — the wire contract is
untouched, and these were never on the wire.
"""
import os


def _env(name: str, default: str) -> str:
    """Read an environment variable, treating empty as unset.

    `os.environ.get(name, default)` returns `""` when the variable is SET to an
    empty string, which is not the same as absent — and container orchestration
    sets variables to empty constantly. A compose file passing through an
    optional `REEL_MODEL: ${REEL_MODEL:-}` handed litellm an empty model string
    and an error that named no provider at all; the fix belongs here, because
    every caller would otherwise have to remember it.
    """
    return os.environ.get(name, "").strip() or default


#: The planning model. litellm-style provider-prefixed string.
MODEL = _env("REEL_MODEL", "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0")

MAX_TOKENS = int(_env("REEL_MAX_TOKENS", "8192"))

#: Ollama's default context window (`num_ctx`) is 4096 tokens *total* (prompt +
#: completion), independent of `max_tokens` — a "thinking"-capable local model
#: can burn through that on reasoning alone before ever emitting the tool call,
#: truncating with an empty response and no error (`finish_reason="length"`).
#: Measured against `ollama_chat/gemma4:12b`: 16384 was inconsistent (worked
#: once, then failed a 3-attempt run entirely); 32768 succeeded first time.
#: Only meaningful for ollama providers — passed conditionally below so it is
#: never sent to a provider that has no such parameter.
OLLAMA_NUM_CTX = int(_env("REEL_OLLAMA_NUM_CTX", "32768"))

MAX_ATTEMPTS = int(_env("REEL_MAX_ATTEMPTS", "3"))
#: Retries for a *transient provider* failure (the provider is overloaded, rate
#: limiting, or returned a 5xx) -- separate from MAX_ATTEMPTS, which budgets
#: unparseable model output. Seen live: Bedrock answering
#: `ServiceUnavailableError: Bedrock is unable to process your request` for a
#: few minutes at a time, which surfaced to the user as an HTTP 502 on the first
#: blip. Applied only by the product facades (conversation._request_turn_with_retry,
#: spec_planner.plan_query_spec), never by request_turn/request_spec themselves,
#: so the harness keeps seeing raw outcomes. Set to 0 to disable.
TRANSIENT_RETRIES = int(_env("REEL_TRANSIENT_RETRIES", "3"))
#: Exponential backoff between transient retries: base * 2**(n-1), capped.
#: Defaults give 1s, 2s, 4s -- well inside Mosaic's 60s planner timeout.
RETRY_BACKOFF_S = float(_env("REEL_RETRY_BACKOFF_S", "1.0"))
RETRY_BACKOFF_MAX_S = float(_env("REEL_RETRY_BACKOFF_MAX_S", "8.0"))

#: A loaded generation on a local model runs for minutes. litellm's default
#: request timeout cuts it off, and the resulting APIConnectionError is
#: indistinguishable from a real transport fault unless you know to look — it
#: silently turned one measurement arm into noise before this was raised.
REQUEST_TIMEOUT = int(_env("REEL_REQUEST_TIMEOUT", "1800"))


def decode_kwargs_for(model: str, decode_kwargs: dict = None) -> dict:
    """Provider-specific decode parameters.

    Shared by every emitter so a tuning gain learned by one reaches the others —
    the same reason `think=False` was wired into the product surface rather than
    left in the reliability harness.
    """
    kwargs = dict(decode_kwargs or {})
    if model.startswith("ollama"):
        if "num_ctx" not in kwargs:
            kwargs["num_ctx"] = OLLAMA_NUM_CTX
        # Reasoning mode off by default for the product surface too. Measured on
        # gemma4:12b: leaving it on cost 2631 completion tokens with NO tool
        # call, against 135 tokens WITH one. A tuning gain that never reaches
        # the thing users actually run is worthless. Set REEL_THINK=1 to
        # re-enable.
        if "think" not in kwargs:
            kwargs["think"] = os.environ.get("REEL_THINK", "").lower() in ("1", "true", "yes")
    return kwargs
