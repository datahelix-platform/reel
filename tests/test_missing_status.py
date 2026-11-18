"""A correct proposal with no ``status`` key, and Reel's own log of a failed turn
(reel#11).

Seen building demo-reel (datahelix#113): Sonnet and Haiku sometimes return a
turn whose ``query_spec`` is correct but which omits ``status`` -- on one eval
case in 3 of 3 runs. ``_normalize_turn`` rejected it, ``_request_turn_with_retry``
retried the identical prompt three times, and ``/turn`` answered 502 with the
cause in the body only, so Reel's own log said nothing.

Two things change here:

1. A MISSING ``status`` is inferred from the payload when it is unambiguous: a
   ``query_spec`` object means proposal; none, with a message, means
   clarification. A ``status`` that is present but wrong, or a payload that
   contradicts itself, is still rejected -- those are the model being wrong,
   not the model being terse.
2. A failed turn is logged at WARNING on Reel's side, with the model, the
   attempt count and each attempt's ``parse_error``, by both the retry facade
   and the endpoint.
"""
import hashlib
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reel.serve.http import create_conversational_app
from reel.story import turn as turn_module
from reel.story.conversation import _request_turn_with_retry
from reel.story.turn import TurnAttempt, _normalize_turn, request_turn, status_was_omitted

CAPS = {"Sample": {"fields": []}}
SPEC_A = {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [{"kind": "field", "slot": "a", "op": "eq", "value": 1}]}


class TestInferredStatus:
    def test_a_missing_status_with_a_query_spec_is_a_proposal(self):
        turn = _normalize_turn({"message": "Filtering to a=1.", "query_spec": SPEC_A})
        assert turn["status"] == "proposal"
        assert turn["query_spec"] == SPEC_A
        assert turn["message"] == "Filtering to a=1."

    def test_a_null_status_is_treated_as_missing(self):
        turn = _normalize_turn({"status": None, "message": "ok", "query_spec": SPEC_A})
        assert turn["status"] == "proposal"

    def test_a_missing_status_with_no_query_spec_is_a_clarification(self):
        turn = _normalize_turn({"message": "Which donor's samples?"})
        assert turn == {"status": "clarification", "message": "Which donor's samples?", "query_spec": None}

    def test_a_missing_status_with_a_null_query_spec_is_a_clarification(self):
        turn = _normalize_turn({"message": "Which one?", "query_spec": None})
        assert turn["status"] == "clarification"

    def test_an_inferred_clarification_still_carries_the_answered_marker(self):
        turn = _normalize_turn({"message": "history_of_rhi holds that.", "resolution": "answered"})
        assert turn["status"] == "clarification" and turn["resolution"] == "answered"

    def test_a_missing_status_with_a_non_object_query_spec_is_still_rejected(self):
        # Inference stops where ambiguity starts: a query_spec that is present but
        # not an object says nothing about what the model meant.
        with pytest.raises(ValueError, match="status"):
            _normalize_turn({"message": "ok", "query_spec": "Sample"})

    def test_a_missing_status_with_nothing_else_is_still_rejected(self):
        with pytest.raises(ValueError, match="status"):
            _normalize_turn({"message": ""})
        with pytest.raises(ValueError, match="status"):
            _normalize_turn({})

    def test_a_present_but_invalid_status_is_still_rejected(self):
        # The model said something and it was wrong -- never inferred over.
        with pytest.raises(ValueError, match="status"):
            _normalize_turn({"status": "maybe", "message": "ok", "query_spec": SPEC_A})
        with pytest.raises(ValueError, match="status"):
            _normalize_turn({"status": "", "message": "ok", "query_spec": SPEC_A})

    def test_a_clarification_carrying_a_query_spec_is_still_a_contradiction(self):
        # The pre-existing loud rejection, unchanged: this is a contradiction, not
        # an omission, so inference never applies.
        with pytest.raises(ValueError, match="clarification"):
            _normalize_turn({"status": "clarification", "message": "which?", "query_spec": SPEC_A})

    def test_a_stated_status_is_never_second_guessed(self):
        # status=proposal without a query_spec stays the proposal error it always was.
        with pytest.raises(ValueError, match="query_spec"):
            _normalize_turn({"status": "proposal", "message": "ok"})

    def test_status_was_omitted_reads_only_the_status_key(self):
        assert status_was_omitted({"message": "ok", "query_spec": SPEC_A})
        assert status_was_omitted({"status": None, "message": "ok"})
        assert not status_was_omitted({"status": "proposal", "message": "ok", "query_spec": SPEC_A})
        assert not status_was_omitted("not a dict")


