"""Schema-grounded LLM query planner: NL instruction -> typed QuerySpec (dict).

The QuerySpec-emitting counterpart to planner.py's QueryPlan emitter, added alongside it
(not replacing it) so the harness can grade both shapes against the same cases and prove
equivalence before anything is retired -- see openspec/changes/add-mosaic-mcp-boundary
tasks 2.2-2.7.

Three deliberate differences from planner.py, each a consequence of Mosaic now owning the
query boundary (ADR-0009):

1. **Emits a plain dict, never local dataclasses.** Exon does not re-model QuerySpec in
   Python. Re-modeling it would recreate exactly the three-way duplication this migration
   exists to remove -- the artifact goes straight to Mosaic's `validate_query_spec`, which
   is the authoritative parser and validator. `exon/ops.py`'s local op dataclasses are what
   this replaces, not something to reimplement in a new spelling.

2. **Grounding comes from `mosaic://capabilities`, which the server generates.** planner.py
   grounds partly in `evals/schema/capabilities.json`, a hand-authored exploration log of
   this deployment's observed behavior. The manifest now carries per-field legal `filter_ops`,
   a `predicate` flag marking which references can be traversed, and `enum_values` -- so the
   model can be told which ops are legal on which field *before* it guesses, rather than
   being corrected by a validation error afterward.

3. **No multi-step plan, no client_filter.** QueryPlan's `source_step` chaining and
   client-side `client_filter` existed because Mosaic had no server-side relationship
   predicate (mosaic#148). It does now, so what took two chained steps plus a client-side
   narrowing collapses into one QuerySpec with a `RelatedCondition`.

Op vocabulary is restricted to `FieldCondition` and `RelatedCondition` -- no `CriteriaGroup`
nesting -- matching add-exon-conversational-contract's Non-Goals. Mosaic's validator accepts
nested groups to depth 3; this emitter simply never produces them, keeping the tool schema
flat and legible to the model. Growing into groups later is additive on both sides.
"""
import json
import os
import time
from dataclasses import dataclass

import litellm
import openai  # see planner.py: litellm normalizes every provider's errors onto openai's.

from ..config import (
    MAX_ATTEMPTS, MAX_TOKENS, MODEL, REQUEST_TIMEOUT, TRANSIENT_RETRIES, decode_kwargs_for,
)
from . import provider_errors
from .provider_errors import describe_model_error, is_transient

# Mosaic's FilterOp enum, verbatim (mosaic.core.schema_typing.FilterOp). Lowercase, and
# `is_null` not `isNull` -- QueryPlan's uppercase "EQ"/"IN" spelling is NOT accepted here.
FILTER_OPS = ["eq", "neq", "in", "gt", "gte", "lt", "lte", "contains", "is_null"]

_FIELD_CONDITION_PROPS = {
    "kind": {"type": "string", "enum": ["field"]},
    "slot": {
        "type": "string",
        "description": "A field name exactly as listed for this entity in the grounding. "
        "Use the slot name given there; an unlisted name is rejected by the server.",
    },
    "op": {"type": "string", "enum": FILTER_OPS},
    "value": {
        "description": "The operand. For op=in, a LIST of values. For op=is_null, a BOOLEAN "
        "-- true means the field is absent, false means it is present (is_null is the only "
        "way to ask about absence; comparing to null never matches). For every other op, a "
        "single value of the field's own type.",
    },
}

