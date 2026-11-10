"""Turn-taking planning core for the Aperture-Exon conversational contract
(add-exon-conversational-contract, design.md Decisions 1/2/4).

This is ONLY the planning core: one stateless function turning (an existing
QuerySpec or none, the prior turns, a new utterance) into a single
{status, message, query_spec} decision -- exactly Decision 3's pure-function
contract, at the smallest scope that is independently useful and testable.

Deliberately NOT included here (separate, later increments -- see
tasks.md Phase 1/2):
  - Turn id assignment, rewind-and-edit recompute-and-suspend bookkeeping
    (Decision 5). This module decides ONE turn; the orchestration layer that
    manages a turns list, assigns ids, and recomputes after an edit is a
    distinct piece of state-management logic layered on top, not planning.
  - The HTTP endpoint (design.md Decision 8's wire contract). This module
    has no network surface at all -- it is a plain function, tested without
    a server, the same order upstream built parse_query_spec/
    validate_query_spec before wrapping them in MCP tools.

Reuses spec_planner.py's capability grounding, SPEC_TOOL's QuerySpec shape,
and its normalizers directly rather than re-deriving them -- a turn's
query_spec is the exact same artifact spec_planner.py's single-shot mode
emits, just conditioned on a conversation instead of one instruction.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import litellm
import openai  # see planner.py: litellm normalizes every provider's errors onto openai's.

from ..config import MAX_ATTEMPTS, MAX_TOKENS, MODEL, REQUEST_TIMEOUT, decode_kwargs_for
from ..planner.provider_errors import is_transient
from ..planner.spec_planner import (
    SPEC_TOOL,
    _normalize_spec,
    render_capability_grounding,
    render_entity_kinds_section,
    render_traversable_edges,
)

TURN_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_turn_response",
        "description": (
            "Respond to one turn of a conversation refining a QuerySpec. If the new "
            "utterance gives a reasonable, unambiguous interpretation, emit "
            "status='proposal' with the updated query_spec and a plain-language "
            "restatement of what it now means. If the utterance is genuinely ambiguous, "
            "contradicts the existing draft in a way that can't be resolved without more "
            "information, or names a value that doesn't resolve against the capability "
            "manifest, emit status='clarification' with a question and NO query_spec. "
            "Default to proposal whenever a reasonable interpretation exists -- never "
            "guess and call it a proposal when the ambiguity is real, and never ask a "
            "clarifying question when the instruction was actually clear. "
            "THE THIRD CASE -- SCHEMA DISCOVERY: when the user asks what the data holds "
            "rather than asking for records ('what do we have on donors about head "
            "injuries?', 'what fields are available on datasets?', 'what do we record "
            "about how samples are stored?'), they are working out which fields to put IN "
            "a query. "
            "NAME THE RELEVANT FIELDS. That is the answer — without the field names the "
            "user cannot build anything, and no other part of the reply substitutes for "
            "them. "
            "BUT ONLY FIELDS THAT ACTUALLY BEAR ON THE QUESTION. If the schema holds "
            "nothing on the topic, say so plainly and name NO field. Reaching for the "
            "nearest plausible one — offering `cause_of_death` and `age_at_death` to "
            "someone asking about toxicology — is worse than saying there is nothing: it "
            "reads as confident and sends them to build a query that answers a different "
            "question. Pointing out that a free-text field MIGHT mention the topic is "
            "fine, as long as it is clear that nothing records it directly. "
            "Then decide FOR YOURSELF whether rows would also help. If one field clearly "
            "answers the question, emit status='proposal' filtered on it and say in a "
            "sentence what you filtered on and what else was close: the user wants data, "
            "and a second turn to reach it is worse than a first turn that shows it. If "
            "the question is a listing, or no single field dominates, emit "
            "status='clarification' with resolution='answered' and simply name them. "
            "That choice is YOURS and is never the user's. Do NOT ask them to pick "
            "between 'schema discovery' and 'retrieving records', or between 'what fields "
            "exist' and 'show me matching records' — those are one question answered at "
            "two lengths, and handing it back as a menu is the refusal this contract "
            "exists to prevent. When a question reads as both at once, it IS both: name "
            "the fields, and propose if one of them clearly answers it. "
            "Fall back to a genuine clarification only when nothing matches at all or two "
            "readings would give materially different queries. "
            "KEEP IT SHORT EITHER WAY. Two or three sentences. Name at most three fields, "
            "in plain language. Do NOT quote the schema's description text verbatim, do "
            "NOT repeat schema jargon like '(facet)' or 'entity', do NOT use headers or "
            "nested bullets, and do NOT end with a menu of options -- ask at most one "
            "short follow-up question. Never refuse the question as a reference lookup, "
            "and never anchor a query on a field-listing entity type."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["proposal", "clarification"],
                },
                "message": {
                    "type": "string",
                    "description": (
                        "For status=proposal: a plain-language restatement of the "
                        "query's current interpretation, e.g. 'Filtering to samples "
                        "from female donors, collected after March 2026.' For "
                        "status=clarification: the question to ask the user."
                    ),
                },
                "resolution": {
                    "type": "string",
                    "enum": ["answered", "blocked"],
                    "description": (
                        "Only meaningful when status=clarification. 'answered': the turn "
                        "ANSWERED what was asked (a schema-discovery question) -- the draft "
                        "is unchanged and nothing is required from the user before the "
                        "conversation can continue. 'blocked' (the default when omitted): "
                        "the turn asks a question that must be answered before the draft "
                        "can move. Omit entirely when status=proposal."
                    ),
                },
                "query_spec": {
                    **SPEC_TOOL["function"]["parameters"],
                    "description": (
                        "Required when status=proposal: the FULL updated QuerySpec "
                        "(not a diff) -- restate every criterion that still applies, "
                        "not only what changed this turn. Omit this property entirely "
                        "when status=clarification."
                    ),
                },
            },
            "required": ["status", "message"],
        },
    },
}


def render_current_draft(existing_query_spec: dict | None) -> str:
    if existing_query_spec is None:
        return "(no draft yet -- this is the first turn of the conversation)"
    return f"```json\n{json.dumps(existing_query_spec, indent=2)}\n```"


def render_conversation_history(prior_turns: tuple) -> str:
    """Prior turns as `(utterance -> message)` pairs, in order. Only the
    two fields relevant to grounding a NEW decision -- the full Turn
    envelope (id, status, query_spec) belongs to the orchestration layer,
    not to what the model needs to read to understand how the draft got
    here."""
    if not prior_turns:
        return "(none -- this is the first turn of the conversation)"
    lines = []
    for i, turn in enumerate(prior_turns, start=1):
        lines.append(f"{i}. User: {turn['utterance']!r}\n   Exon: {turn['message']!r}")
    return "\n".join(lines)


DEFAULT_GROUNDING_BODY = """## Live capability manifest -- the entities, fields, and the \
operators legal on each. Use these names and operators exactly:
{{capabilities}}