def _tool_call_response(arguments: dict):
    """What litellm.completion returns for a tool-call protocol, trimmed to what
    request_turn reads."""
    call = SimpleNamespace(function=SimpleNamespace(arguments=json.dumps(arguments)))
    message = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=None)


class TestRequestTurnRecordsTheInference:
    def test_an_omitted_status_is_accepted_and_flagged_on_the_attempt(self, monkeypatch):
        monkeypatch.setattr(turn_module.litellm, "completion",
                            lambda **kw: _tool_call_response({"message": "ok", "query_spec": SPEC_A}))
        attempt = request_turn("a=1", CAPS, model="test/model")
        assert attempt.parse_error is None
        assert attempt.turn["status"] == "proposal"
        assert attempt.status_inferred is True

    def test_a_stated_status_is_not_flagged(self, monkeypatch):
        monkeypatch.setattr(turn_module.litellm, "completion",
                            lambda **kw: _tool_call_response({"status": "proposal", "message": "ok", "query_spec": SPEC_A}))
        attempt = request_turn("a=1", CAPS, model="test/model")
        assert attempt.turn["status"] == "proposal"
        assert attempt.status_inferred is False

    def test_a_parse_failure_leaves_the_flag_unset(self, monkeypatch):
        monkeypatch.setattr(turn_module.litellm, "completion",
                            lambda **kw: _tool_call_response({"status": "maybe", "message": "ok"}))
        attempt = request_turn("a=1", CAPS, model="test/model")
        assert attempt.turn is None and "status" in attempt.parse_error
        assert attempt.status_inferred is False


PARSE_ERROR = "ValueError: 'status' must be 'proposal' or 'clarification', got 'maybe'"


def _unparseable(parse_error=PARSE_ERROR, redacted="ValueError(field='status')"):
    return TurnAttempt(protocol="tool_call", raw_content=None, structured_arguments='{"status": "maybe"}',
                       finish_reason="tool_calls", parse_error=parse_error, parse_error_redacted=redacted)


def _proposal(spec, message="ok", inferred=False):
    return TurnAttempt(protocol="tool_call", status_inferred=inferred,
                       turn={"status": "proposal", "message": message, "query_spec": spec})


class _ScriptedModel:
    def __init__(self, *attempts):
        self.queue = list(attempts)
        self.calls = 0

    def __call__(self, utterance, capabilities, *, existing_query_spec=None, prior_turns=(), **kw):
        self.calls += 1
        return self.queue.pop(0)


@pytest.fixture
def client(monkeypatch):
    model = _ScriptedModel()
    monkeypatch.setattr("reel.story.conversation.request_turn", model)
    monkeypatch.setattr("reel.story.conversation.MAX_ATTEMPTS", 3)
    tc = TestClient(create_conversational_app(CAPS, model="test/model"))
    tc.model = model
    return tc


UTTERANCE = "csf donors"
UTTERANCE_HASH = hashlib.sha256(UTTERANCE.encode()).hexdigest()[:12]