SPEC_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_query_spec",
        "description": (
            "Emit a typed QuerySpec for the given instruction, grounded ONLY in the live "
            "capability manifest provided. Never invent entity, field, or relationship names "
            "not present in that grounding, and never use an operator the grounding does not "
            "list as legal for that specific field."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "v": {
                    "type": "integer",
                    "enum": [1],
                    "description": "QuerySpec version. Always 1.",
                },
                "anchor": {
                    "type": "string",
                    "description": "The entity type the query returns, exactly as named in "
                    'the grounding, e.g. "Sample" -- singular and capitalised, never the '
                    "plural accessor name.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["AND", "OR"],
                    "description": "How the top-level criteria combine.",
                },
                "criteria": {
                    "type": "array",
                    "description": "The filters. An empty list means 'all records of the "
                    "anchor type' -- valid, and correct when the instruction asks for "
                    "everything.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["field", "related"],
                                "description": (
                                    "'field': a condition on one of the anchor entity's own "
                                    "fields. 'related': a condition on a RELATED entity "
                                    "reached through a reference, used when the instruction "
                                    "constrains something about the related record rather "
                                    "than the anchor (e.g. 'samples from female donors')."
                                ),
                            },
                            "slot": _FIELD_CONDITION_PROPS["slot"],
                            "op": _FIELD_CONDITION_PROPS["op"],
                            "value": {},
                            "edge": {
                                "type": "string",
                                "description": "Required when kind=related: the reference "
                                "field on the anchor entity to traverse. Only fields the "
                                "grounding marks as traversable may be used.",
                            },
                            "quantifier": {
                                "type": "string",
                                "enum": ["some", "none"],
                                "description": "Required when kind=related. 'some': at least "
                                "one related record matches. 'none': no related record "
                                "matches (use for 'without', 'never', 'no ...').",
                            },
                            "criteria": {
                                "type": "array",
                                "description": "Required when kind=related: conditions on the "
                                "RELATED entity's own fields. These combine with AND. Field "
                                "names here belong to the related entity, not the anchor.",
                                "items": {
                                    "type": "object",
                                    "properties": _FIELD_CONDITION_PROPS,
                                    "required": ["slot", "op"],
                                },
                            },
                        },
                        "required": ["kind"],
                    },
                },
                "sort": {
                    "type": "array",
                    "description": "Optional ordering. AT MOST ONE entry -- Mosaic's query "
                    "surface takes a single order_by/order_dir pair, and a second entry is "
                    "rejected. Only fields the grounding marks as orderable may be used.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slot": {"type": "string"},
                            "direction": {"type": "string", "enum": ["asc", "desc"]},
                        },
                        "required": ["slot"],
                    },
                },
            },
            "required": ["v", "anchor", "mode", "criteria"],
        },
    },
}

SPEC_JSON_SCHEMA = SPEC_TOOL["function"]["parameters"]


def _one_line(text: str) -> str:
    """Collapse a LinkML folded description onto one line.

    The grounding is a line-per-slot listing the model reads positionally; a
    description arriving as a multi-line block would break that shape.
    """
    return " ".join(text.split())


def render_capability_grounding(capabilities: dict) -> str:
    """The per-entity field listing, with each field's LEGAL OPERATORS and its
    human-authored DESCRIPTION inline.

    The operators are the substantive grounding improvement over planner.py's
    render_schema_slots, which listed field names only and left the model to guess which
    applied -- a guess it could only be corrected on after a failed validation round-trip.
    Reference fields are rendered as traversable edges rather than as directly-filterable
    fields, because that is what the manifest reports: Mosaic's `where:` contract gives
    references no direct FilterOp at all, only relationship-predicate filtering (mosaic#181).

    The descriptions are what make SCHEMA DISCOVERY possible: a question phrased in the
    researcher's own vocabulary ("what do we have on donors about toxicology reports?")
    resolves to a slot whose name may share none of its words. The manifest has carried
    them all along -- `slot_model_to_dict` includes `description`, spread into every field
    by `entity_capability_to_dict` -- and this renderer used to drop them, which is what
    made schema questions look like a capability gap rather than a rendering one.
    """
    lines = []
    for entity, caps in sorted(capabilities.items()):
        header = f'- entity "{entity}":'
        if caps.get("description"):
            header += f' {_one_line(caps["description"])}'
        lines.append(header)
        for f in sorted(caps["fields"], key=lambda f: f["name"]):
            name = f["name"]
            if f["kind"] == "reference" and f.get("predicate"):
                target = f.get("target_entity_type")
                arity = "many" if f.get("multivalued") else "one"
                detail = (
                    f'    {name}: reference -> {target} (to-{arity}) -- traversable as a '
                    f'related edge, NOT a direct field filter'
                )
            else:
                ops = ", ".join(f["filter_ops"]) if f["filter_ops"] else "(not filterable)"
                detail = f"    {name}: {f['range']} -- ops: {ops}"
                if f.get("enum_values"):
                    detail += f"; allowed values: {', '.join(f['enum_values'])}"
                if f.get("orderable"):
                    detail += "; orderable"
            # Every slot, reference fields INCLUDED. The reference branch used to
            # `continue` before this point, which would have omitted exactly the slots
            # that name where related information lives -- most of what "what do we have
            # about X" is actually asking.
            if f.get("description"):
                detail += f'; "{_one_line(f["description"])}"'
            lines.append(detail)
    return "\n".join(lines)


