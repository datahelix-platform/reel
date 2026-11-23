"""HTTP endpoint for the Aperture-Exon conversational contract (design.md
Decision 8's wire contract). Slice 3 of 3 -- see conversational_planner.py's
docstring for the full split.

This is a thin wrapper: it translates Decision 8's JSON request/response
shape to and from conversational_orchestrator.append_turn/edit_turn calls.
It has no planning logic and no turn-list bookkeeping of its own -- both
already exist and are already tested in isolation.

Capabilities are supplied at app-construction time (`create_conversational_app
(capabilities, ...)`), mirroring how `mosaic.mcp.server.create_mcp_server`
takes its schema-derived data as a constructor argument rather than
fetching it itself. That keeps the app itself testable with a plain dict.

`main()` (below) is the deployment entry point that supplies it for real:
`python -m exon.conversational_server` fetches `mosaic://capabilities` from
Mosaic once at startup via `mosaic_mcp.fetch_capabilities()` and serves the
app -- design.md Decision 8's own "Exon's own turn endpoint may call ...
mosaic://capabilities as an MCP client", now that
`add-mosaic-mcp-boundary` task 2.3 has supplied that client.

This is the process Mosaic's `converse_query_spec` tool talks to: point
Mosaic's `MOSAIC_EXON_URL` at this server's `/turn` (renamed to
`MOSAIC_REEL_URL` at migration Phase C2, with the old name kept as an alias).
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..config import MODEL
from ..redact import describe_utterance, log_full_text
from ..story.conversation import (
    HeldTurnInBase,
    _current_query_spec,
    _reject_held_base,
    append_turn,
    edit_turn,
    held_turn_ids,
)

#: Why this endpoint answered 5xx, in Reel's own log. Mosaic relays the body of a
#: 502 into its log and shows the user a generic error turn, so until this
#: existed the container that failed was the one that said nothing (reel#11).
log = logging.getLogger("reel.serve.http")


class TurnModel(BaseModel):
    id: str
    utterance: str
    status: Literal["proposal", "clarification", "suspended"]
    query_spec: Optional[dict] = None
    message: str
    #: Turn contract Amendment 1 (design/turn-contract.md): this step changed
    #: after an upstream edit and awaits the user's confirmation -- not applied.
    #: Present only when true; the endpoint serializes with exclude_unset.
    held_for_review: Optional[bool] = None
    #: The spec the user last accepted for this step. Present iff held.
    previous_query_spec: Optional[dict] = None


class IncomingTurnModel(TurnModel):
    """A turn as a caller sends it back in ``turns``.

    Wider than ``TurnModel`` by exactly one status: ``error``. Mosaic answers a
    failed turn with an ``error`` turn (Decision 8) that carries no id, and a
    chat UI that draws it -- Aperture does, minting a local id for it -- then
    echoes it back on the next request. An error turn is not a conversational
    step (nothing was applied, nothing can be rewound to), so the endpoint drops
    it before planning rather than rejecting the whole request with a 422, which
    made every retry after one provider hiccup fail until the user cleared the
    chat. Responses still use ``TurnModel``: Reel itself never emits ``error``.
    """

    status: Literal["proposal", "clarification", "suspended", "error"]


class TurnRequest(BaseModel):
    utterance: str
    query_spec: Optional[dict] = None
    turns: list[IncomingTurnModel] = []
    edit_turn_id: Optional[str] = None


class TurnResponse(BaseModel):
    turn: TurnModel
    suspended_turn_ids: list[str] = []
    #: The FULL conversation after this call -- the authoritative state.
    #:
    #: Added because `turn` + `suspended_turn_ids` is provably insufficient
    #: after an edit: `edit_turn` recomputes every turn following the edited
    #: one (a real model call each), and those recomputed turns used to be
    #: discarded here, so a caller could not learn their new message or spec.
    #: A client deriving "the current draft" from its own stale copy would
    #: then read a pre-edit QuerySpec and execute the wrong query -- found by
    #: building the demo chat client against the real path.
    #:
    #: Returned on every call, not just edits, so a caller never has to
    #: reconstruct state itself: replace your list with this one. `turn` and
    #: `suspended_turn_ids` are kept (both still useful: which turn this call
    #: was about, and what to surface for re-prompting) and are redundant
    #: with, never contradictory to, this field.
    turns: list[TurnModel] = []
    #: Turn contract Amendment 1: the ids in `turns` held for review after an
    #: edit, in order. Redundant with each turn's `held_for_review`, as
    #: `suspended_turn_ids` is with `status: "suspended"`. [] on an append.
    held_turn_ids: list[str] = []


def _incoming_turn(t: IncomingTurnModel) -> dict:
    """A request turn as the orchestrator takes it. The hold fields are kept
    only on a turn that is actually held, so a turn the caller sent without
    them goes back out without them."""
    d = t.model_dump()
    if not d.get("held_for_review"):
        d.pop("held_for_review", None)
        d.pop("previous_query_spec", None)
    return d


def create_conversational_app(
    capabilities: dict, *, model: str = None, protocol: str = "tool_call"
) -> FastAPI:
    """Build the Exon conversational turn-taking service for one Mosaic
    deployment's capability manifest.

    `model`/`protocol` are threaded through to every request_turn call --
    a deployment-time choice (mirrors planner.py's REEL_MODEL env var),
    not something a caller sets per-request.
    """
    app = FastAPI(title="exon-conversational")

    # exclude_unset: the Amendment 1 fields appear only on a held turn, so an
    # unheld turn is byte-for-byte the Decision 8 shape. Every other field is
    # always set explicitly below.
    @app.post("/turn", response_model=TurnResponse, response_model_exclude_unset=True)
    def turn(req: TurnRequest) -> TurnResponse:
        # Error turns are display-only on the caller's side; see IncomingTurnModel.
        turns = [_incoming_turn(t) for t in req.turns if t.status != "error"]
        kw = {"model": model, "protocol": protocol}

        if req.edit_turn_id is not None:
            try:
                new_turns, redone, suspended = edit_turn(
                    turns, req.edit_turn_id, req.utterance, capabilities, **kw
                )
            except ValueError as exc:
                # A caller mistake (an edit_turn_id not present in the
                # turns it sent) -- 400, not 500: nothing on Exon's own
                # side is broken.
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                # A genuine system failure (API error, exhausted parse
                # retries) -- never folded into a conversational status,
                # per conversational_orchestrator.py's own rationale.
                # Mosaic's converse_query_spec (not this endpoint) is
                # responsible for turning an unreachable/failing Exon into
                # its own discriminated "error" turn status (Decision 8) --
                # from here, a loud transport-level failure is correct.
                _log_turn_failure("edit", req, exc, model)
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return TurnResponse(
                turn=TurnModel(**redone),
                suspended_turn_ids=suspended,
                turns=[TurnModel(**t) for t in new_turns],
                held_turn_ids=held_turn_ids(new_turns),
            )

        # Plain append: Decision 9, as amended 2026-11-23 (turn-contract.md
        # Amendment 2). The wire's `query_spec` is the reader's current
        # draft and may carry edits the turns never proposed (aperture#89:
        # the columns the reader chose), so it is passed through as
        # append_turn's `existing_query_spec` override rather than asserted
        # equal to the turns-derived draft. Mosaic re-validates every
        # proposal this returns (ADR-0007), so the planner only *reads* it.
        # Checked first: with a held turn in the history there is no agreed
        # draft to plan from (Amendment 1).
        try:
            _reject_held_base(turns)
        except HeldTurnInBase as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        derived = _current_query_spec(turns)
        if (req.query_spec is None) != (derived is None):
            # A null spec while the turns derive a draft (or a spec with no
            # draft to edit) is a caller bug, not an edit: still a 400.
            raise HTTPException(
                status_code=400,
                detail=(
                    "'query_spec' must be null exactly when 'turns' derive no draft "
                    "(turn-contract.md Amendment 2). "
                    f"given={req.query_spec!r} derived={derived!r}"
                ),
            )
        if req.query_spec is not None:
            problem = _query_spec_shape_problem(req.query_spec)
            if problem:
                raise HTTPException(
                    status_code=400,
                    detail=f"'query_spec' is malformed: {problem} (turn-contract.md Amendment 2)",
                )
            kw["existing_query_spec"] = req.query_spec

        try:
            new_turns, new_turn = append_turn(turns, req.utterance, capabilities, **kw)
        except RuntimeError as exc:
            _log_turn_failure("append", req, exc, model)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return TurnResponse(
            turn=TurnModel(**new_turn),
            suspended_turn_ids=[],
            turns=[TurnModel(**t) for t in new_turns],
            held_turn_ids=[],
        )

    return app


def _query_spec_shape_problem(spec: dict) -> str | None:
    """A request-shape guard on an appended turn's `query_spec`, not query
    validation (turn-contract.md Amendment 2). Reel carries no query
    validator (ADR-0007): it checks only the envelope a planner can read
    -- `v`, `anchor`, `mode`, `criteria`, and the optional `columns` list
    -- and leaves slots, operators, paths and modes to Mosaic, which
    re-validates every proposal Reel returns. Returns the first problem,
    or None."""
    v = spec.get("v")
    if type(v) is not int or v != 1:  # not True, not 1.0
        return "'v' must be the integer 1"
    if not isinstance(spec.get("anchor"), str):
        return "'anchor' must be a string"
    if spec.get("mode") not in ("AND", "OR"):
        return "'mode' must be 'AND' or 'OR'"
    if not isinstance(spec.get("criteria"), list):
        return "'criteria' must be a list"
    if "columns" in spec:
        columns = spec["columns"]
        if not isinstance(columns, list):
            return "'columns' must be a list"
        for i, col in enumerate(columns):
            if not isinstance(col, dict) or "path" not in col or not set(col) <= {"path", "mode"}:
                return f"'columns[{i}]' must be an object with 'path' and an optional 'mode'"
            path = col["path"]
            if (
                not isinstance(path, list)
                or not path
                or not all(isinstance(p, str) and p for p in path)
            ):
                return f"'columns[{i}].path' must be a non-empty list of non-empty strings"
            if "mode" in col and not isinstance(col["mode"], str):
                return f"'columns[{i}].mode' must be a string"
    return None


def _log_turn_failure(kind: str, req: TurnRequest, exc: RuntimeError, model: str | None) -> None:
    """One WARNING per 502: the endpoint-level summary a reader greps for first.
    The per-attempt breakdown is logged where the attempts were made, in
    conversation._request_turn_with_retry.

    Redacted by default (see reel.redact): the turn id, request kind, prior-turn
    count, the utterance's length and hash, the model and the redacted cause --
    never the utterance or a value from the model's output. REEL_LOG_UTTERANCES=1
    logs the utterance and the full cause instead."""
    if log_full_text():
        cause = str(exc)
    else:
        cause = getattr(exc, "log_cause", None) or type(exc).__name__
    turn = f"turn_id={req.edit_turn_id!r}, " if req.edit_turn_id is not None else ""
    log.warning(
        "POST /turn (%s, %s%d prior turn(s)) -> 502: model=%r %s -- %s",
        kind, turn, len(req.turns), model or MODEL, describe_utterance(req.utterance), cause,
    )


#: Where this service listens. Mosaic's own MOSAIC_EXON_URL must agree with
#: whatever these produce -- the two are configured independently, in
#: different processes, so a mismatch is a deployment error nothing here can
#: detect (the symptom is an "error" turn from converse_query_spec saying
#: Exon is unreachable).
HOST_ENV = "REEL_TURN_HOST"
PORT_ENV = "REEL_TURN_PORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9100


def main() -> None:
    """`python -m reel.serve.http` -- the deployable turn service.

    Capabilities are fetched ONCE, at startup, not per request: they are a
    property of the deployment's schema, not of any conversation, and
    re-fetching per turn would add a round-trip to every chat message for
    data that cannot change without a schema migration. The cost is that a
    schema change needs a restart, which is the same tradeoff
    `mosaic.mcp.server.create_mcp_server` already makes for its own
    resources (built at mount time).

    Failing loudly here rather than starting a server that cannot plan
    anything: a planner with no grounding would accept turns and then fail
    every one of them, which is strictly worse than not starting.
    """
    import os
    import sys

    import uvicorn

    from ..planner.boundary import MosaicBoundaryError, fetch_capabilities, mcp_url

    print(f"Fetching capability grounding from {mcp_url()} ...", file=sys.stderr)
    try:
        capabilities = fetch_capabilities()
    except MosaicBoundaryError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        sys.exit(3)

    host = os.environ.get(HOST_ENV, "").strip() or DEFAULT_HOST
    try:
        port = int(os.environ.get(PORT_ENV, "").strip() or DEFAULT_PORT)
    except ValueError:
        print(
            f"{PORT_ENV}={os.environ.get(PORT_ENV)!r} is not an integer", file=sys.stderr
        )
        sys.exit(1)

    print(
        f"Grounded in {len(capabilities)} entities: {', '.join(sorted(capabilities))}",
        file=sys.stderr,
    )
    print(
        f"Serving Reel's conversational turn endpoint at http://{host}:{port}/turn\n"
        # Mosaic's variable, not Reel's -- renaming it to MOSAIC_REEL_URL is
        # migration Phase C2 (with the old name kept as a deprecated alias),
        # and that has not happened. Mosaic reads MOSAIC_EXON_URL today, so
        # that is what this must print or the instruction is simply wrong.
        f"Point Mosaic at it with: MOSAIC_EXON_URL=http://{host}:{port}/turn",
        file=sys.stderr,
    )
    uvicorn.run(create_conversational_app(capabilities), host=host, port=port)


if __name__ == "__main__":
    main()