class TestFailedTurnsAreLogged:
    """Redacted by default (no utterance, no values from the model's output);
    verbatim with REEL_LOG_UTTERANCES=1."""

    def _fail_three_parses(self, client, caplog):
        client.model.queue += [
            _unparseable(),
            _unparseable("ValueError: 'message' is required", "ValueError(field='message')"),
            _unparseable(),
        ]
        with caplog.at_level(logging.WARNING, logger="reel"):
            r = client.post("/turn", json={"utterance": UTTERANCE, "query_spec": None, "turns": []})
        assert r.status_code == 502
        assert client.model.calls == 3
        # The 502 body Mosaic relays is unchanged by redaction: it is not a log.
        assert PARSE_ERROR in r.json()["detail"]
        facade = [rec for rec in caplog.records if rec.name == "reel.story.conversation"]
        endpoint = [rec for rec in caplog.records if rec.name == "reel.serve.http"]
        assert len(facade) == 1 and facade[0].levelno == logging.WARNING
        assert len(endpoint) == 1 and endpoint[0].levelno == logging.WARNING
        return facade[0].getMessage(), endpoint[0].getMessage()

    def test_exhausted_parse_attempts_are_logged_redacted_by_default(self, client, caplog, monkeypatch):
        monkeypatch.delenv("REEL_LOG_UTTERANCES", raising=False)
        facade, endpoint = self._fail_three_parses(client, caplog)

        assert "model='test/model'" in facade and "3 attempt(s)" in facade
        assert f"utterance len={len(UTTERANCE)} sha256={UTTERANCE_HASH}" in facade
        assert "attempt 1: parse_error=ValueError(field='status')" in facade
        assert "attempt 2: parse_error=ValueError(field='message')" in facade
        assert "attempt 3: parse_error=ValueError(field='status')" in facade

        assert "502" in endpoint and "(append, 0 prior turn(s))" in endpoint
        assert "model='test/model'" in endpoint and "3 attempt(s)" in endpoint
        assert f"utterance len={len(UTTERANCE)} sha256={UTTERANCE_HASH}" in endpoint
        assert "ValueError(field='status')" in endpoint

        for text in (facade, endpoint):
            assert UTTERANCE not in text
            assert "maybe" not in text  # the value the model sent
            assert PARSE_ERROR not in text

    def test_the_flag_logs_the_utterance_and_full_parse_errors(self, client, caplog, monkeypatch):
        monkeypatch.setenv("REEL_LOG_UTTERANCES", "1")
        facade, endpoint = self._fail_three_parses(client, caplog)

        assert "model='test/model'" in facade and "3 attempt(s)" in facade
        assert f"utterance='{UTTERANCE}'" in facade
        assert "attempt 1: parse_error=" + PARSE_ERROR in facade
        assert "attempt 2: parse_error=ValueError: 'message' is required" in facade
        assert "attempt 3: parse_error=" + PARSE_ERROR in facade

        assert "502" in endpoint and f"utterance='{UTTERANCE}'" in endpoint and PARSE_ERROR in endpoint

    def test_a_provider_failure_is_logged_by_type_by_default(self, client, caplog, monkeypatch):
        monkeypatch.delenv("REEL_LOG_UTTERANCES", raising=False)
        client.model.queue.append(TurnAttempt(protocol="tool_call", error="AuthenticationError: bad creds"))
        with caplog.at_level(logging.WARNING, logger="reel"):
            r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 502
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert {rec.name for rec in warnings} == {"reel.story.conversation", "reel.serve.http"}
        assert all("AuthenticationError" in rec.getMessage() for rec in warnings)
        assert not any("bad creds" in rec.getMessage() for rec in warnings)

    def test_a_provider_failure_is_logged_in_full_with_the_flag(self, client, caplog, monkeypatch):
        monkeypatch.setenv("REEL_LOG_UTTERANCES", "1")
        client.model.queue.append(TurnAttempt(protocol="tool_call", error="AuthenticationError: bad creds"))
        with caplog.at_level(logging.WARNING, logger="reel"):
            r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 502
        assert any("AuthenticationError: bad creds" in rec.getMessage() for rec in caplog.records)

    def test_a_failed_edit_is_logged_as_an_edit_with_its_turn_id(self, client, caplog, monkeypatch):
        monkeypatch.delenv("REEL_LOG_UTTERANCES", raising=False)
        history = [{"id": "t1", "utterance": "first", "status": "proposal", "message": "ok", "query_spec": SPEC_A}]
        client.model.queue += [_unparseable() for _ in range(3)]
        with caplog.at_level(logging.WARNING, logger="reel.serve.http"):
            r = client.post("/turn", json={"utterance": "revised", "query_spec": SPEC_A, "turns": history, "edit_turn_id": "t1"})
        assert r.status_code == 502
        (rec,) = [rec for rec in caplog.records if rec.name == "reel.serve.http"]
        assert "(edit, turn_id='t1', 1 prior turn(s))" in rec.getMessage()
        assert "revised" not in rec.getMessage()

    def test_a_successful_turn_logs_no_warning(self, client, caplog):
        client.model.queue.append(_proposal(SPEC_A))
        with caplog.at_level(logging.INFO, logger="reel"):
            r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 200
        assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]

    def test_an_inferred_status_is_noted_at_info_not_warning(self, client, caplog):
        # The turn succeeded; the omission is worth a trace for evals, not an alarm.
        client.model.queue.append(_proposal(SPEC_A, inferred=True))
        with caplog.at_level(logging.INFO, logger="reel"):
            r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 200
        (rec,) = [rec for rec in caplog.records if "inferred status" in rec.getMessage()]
        assert rec.levelno == logging.INFO and "'proposal'" in rec.getMessage()