def render_traversable_edges(capabilities: dict) -> str:
    """The only legal `edge` values for a kind=related criterion, per anchor entity.

    Kept as its own section for the same reason planner.py separates
    render_relationship_types: two unrelated models both put the wrong identifier in the
    relationship slot when the grounding offered more than one name without saying which
    belonged where. Here the edge name and the entity it must be used on are stated together.
    """
    lines = []
    for entity, caps in sorted(capabilities.items()):
        for f in sorted(caps["fields"], key=lambda f: f["name"]):
            if f["kind"] == "reference" and f.get("predicate"):
                lines.append(
                    f'- on anchor "{entity}": edge="{f["name"]}" reaches '
                    f'{f.get("target_entity_type")} '
                    f'({"to-many" if f.get("multivalued") else "to-one"})'
                )
    if not lines:
        lines.append("- (this schema exposes no traversable references)")
    lines.append(
        "- Only the forward direction is offered above (a reference field the anchor "
        "entity itself holds). There is no reverse traversal -- an anchor cannot reach "
        "entities that merely hold a reference back to it. If the instruction requires "
        "that direction, this is a real capability limitation: say so plainly (or ask a "
        "clarifying question, in turn mode) rather than inventing an edge name that "
        "isn't listed above."
    )
    return "\n".join(lines)


DEFAULT_GROUNDING_BODY = """## Live capability manifest -- the entities, fields, and the \
operators legal on each. Use these names and operators exactly:
{{capabilities}}

## Traversable relationship edges (the only legal `edge` values for a kind=related criterion):
{{edges}}

## Hard constraints
- `sort` takes AT MOST ONE entry.
- A reference field is never a direct field filter; constrain the related entity with a \
kind=related criterion instead.
- Operators are lowercase (`eq`, `in`, `is_null`), and only those listed for that specific \
field are accepted.
- Aggregation, grouping, distinct-values, and pivots are NOT supported. If the instruction \
requires one, still emit the closest honest row-returning filter rather than inventing an \
unsupported shape."""


def build_grounding_context(capabilities: dict) -> str:
    """The source of entity/field/operator names the model may use."""
    return DEFAULT_GROUNDING_BODY.replace(
        "{{capabilities}}", render_capability_grounding(capabilities)
    ).replace("{{edges}}", render_traversable_edges(capabilities))


DEFAULT_SYSTEM_PROMPT = (
    "You are Exon, a query planner for a bioinformatics metadata graph. Translate the user's "
    "natural-language instruction into a typed QuerySpec. Never respond with prose, raw "
    "GraphQL, or SQL. Ground every entity, field, and operator STRICTLY in the capability "
    "manifest given below -- a name or operator not listed there is rejected by the server. "
    "Preserve EVERY constraint the instruction states: if it names a region, a type, or an "
    "attribute, that must appear as a criterion. When the instruction constrains a RELATED "
    "entity rather than the anchor (e.g. 'samples from female donors'), emit a kind=related "
    "criterion traversing the appropriate edge -- never flatten it into a filter on the "
    "anchor's own reference field, which the server rejects."
)


@dataclass
class SpecAttempt:
    """One model call, whatever happened. Mirrors planner.PlanAttempt's contract -- records
    failure modes rather than raising, because the harness grades the distribution of
    outcomes and a raised exception would collapse the signal being measured.

    `spec` is a plain dict (or None), never a dataclass: Mosaic's validate_query_spec is the
    authoritative validator, so there is nothing for Exon to usefully type-check locally.
    """

    protocol: str
    spec: dict | None = None
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
        """Ran out of budget before producing anything -- see planner.PlanAttempt.truncated."""
        return self.finish_reason == "length" and self.spec is None


