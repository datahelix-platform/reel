"""Downstream steps changed by a mid-path edit are held for review (reel#19).

ADR-0004 point 2, as decided 2026-11-10 and specified by the turn contract's
Amendment 1 (design/turn-contract.md): after an edit, a recomputed step whose
QuerySpec differs from the one the user accepted comes back as a proposal
awaiting confirmation -- `held_for_review: true`, with `previous_query_spec` --
and the response lists it in `changed_turn_ids`. No model calls: request_turn is
scripted, as in test_conversational_orchestrator.py.
"""
import pytest
from fastapi.testclient import TestClient

from reel.serve.http import create_conversational_app
from reel.story.conversation import (
    HeldTurnInBase,
    append_turn,
    edit_turn,
    held_turn_ids,
    specs_equal,
)
from reel.story.turn import TurnAttempt

CAPS = {"Sample": {"fields": []}}


def _spec(*slots, mode="AND"):
    return {"v": 1, "anchor": "Sample", "mode": mode,
            "criteria": [{"kind": "field", "slot": s, "op": "eq", "value": 1} for s in slots]}


A, AB, ABC, AC, X = _spec("a"), _spec("a", "b"), _spec("a", "b", "c"), _spec("a", "c"), _spec("x")


def _proposal(spec, message="ok"):
    return TurnAttempt(protocol="tool_call", turn={"status": "proposal", "message": message, "query_spec": spec})


def _clarification(message="which one?"):
    return TurnAttempt(protocol="tool_call", turn={"status": "clarification", "message": message, "query_spec": None})


def _discovery(message="slot c holds that."):
    return TurnAttempt(protocol="tool_call", turn={
        "status": "clarification", "message": message, "query_spec": None, "resolution": "answered"})


class _ScriptedModel:
    def __init__(self, *attempts):
        self.queue = list(attempts)
        self.calls = []

    def __call__(self, utterance, capabilities, *, existing_query_spec=None, prior_turns=(), **kw):
        self.calls.append({"utterance": utterance, "existing_query_spec": existing_query_spec})
        return self.queue.pop(0)


def _turn(id, spec, utterance=None, **extra):
    status = "proposal" if spec is not None else "clarification"
    return {"id": id, "utterance": utterance or f"say {id}", "status": status,
            "query_spec": spec, "message": "ok", **extra}


THREE = [_turn("t1", A), _turn("t2", AB), _turn("t3", ABC)]


@pytest.fixture
def model(monkeypatch):
    m = _ScriptedModel()
    monkeypatch.setattr("reel.story.conversation.request_turn", m)
    return m


class TestSpecsEqual:
    def test_identical_specs_are_equal(self):
        assert specs_equal(AB, _spec("a", "b"))

    def test_criteria_order_does_not_matter(self):
        assert specs_equal(_spec("a", "b"), _spec("b", "a"))

    def test_nested_criteria_order_does_not_matter(self):
        def rel(*slots):
            return {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [
                {"kind": "related", "edge": "donor", "criteria": _spec(*slots)["criteria"]}]}
        assert specs_equal(rel("a", "b"), rel("b", "a"))

    def test_a_different_value_mode_or_anchor_is_not_equal(self):
        assert not specs_equal(_spec("a"), _spec("b"))
        assert not specs_equal(_spec("a", "b"), _spec("a", "b", mode="OR"))
        assert not specs_equal(A, {**A, "anchor": "Donor"})

    def test_none_equals_only_none(self):
        assert specs_equal(None, None)
        assert not specs_equal(None, A) and not specs_equal(A, None)