## Traversable relationship edges (the only legal `edge` values for a kind=related criterion):
{{edges}}{{kinds}}

## Conversation so far:
{{history}}

## Current draft QuerySpec (what the conversation has built up to this point):
{{draft}}

## Hard constraints
- Refine the CURRENT DRAFT -- preserve every criterion it already expresses unless the \
new utterance explicitly changes or removes it. Never regenerate from scratch and \
silently drop a constraint the user already asked for.
- `sort` takes AT MOST ONE entry.
- A reference field is never a direct field filter; constrain the related entity with a \
kind=related criterion instead.
- Operators are lowercase (`eq`, `in`, `is_null`), and only those listed for that specific \
field are accepted.
- Aggregation, grouping, distinct-values, and pivots are NOT supported inside a QuerySpec. \
If the instruction needs one, ask a clarifying question naming that limitation rather \
than emitting a QuerySpec that can't actually answer it."""


def build_turn_grounding(
    capabilities: dict, existing_query_spec: dict | None, prior_turns: tuple
) -> str:
    return (
        DEFAULT_GROUNDING_BODY.replace(
            "{{capabilities}}", render_capability_grounding(capabilities)
        )
        .replace("{{edges}}", render_traversable_edges(capabilities))
        .replace("{{kinds}}", render_entity_kinds_section(capabilities))
        .replace("{{history}}", render_conversation_history(prior_turns))
        .replace("{{draft}}", render_current_draft(existing_query_spec))
    )


DEFAULT_SYSTEM_PROMPT = (
    "You are Exon, refining a QuerySpec across a conversation, one turn at a time -- not "
    "answering a one-shot question. Ground every entity, field, and operator STRICTLY in "
    "the capability manifest given below; a name or operator not listed there is rejected "
    "by the server. When a current draft exists, refine it: preserve every constraint it "
    "already expresses unless the new utterance explicitly changes or removes it. When the "
    "utterance is genuinely ambiguous -- a contradictory constraint, or a value that "
    "doesn't resolve against the manifest -- ask a clarifying question instead of "
    "guessing. Never produce a QuerySpec that quietly waters down or drops a stated "
    "constraint just to avoid asking a question. Each field in the manifest below "
    "carries the schema author's own description of what it holds; use those to "
    "resolve what the user is asking for, including when they ask what the data "
    "holds rather than for records. That question is how a user works out which "
    "fields to query, so answer it -- and answer it by BUILDING the query when one "
    "field clearly fits, rather than describing the options and waiting. Write for a "
    "researcher, not for someone reading the schema: short, plain, no schema jargon, "
    "no verbatim description text, no menus."
)


@dataclass
class TurnAttempt:
    """One model call, whatever happened. Mirrors spec_planner.SpecAttempt's
    contract: records failure modes rather than raising, because a future
    harness (Phase 2) will grade the distribution of outcomes.

    `turn` is a plain dict ({"status", "message", "query_spec"}), never a
    dataclass -- `query_spec` inside it is validated by Mosaic's
    validate_query_spec, not re-validated here (see spec_planner.py's own
    rationale for the same choice)."""

    protocol: str
    turn: dict | None = None
    raw_content: str | None = None
    structured_arguments: str | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    error: str | None = None
    #: True when `error` is a transient provider failure (see provider_errors).
    error_transient: bool = False
    latency_s: float = 0.0
    parse_error: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length" and self.turn is None