class TestRetryFacadeDirectly:
    def test_the_facade_returns_an_inferred_turn_on_the_first_attempt(self, monkeypatch):
        # The whole point: no retries, no 502, the turn the model gave is used.
        model = _ScriptedModel(_proposal(SPEC_A, inferred=True))
        monkeypatch.setattr("reel.story.conversation.request_turn", model)
        turn = _request_turn_with_retry("x", CAPS, existing_query_spec=None, prior_turns=())
        assert turn["query_spec"] == SPEC_A and model.calls == 1

    def test_the_raised_error_still_names_the_last_parse_error(self, monkeypatch):
        monkeypatch.setattr("reel.story.conversation.MAX_ATTEMPTS", 2)
        model = _ScriptedModel(_unparseable(), _unparseable())
        monkeypatch.setattr("reel.story.conversation.request_turn", model)
        with pytest.raises(RuntimeError, match="2 attempt\\(s\\) -- " + PARSE_ERROR.replace("(", "\\(")):
            _request_turn_with_retry("x", CAPS, existing_query_spec=None, prior_turns=())


class TestRedactedParseErrors:
    """reel.redact names the rejected key, never a value."""

    def test_a_status_error_names_the_field_not_the_value(self):
        from reel.redact import redact_parse_error
        with pytest.raises(ValueError) as e:
            _normalize_turn({"status": "maybe", "message": "ok", "query_spec": SPEC_A})
        assert redact_parse_error(e.value) == "ValueError(field='status')"

    def test_a_value_after_equals_is_not_taken_for_the_field(self):
        from reel.redact import redact_parse_error
        with pytest.raises(ValueError) as e:
            _normalize_turn({"status": "proposal", "message": "ok"})
        assert redact_parse_error(e.value) == "ValueError(field='query_spec')"

    def test_an_identifier_shaped_value_that_is_no_schema_key_is_dropped(self):
        from reel.redact import redact_parse_error
        assert redact_parse_error(ValueError("got 'csf_donors' here")) == "ValueError"
        assert redact_parse_error(KeyError("csf_donors")) == "KeyError"

    def test_a_key_error_names_its_key(self):
        from reel.redact import redact_parse_error
        assert redact_parse_error(KeyError("anchor")) == "KeyError(field='anchor')"

    def test_a_json_error_is_its_type_only(self):
        from reel.redact import redact_parse_error
        with pytest.raises(json.JSONDecodeError) as e:
            json.loads("{not json")
        assert redact_parse_error(e.value) == "JSONDecodeError"

    def test_request_turn_records_the_redacted_form(self, monkeypatch):
        monkeypatch.setattr(turn_module.litellm, "completion",
                            lambda **kw: _tool_call_response({"status": "maybe", "message": "ok"}))
        attempt = request_turn("a=1", CAPS, model="test/model")
        assert attempt.parse_error_redacted == "ValueError(field='status')"