class TestEditTurnHolds:
    def test_an_unchanged_recompute_is_not_held(self, model):
        # Edit t1 to the same meaning; t2/t3 re-plan to what they were.
        model.queue += [_proposal(A), _proposal(AB), _proposal(ABC)]
        new_turns, redone, suspended = edit_turn(THREE, "t1", "say t1 again", CAPS)
        assert held_turn_ids(new_turns) == [] and suspended == []
        assert all("held_for_review" not in t and "previous_query_spec" not in t for t in new_turns)

    def test_a_reordered_but_equivalent_spec_is_not_held(self, model):
        model.queue += [_proposal(A), _proposal(_spec("b", "a")), _proposal(ABC)]
        new_turns, _, _ = edit_turn(THREE, "t1", "same", CAPS)
        assert held_turn_ids(new_turns) == []

    def test_a_changed_spec_is_held_with_its_previous_spec(self, model):
        model.queue += [_proposal(X), _proposal(_spec("x", "b")), _proposal(_spec("x", "b", "c"))]
        new_turns, redone, suspended = edit_turn(THREE, "t1", "x instead", CAPS)
        assert held_turn_ids(new_turns) == ["t2", "t3"]
        t2, t3 = new_turns[1], new_turns[2]
        assert t2["status"] == "proposal" and t2["query_spec"] == _spec("x", "b")
        assert t2["held_for_review"] is True and t2["previous_query_spec"] == AB
        assert t3["previous_query_spec"] == ABC
        assert suspended == []

    def test_the_edited_turn_itself_is_never_held(self, model):
        model.queue += [_proposal(X), _proposal(AB), _proposal(ABC)]
        new_turns, redone, _ = edit_turn(THREE, "t1", "x instead", CAPS)
        assert "held_for_review" not in redone and "t1" not in held_turn_ids(new_turns)

    def test_later_steps_are_re_asked_against_the_held_spec(self, model):
        # t2 changes (held); t3 is planned against t2's NEW spec, and is held
        # only if it changed itself.
        model.queue += [_proposal(A), _proposal(AC), _proposal(ABC)]
        new_turns, _, _ = edit_turn(THREE, "t1", "same", CAPS)
        assert model.calls[2]["existing_query_spec"] == AC
        assert held_turn_ids(new_turns) == ["t2"]

    def test_a_proposal_that_becomes_an_answered_clarification_is_held(self, model):
        model.queue += [_proposal(A), _discovery(), _proposal(ABC)]
        new_turns, _, suspended = edit_turn(THREE, "t1", "same", CAPS)
        t2 = new_turns[1]
        assert t2["status"] == "clarification" and t2["query_spec"] is None
        assert t2["held_for_review"] is True and t2["previous_query_spec"] == AB
        assert suspended == []

    def test_a_clarification_that_becomes_a_proposal_is_held_with_a_null_previous(self, model):
        turns = [_turn("t1", A), _turn("t2", None), _turn("t3", ABC)]
        model.queue += [_proposal(A), _proposal(AB), _proposal(ABC)]
        new_turns, _, _ = edit_turn(turns, "t1", "same", CAPS)
        t2 = new_turns[1]
        assert t2["held_for_review"] is True and t2["previous_query_spec"] is None
        assert held_turn_ids(new_turns) == ["t2"]

    def test_a_clarification_that_stays_specless_is_not_held(self, model):
        turns = [_turn("t1", A), _turn("t2", None)]
        model.queue += [_proposal(A), _discovery()]
        new_turns, _, _ = edit_turn(turns, "t1", "same", CAPS)
        assert held_turn_ids(new_turns) == []

    def test_a_suspend_cascade_is_reported_as_suspended_only(self, model):
        model.queue += [_proposal(X), _clarification("b of what?")]
        new_turns, _, suspended = edit_turn(THREE, "t1", "x instead", CAPS)
        assert suspended == ["t2", "t3"]
        assert held_turn_ids(new_turns) == []
        assert all("held_for_review" not in t for t in new_turns)

    def test_a_held_step_after_which_a_step_suspends(self, model):
        # Holds and suspensions are disjoint: t2 held, t3 suspended.
        model.queue += [_proposal(A), _proposal(AC), _clarification()]
        new_turns, _, suspended = edit_turn(THREE, "t1", "same", CAPS)
        assert held_turn_ids(new_turns) == ["t2"] and suspended == ["t3"]

    def test_a_re_held_step_keeps_the_spec_the_user_accepted(self, model):
        # t2 is already held (accepted AB, pending AC). Another edit of t1
        # re-plans t2 to X: its baseline is still AB, not the unconfirmed AC.
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB)]
        model.queue += [_proposal(_spec("y")), _proposal(X)]
        new_turns, _, _ = edit_turn(turns, "t1", "y", CAPS)
        assert new_turns[1]["held_for_review"] is True and new_turns[1]["previous_query_spec"] == AB

    def test_a_held_step_that_re_plans_to_its_accepted_spec_is_released(self, model):
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB)]
        model.queue += [_proposal(A), _proposal(AB)]
        new_turns, _, _ = edit_turn(turns, "t1", "a", CAPS)
        assert "held_for_review" not in new_turns[1] and "previous_query_spec" not in new_turns[1]

    def test_editing_the_held_step_itself_is_allowed(self, model):
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB)]
        model.queue += [_proposal(AB)]
        new_turns, redone, _ = edit_turn(turns, "t2", "b after all", CAPS)
        assert redone["query_spec"] == AB and "held_for_review" not in redone

    def test_editing_past_a_held_step_is_refused(self, model):
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB), _turn("t3", ABC)]
        with pytest.raises(HeldTurnInBase, match="t2"):
            edit_turn(turns, "t3", "anything", CAPS)
        assert model.calls == []


