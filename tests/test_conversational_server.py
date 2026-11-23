"""HTTP endpoint tests (design.md Decision 8's wire contract). Stubs
request_turn exactly as test_conversational_orchestrator.py does -- this
file asks a different question again: given the orchestration layer
works (already proven), does the HTTP translation match Decision 8's JSON
shape exactly, and does Decision 9's query_spec handling (as amended
2026-11-23, turn-contract.md Amendment 2: an edited draft passes through,
a malformed one is a 400) behave as stated?
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


from reel.story.turn import TurnAttempt
from reel.serve.http import create_conversational_app

CAPS = {"Sample": {"fields": []}}

SPEC_A = {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [{"kind": "field", "slot": "a", "op": "eq", "value": 1}]}
SPEC_B = {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [{"kind": "field", "slot": "b", "op": "eq", "value": 2}]}
SPEC_C = {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [{"kind": "field", "slot": "c", "op": "eq", "value": 3}]}


def _proposal(spec, message="ok"):
    return TurnAttempt(protocol="tool_call", turn={"status": "proposal", "message": message, "query_spec": spec})


def _clarification(message="which one?"):
    return TurnAttempt(protocol="tool_call", turn={"status": "clarification", "message": message, "query_spec": None})


class _ScriptedModel:
    def __init__(self, *attempts):
        self.queue = list(attempts)
        self.seen_query_specs = []

    def __call__(self, utterance, capabilities, *, existing_query_spec=None, prior_turns=(), **kw):
        self.seen_query_specs.append(existing_query_spec)
        return self.queue.pop(0)


@pytest.fixture
def client(monkeypatch):
    model = _ScriptedModel()
    monkeypatch.setattr("reel.story.conversation.request_turn", model)
    app = create_conversational_app(CAPS)
    tc = TestClient(app)
    tc.model = model  # stash for tests to queue responses onto
    return tc


class TestAppend:
    def test_first_turn_with_empty_history_and_null_spec(self, client):
        client.model.queue.append(_proposal(SPEC_A, "filtering to a=1"))
        r = client.post("/turn", json={"utterance": "hello", "query_spec": None, "turns": []})
        assert r.status_code == 200
        body = r.json()
        assert body["turn"]["status"] == "proposal"
        assert body["turn"]["query_spec"] == SPEC_A
        assert body["turn"]["message"] == "filtering to a=1"
        assert body["suspended_turn_ids"] == []
        assert body["turn"]["id"]  # assigned, non-empty

    def test_second_turn_with_matching_query_spec_succeeds(self, client):
        client.model.queue.append(_proposal(SPEC_A, "t1"))
        r1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []})
        t1 = r1.json()["turn"]

        client.model.queue.append(_proposal(SPEC_B, "t2"))
        r2 = client.post("/turn", json={
            "utterance": "second", "query_spec": SPEC_A, "turns": [t1],
        })
        assert r2.status_code == 200
        assert r2.json()["turn"]["query_spec"] == SPEC_B

    def test_turns_derived_query_spec_is_accepted_and_planned_from(self, client):
        client.model.queue.extend([_proposal(SPEC_A, "t1"), _proposal(SPEC_B, "t2")])
        t1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []}).json()["turn"]

        r2 = client.post("/turn", json={"utterance": "second", "query_spec": SPEC_A, "turns": [t1]})
        assert r2.status_code == 200
        assert client.model.seen_query_specs[-1] == SPEC_A

    def test_edited_query_spec_is_accepted_and_reaches_the_planner(self, client):
        # Amendment 2: the reader edited the draft (aperture#89 -- the
        # columns they chose), so the wire spec differs from what the turns
        # derive. It is the planner's starting draft, not a 400.
        client.model.queue.extend([_proposal(SPEC_A, "t1"), _proposal(SPEC_B, "t2")])
        t1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []}).json()["turn"]

        edited = {**SPEC_A, "columns": [{"path": ["name"]}, {"path": ["donor", "samples", "id"], "mode": "explode"}]}
        r2 = client.post("/turn", json={"utterance": "second", "query_spec": edited, "turns": [t1]})
        assert r2.status_code == 200
        assert r2.json()["turn"]["query_spec"] == SPEC_B
        assert client.model.seen_query_specs[-1] == edited

    @pytest.mark.parametrize("malformed, problem", [
        ({**SPEC_A, "v": 2}, "'v'"),
        ({k: v for k, v in SPEC_A.items() if k != "anchor"}, "'anchor'"),
        ({**SPEC_A, "anchor": 7}, "'anchor'"),
        ({**SPEC_A, "mode": "XOR"}, "'mode'"),
        ({**SPEC_A, "criteria": {"slot": "a"}}, "'criteria'"),
        ({**SPEC_A, "columns": {"path": ["a"]}}, "'columns'"),
        ({**SPEC_A, "columns": [{"path": []}]}, "'columns[0].path'"),
        ({**SPEC_A, "columns": [{"path": ["a", 1]}]}, "'columns[0].path'"),
        ({**SPEC_A, "columns": [{"slot": "a"}]}, "'columns[0]'"),
        ({**SPEC_A, "columns": [{"path": ["a"], "sql": "drop"}]}, "'columns[0]'"),
        ({**SPEC_A, "columns": [{"path": ["a", "b"], "mode": 3}]}, "'columns[0].mode'"),
    ])
    def test_malformed_query_spec_is_rejected_400(self, client, malformed, problem):
        client.model.queue.append(_proposal(SPEC_A, "t1"))
        t1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []}).json()["turn"]

        r2 = client.post("/turn", json={"utterance": "second", "query_spec": malformed, "turns": [t1]})
        assert r2.status_code == 400
        assert "malformed" in r2.json()["detail"] and problem in r2.json()["detail"]
        assert len(client.model.seen_query_specs) == 1  # the planner was never asked

    def test_null_query_spec_while_turns_derive_a_draft_is_rejected_400(self, client):
        client.model.queue.append(_proposal(SPEC_A, "t1"))
        t1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []}).json()["turn"]

        r2 = client.post("/turn", json={"utterance": "second", "query_spec": None, "turns": [t1]})
        assert r2.status_code == 400
        assert "must be null exactly when" in r2.json()["detail"]

    def test_query_spec_with_no_draft_to_edit_is_rejected_400(self, client):
        r = client.post("/turn", json={"utterance": "first", "query_spec": SPEC_A, "turns": []})
        assert r.status_code == 400
        assert "must be null exactly when" in r.json()["detail"]

    def test_a_system_failure_is_a_502_not_a_conversational_status(self, client, monkeypatch):
        monkeypatch.setattr("reel.story.conversation.MAX_ATTEMPTS", 1)
        client.model.queue.append(TurnAttempt(protocol="tool_call", error="provider down"))
        r = client.post("/turn", json={"utterance": "hello", "query_spec": None, "turns": []})
        assert r.status_code == 502
        assert "provider down" in r.json()["detail"]

    def test_malformed_request_body_is_a_422(self, client):
        r = client.post("/turn", json={"query_spec": None, "turns": []})  # missing 'utterance'
        assert r.status_code == 422

    def test_unknown_turn_status_in_history_is_rejected_by_schema(self, client):
        r = client.post("/turn", json={
            "utterance": "x", "query_spec": None,
            "turns": [{"id": "a", "utterance": "y", "status": "not-a-real-status", "message": "z"}],
        })
        assert r.status_code == 422


class TestEdit:
    def test_editing_a_turn_returns_the_redone_turn_with_the_same_id(self, client):
        client.model.queue.append(_proposal(SPEC_A, "t1"))
        r1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []})
        t1 = r1.json()["turn"]

        client.model.queue.append(_proposal(SPEC_B, "t1 revised"))
        r2 = client.post("/turn", json={
            "utterance": "first, revised", "query_spec": SPEC_A, "turns": [t1], "edit_turn_id": t1["id"],
        })
        assert r2.status_code == 200
        body = r2.json()
        assert body["turn"]["id"] == t1["id"]
        assert body["turn"]["query_spec"] == SPEC_B
        assert body["suspended_turn_ids"] == []

    def test_editing_recomputes_and_suspends_a_later_turn(self, client):
        client.model.queue.append(_proposal(SPEC_A, "t1"))
        r1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []})
        t1 = r1.json()["turn"]

        client.model.queue.append(_proposal(SPEC_B, "t2"))
        r2 = client.post("/turn", json={"utterance": "second", "query_spec": SPEC_A, "turns": [t1]})
        t2 = r2.json()["turn"]

        client.model.queue.append(_proposal(SPEC_C, "t1 redone"))
        client.model.queue.append(_clarification("t2 no longer applies"))
        r3 = client.post("/turn", json={
            "utterance": "pivot", "query_spec": SPEC_A, "turns": [t1, t2], "edit_turn_id": t1["id"],
        })
        assert r3.status_code == 200
        body = r3.json()
        assert body["suspended_turn_ids"] == [t2["id"]]
        assert body["turn"]["query_spec"] == SPEC_C

    def test_editing_an_unknown_turn_id_is_a_400(self, client):
        r = client.post("/turn", json={
            "utterance": "edit", "query_spec": None,
            "turns": [{"id": "a", "utterance": "x", "status": "proposal", "query_spec": SPEC_A, "message": "y"}],
            "edit_turn_id": "no-such-id",
        })
        assert r.status_code == 400
        assert "no turn" in r.json()["detail"]

    def test_edit_does_not_require_query_spec_to_match_current_state(self, client):
        # Decision 9's query_spec handling is append-only: an edit's redo step
        # rewinds to a PAST point (turns[:idx]), which the wire's
        # current-state field is never the relevant input for.
        client.model.queue.append(_proposal(SPEC_A, "t1"))
        r1 = client.post("/turn", json={"utterance": "first", "query_spec": None, "turns": []})
        t1 = r1.json()["turn"]

        client.model.queue.append(_proposal(SPEC_B, "t1 redone"))
        r2 = client.post("/turn", json={
            # Deliberately wrong/stale top-level query_spec -- must not matter for an edit.
            "utterance": "revise", "query_spec": SPEC_C, "turns": [t1], "edit_turn_id": t1["id"],
        })
        assert r2.status_code == 200


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_the_answered_marker_survives_the_http_layer_without_breaking_it():
    """add-schema-discovery-for-query-building design.md Decision 2.

    `resolution` is an Exon-internal marker: `edit_turn` reads it only off a
    fresh recompute, so it never needs to cross this boundary. What it MUST
    not do is break it. TurnModel does not declare the field, so a future
    `extra="forbid"` here would raise on every discovery turn -- a failure
    the orchestrator's own tests cannot see, because they never build a
    TurnModel.
    """
    from reel.serve.http import TurnModel

    turn = TurnModel(**{
        "id": "t1", "utterance": "what do we have about head injuries?",
        "status": "clarification", "message": "history_of_rhi holds that.",
        "query_spec": None, "resolution": "answered",
    })
    assert turn.status == "clarification"
    assert "resolution" not in turn.model_dump()