def request_spec(
    instruction: str,
    capabilities: dict,
    *,
    model: str = None,
    context: tuple = None,
    protocol: str = "tool_call",
    decode_kwargs: dict = None,
    max_tokens: int = None,
) -> SpecAttempt:
    """One stateless single-turn call. No retries -- see plan_query_spec for the facade.

    Takes the capability manifest alone: unlike request_plan, there is no separate
    mosaic_schema argument, because Mosaic's manifest already carries the field metadata the
    two used to have to be cross-referenced for.

    `context` is an optional (system_prompt, grounding) pair, letting the harness swap in a
    tuned context artifact; omitted, the module defaults are rendered.
    """
    model = model or MODEL
    max_tokens = max_tokens or MAX_TOKENS
    if context is None:
        system = DEFAULT_SYSTEM_PROMPT
        grounding = build_grounding_context(capabilities)
    else:
        system, grounding = context

    kwargs = decode_kwargs_for(model, decode_kwargs)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{grounding}\n\n## Instruction\n{instruction}"},
    ]
    if protocol == "tool_call":
        kwargs.update(
            tools=[SPEC_TOOL],
            tool_choice={"type": "function", "function": {"name": "emit_query_spec"}},
        )
    elif protocol == "json_schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "query_spec", "schema": SPEC_JSON_SCHEMA},
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
        return SpecAttempt(
            protocol=protocol,
            error=f"{type(e).__name__}: {e}",
            error_transient=is_transient(e),
            latency_s=time.monotonic() - started,
        )

    latency = time.monotonic() - started
    choice = response.choices[0]
    attempt = SpecAttempt(
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
        # No fenced-code or prose extraction here, ever -- see planner.request_plan.
        payload = attempt.raw_content
        attempt.structured_arguments = payload

    if payload:
        try:
            attempt.spec = _normalize_spec(json.loads(payload))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            attempt.parse_error = f"{type(e).__name__}: {e}"
    return attempt


def plan_query_spec(
    instruction: str,
    capabilities: dict,
    *,
    context: tuple = None,
    protocol: str = "tool_call",
) -> dict:
    """Retrying facade for the product surface. Mirrors planner.plan_query.

    A bounded retry is right here -- a user asking a question wants an answer, and the
    observed format failures are sampling variance. Deliberately NOT used by the harness
    runner, where a retry would conceal the unreliability being measured.
    """
    last = None
    attempts = 0
    retries = 0
    while attempts < MAX_ATTEMPTS:
        last = request_spec(instruction, capabilities, context=context, protocol=protocol)
        if last.error:
            # A transient provider failure doesn't spend a parse attempt: the
            # model never answered, so there is no sampling variance to retry.
            if last.error_transient and retries < TRANSIENT_RETRIES:
                retries += 1
                provider_errors.sleep_before_retry(retries)
                continue
            raise RuntimeError(describe_model_error(
                MODEL, last.error, transient=last.error_transient, retries=retries,
            ))
        if last.spec is not None:
            return last.spec
        attempts += 1

    detail = last.parse_error or f"content: {(last.raw_content or '')[:400]!r}"
    if last.truncated:
        detail = (
            f"response truncated (finish_reason=length) before producing a spec; {detail}"
        )
    raise RuntimeError(
        f"Model {MODEL!r} produced no usable QuerySpec in {MAX_ATTEMPTS} attempt(s) via "
        f"protocol={protocol!r} -- {detail}"
    )


def _normalize_spec(raw: dict) -> dict:
    """Shape-normalize the model's raw tool arguments into a QuerySpec dict.

    Deliberately minimal, and NOT a validator: it fills the defaults the tool schema marks
    optional and drops the empty scaffolding some models emit for the branch they didn't take
    (a kind=field criterion carrying `edge: null`, say). It does not check that slots exist,
    that operators are legal for their field, or that edges are traversable -- all of that is
    `validate_query_spec`'s job, in-process on Mosaic's side with the registry in hand.
    Re-checking it here is what would rebuild the duplication this migration removes.

    Raises only on structural nonsense that would make the artifact un-sendable.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"expected a JSON object, got {type(raw).__name__}")

    anchor = raw.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise ValueError("'anchor' is required and must be a non-empty string")

    spec = {
        "v": raw.get("v", 1),
        "anchor": anchor,
        "mode": raw.get("mode", "AND"),
        "criteria": [_normalize_criterion(c) for c in (raw.get("criteria") or [])],
    }

    sort = raw.get("sort") or []
    if sort:
        spec["sort"] = [
            {"slot": s["slot"], "direction": s.get("direction", "asc")}
            for s in sort
            if isinstance(s, dict) and s.get("slot")
        ]
    return spec


def _normalize_criterion(c: dict) -> dict:
    if not isinstance(c, dict):
        raise TypeError(f"criterion must be an object, got {type(c).__name__}")
    kind = c.get("kind")
    # Infer the kind when the model omitted it but the payload is unambiguous -- an
    # unlabelled criterion carrying `edge` is a related one. Cheap, and it converts a
    # whole-spec parse failure into a spec Mosaic can at least give a precise error about.
    if kind not in ("field", "related"):
        kind = "related" if c.get("edge") else "field"

    if kind == "related":
        return {
            "kind": "related",
            "edge": c.get("edge"),
            "quantifier": c.get("quantifier", "some"),
            "criteria": [_normalize_field_condition(f) for f in (c.get("criteria") or [])],
        }
    return _normalize_field_condition(c)


def _normalize_field_condition(c: dict) -> dict:
    if not isinstance(c, dict):
        raise TypeError(f"field condition must be an object, got {type(c).__name__}")
    out = {"kind": "field", "slot": c.get("slot"), "op": c.get("op"), "value": c.get("value")}
    # `is_null` takes a BOOLEAN operand upstream -- true means "is null", false means "is not
    # null" (mosaic.core.storage: "is_null is the only op that addresses absence"; its own
    # compiler emits value=False for the negative case). A model reading the op name as
    # self-describing tends to omit the operand entirely, which Mosaic rejects with
    # INVALID_VALUE_TYPE on every such criterion. Reading a missing operand as the plain
    # meaning of the op name is the only sensible fill, and it is a shape fix, not a semantic
    # guess -- there is no other value it could mean.
    if c.get("op") == "is_null" and not isinstance(out["value"], bool):
        out["value"] = True
    return out