class TestAppendTurn:
    def test_appending_after_a_held_turn_is_refused(self, model):
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB)]
        with pytest.raises(HeldTurnInBase, match="t2"):
            append_turn(turns, "more", CAPS)
        assert model.calls == []

    def test_a_new_turn_is_never_held(self, model):
        model.queue.append(_proposal(AB))
        new_turns, new_turn = append_turn([_turn("t1", A)], "and b", CAPS)
        assert "held_for_review" not in new_turn and held_turn_ids(new_turns) == []


@pytest.fixture
def client(monkeypatch):
    m = _ScriptedModel()
    monkeypatch.setattr("reel.story.conversation.request_turn", m)
    tc = TestClient(create_conversational_app(CAPS))
    tc.model = m
    return tc


class TestHttp:
    def test_an_edit_reports_held_turns_on_the_wire(self, client):
        client.model.queue += [_proposal(X), _proposal(_spec("x", "b")), _proposal(_spec("x", "b", "c"))]
        r = client.post("/turn", json={"utterance": "x instead", "query_spec": ABC,
                                       "turns": THREE, "edit_turn_id": "t1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed_turn_ids"] == ["t2", "t3"]
        assert body["suspended_turn_ids"] == []
        t1, t2, t3 = body["turns"]
        assert "held_for_review" not in t1 and "previous_query_spec" not in t1
        assert t2["held_for_review"] is True and t2["previous_query_spec"] == AB
        assert t2["status"] == "proposal" and t2["query_spec"] == _spec("x", "b")
        assert t3["previous_query_spec"] == ABC

    def test_an_unheld_turn_keeps_the_decision_8_shape_exactly(self, client):
        client.model.queue += [_proposal(A), _proposal(AB), _proposal(ABC)]
        r = client.post("/turn", json={"utterance": "same", "query_spec": ABC,
                                       "turns": THREE, "edit_turn_id": "t1"})
        body = r.json()
        assert body["changed_turn_ids"] == []
        for t in body["turns"] + [body["turn"]]:
            assert set(t) == {"id", "utterance", "status", "query_spec", "message"}

    def test_an_append_returns_an_empty_changed_list(self, client):
        client.model.queue.append(_proposal(A))
        r = client.post("/turn", json={"utterance": "a", "query_spec": None, "turns": []})
        assert r.status_code == 200
        assert r.json()["changed_turn_ids"] == []

    def test_appending_after_a_held_turn_is_a_400_naming_it(self, client):
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB)]
        r = client.post("/turn", json={"utterance": "more", "query_spec": AC, "turns": turns})
        assert r.status_code == 400 and "t2" in r.json()["detail"]

    def test_a_confirmed_turn_is_an_ordinary_turn(self, client):
        # Confirming is client-side: the caller drops the hold fields and sends
        # the turn back. The append then plans on top of the confirmed spec.
        turns = [_turn("t1", A), _turn("t2", AC)]
        client.model.queue.append(_proposal(_spec("a", "c", "d")))
        r = client.post("/turn", json={"utterance": "and d", "query_spec": AC, "turns": turns})
        assert r.status_code == 200
        assert client.model.calls[0]["existing_query_spec"] == AC

    def test_an_explicit_false_hold_is_not_echoed(self, client):
        turns = [_turn("t1", A, held_for_review=False, previous_query_spec=None)]
        client.model.queue.append(_proposal(AB))
        r = client.post("/turn", json={"utterance": "and b", "query_spec": A, "turns": turns})
        assert r.status_code == 200
        assert "held_for_review" not in r.json()["turns"][0]

    def test_editing_past_a_held_turn_is_a_400(self, client):
        turns = [_turn("t1", A), _turn("t2", AC, held_for_review=True, previous_query_spec=AB), _turn("t3", ABC)]
        r = client.post("/turn", json={"utterance": "x", "query_spec": ABC, "turns": turns, "edit_turn_id": "t3"})
        assert r.status_code == 400 and "t2" in r.json()["detail"]