def request_turn(
    utterance: str,
    capabilities: dict,
    *,
    existing_query_spec: dict | None = None,
    prior_turns: tuple = (),
    model: str = None,
    context: tuple = None,
    protocol: str = "tool_call",
    decode_kwargs: dict = None,
    max_tokens: int = None,
) -> TurnAttempt:
    """One stateless single-turn call: decide status=proposal|clarification
    for `utterance`, given the draft and history so far. No retries -- see
    spec_planner.plan_query_spec's rationale for why the retry facade
    belongs one layer up, not here.

    This function assigns no turn id and does no rewind/suspend
    bookkeeping -- purely `(existing spec, history, new utterance) ->
    decision`, per design.md Decision 3.
    """
    model = model or MODEL
    max_tokens = max_tokens or MAX_TOKENS
    if context is None:
        system = DEFAULT_SYSTEM_PROMPT
        grounding = build_turn_grounding(capabilities, existing_query_spec, prior_turns)
    else:
        system, grounding = context

    kwargs = decode_kwargs_for(model, decode_kwargs)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{grounding}\n\n## New instruction\n{utterance}"},
    ]
    if protocol == "tool_call":
        kwargs.update(
            tools=[TURN_TOOL],
            tool_choice={"type": "function", "function": {"name": "emit_turn_response"}},
        )
    elif protocol == "json_schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "turn_response", "schema": TURN_TOOL["function"]["parameters"]},
        }
    elif protocol == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    elif protocol not in ("delimited", "raw"):
        raise ValueError(f"unsupported protocol {protocol!r}")

    started = time.monotonic()
    try:
        response = litellm.completion(
            model=model, max_tokens=max_tokens, messages=messages,
            timeout=REQUEST_TIMEOUT, **kwargs
        )
    except openai.APIError as e:
        return TurnAttempt(
            protocol=protocol,
            error=f"{type(e).__name__}: {e}",
            error_transient=is_transient(e),
            latency_s=time.monotonic() - started,
        )

    latency = time.monotonic() - started
    choice = response.choices[0]
    attempt = TurnAttempt(
        protocol=protocol,
        raw_content=choice.message.content,
        finish_reason=choice.finish_reason,
        usage=dict(response.usage) if getattr(response, "usage", None) else None,
        latency_s=latency,
    )

    payload = None
    tool_calls = getattr(choice.message, "tool_calls", None)
    if protocol == "tool_call":
        if tool_calls:
            attempt.structured_arguments = tool_calls[0].function.arguments
            payload = attempt.structured_arguments
    else:
        payload = attempt.raw_content
        attempt.structured_arguments = payload

    if payload:
        try:
            attempt.turn = _normalize_turn(json.loads(payload))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            attempt.parse_error = f"{type(e).__name__}: {e}"
    return attempt


def _normalize_turn(raw: dict) -> dict:
    """Shape-normalize the model's raw tool arguments into
    {"status", "message", "query_spec"}. NOT a validator -- see
    spec_planner._normalize_spec's docstring for why that split is
    deliberate; Mosaic's validate_query_spec is the authority for
    `query_spec`'s own legality.

    Rejects loudly (raises) on a combination that is not a shape
    ambiguity but a genuine contradiction from the model: a
    'clarification' carrying a query_spec anyway. Silently dropping that
    query_spec would hide exactly the kind of half-confident, half-unsure
    output this design exists to surface rather than paper over.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"expected a JSON object, got {type(raw).__name__}")

    status = raw.get("status")
    if status not in ("proposal", "clarification"):
        raise ValueError(f"'status' must be 'proposal' or 'clarification', got {status!r}")

    message = raw.get("message")
    if not isinstance(message, str) or not message:
        raise ValueError("'message' is required and must be a non-empty string")

    if status == "proposal":
        qs = raw.get("query_spec")
        if not isinstance(qs, dict):
            raise ValueError("status='proposal' requires a 'query_spec' object")
        return {"status": "proposal", "message": message, "query_spec": _normalize_spec(qs)}

    if raw.get("query_spec") is not None:
        raise ValueError(
            "status='clarification' must not carry a 'query_spec' -- a response can't "
            "simultaneously ask a question and propose a change"
        )
    turn = {"status": "clarification", "message": message, "query_spec": None}
    # `resolution` is carried ONLY when the model declared "answered". Absent means
    # blocked, which is the pre-existing behavior and the safe default: an answered
    # turn misread as blocking degrades to over-suspension on edit, never to a wrong
    # answer. See add-schema-discovery-for-query-building design.md Decision 2.
    if raw.get("resolution") == "answered":
        turn["resolution"] = "answered"
    return turn
