"""Transient provider failures and echoed error turns.

Two failures seen driving the live stack (Aperture -> Mosaic -> Reel on Bedrock):

1. Bedrock answered ``ServiceUnavailableError: Bedrock is unable to process
   your request`` for a few minutes. The facade raised on the FIRST such error,
   so every blip became an HTTP 502 whose message told the user to check their
   API key. Transient provider errors are now retried with backoff (without
   spending a parse attempt) and, if they persist, described as what they are.

2. Aperture draws Mosaic's ``error`` turn and echoes it back in ``turns`` on the
   next request. ``TurnModel`` rejected the unknown status, so every retry after
   one failure was a 422 until the user cleared the chat. Error turns in the
   incoming history are now accepted and dropped before planning.
"""
import litellm
import pytest
from fastapi.testclient import TestClient

from reel.planner import provider_errors
from reel.planner.provider_errors import backoff_delay, describe_model_error, is_transient
from reel.planner.spec_planner import SpecAttempt, plan_query_spec
from reel.serve.http import create_conversational_app
from reel.story.turn import TurnAttempt

CAPS = {"Sample": {"fields": []}}
SPEC_A = {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [{"kind": "field", "slot": "a", "op": "eq", "value": 1}]}
SPEC_B = {"v": 1, "anchor": "Sample", "mode": "AND", "criteria": [{"kind": "field", "slot": "b", "op": "eq", "value": 2}]}

UNAVAILABLE = 'ServiceUnavailableError: litellm.ServiceUnavailableError: BedrockException - {"message":"Bedrock is unable to process your request."}'


def _proposal(spec, message="ok"):
    return TurnAttempt(protocol="tool_call", turn={"status": "proposal", "message": message, "query_spec": spec})


def _transient():
    return TurnAttempt(protocol="tool_call", error=UNAVAILABLE, error_transient=True)


class _ScriptedModel:
    def __init__(self, *attempts):
        self.queue = list(attempts)
        self.calls = 0

    def __call__(self, utterance, capabilities, *, existing_query_spec=None, prior_turns=(), **kw):
        self.calls += 1
        return self.queue.pop(0)


@pytest.fixture
def slept(monkeypatch):
    calls = []
    monkeypatch.setattr(provider_errors, "sleep_before_retry", lambda n: calls.append(n))
    return calls


@pytest.fixture
def client(monkeypatch, slept):
    model = _ScriptedModel()
    monkeypatch.setattr("reel.story.conversation.request_turn", model)
    tc = TestClient(create_conversational_app(CAPS))
    tc.model = model
    return tc


class TestClassification:
    def test_service_unavailable_and_rate_limit_are_transient(self):
        assert is_transient(litellm.ServiceUnavailableError(message="x", llm_provider="bedrock", model="m"))
        assert is_transient(litellm.RateLimitError(message="x", llm_provider="bedrock", model="m"))

    def test_auth_failure_is_not_transient(self):
        assert not is_transient(litellm.AuthenticationError(message="x", llm_provider="bedrock", model="m"))

    def test_timeouts_are_not_transient(self):
        # REQUEST_TIMEOUT is sized for slow local generations; retrying one multiplies the wait.
        assert not is_transient(litellm.Timeout(message="x", llm_provider="ollama", model="m"))

    def test_backoff_doubles_and_caps(self):
        assert [backoff_delay(n) for n in (1, 2, 3)] == [1.0, 2.0, 4.0]
        assert backoff_delay(10) == 8.0

    def test_messages_distinguish_outage_from_misconfiguration(self):
        transient = describe_model_error("m", UNAVAILABLE, transient=True, retries=3)
        assert "temporarily unavailable" in transient and "API key" not in transient
        permanent = describe_model_error("m", "AuthenticationError: nope", transient=False, retries=0)
        assert "credentials" in permanent


class TestTransientRetryOverHttp:
    def test_a_transient_blip_is_retried_and_the_turn_succeeds(self, client, slept):
        client.model.queue += [_transient(), _transient(), _proposal(SPEC_A)]
        r = client.post("/turn", json={"utterance": "csf donors", "query_spec": None, "turns": []})
        assert r.status_code == 200
        assert r.json()["turn"]["query_spec"] == SPEC_A
        assert slept == [1, 2]

    def test_a_persistent_outage_is_a_502_that_says_so(self, client, slept, monkeypatch):
        monkeypatch.setattr("reel.story.conversation.TRANSIENT_RETRIES", 3)
        client.model.queue += [_transient() for _ in range(4)]
        r = client.post("/turn", json={"utterance": "csf donors", "query_spec": None, "turns": []})
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert "temporarily unavailable" in detail and "Bedrock is unable" in detail
        assert slept == [1, 2, 3]
        assert client.model.calls == 4

    def test_a_permanent_error_is_not_retried(self, client, slept):
        client.model.queue += [TurnAttempt(protocol="tool_call", error="AuthenticationError: bad creds"), _proposal(SPEC_A)]
        r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 502
        assert "credentials" in r.json()["detail"]
        assert slept == [] and client.model.calls == 1

    def test_transient_retries_do_not_spend_parse_attempts(self, client, slept, monkeypatch):
        monkeypatch.setattr("reel.story.conversation.MAX_ATTEMPTS", 1)
        client.model.queue += [_transient(), _proposal(SPEC_A)]
        r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 200

    def test_retries_can_be_disabled(self, client, slept, monkeypatch):
        monkeypatch.setattr("reel.story.conversation.TRANSIENT_RETRIES", 0)
        client.model.queue += [_transient(), _proposal(SPEC_A)]
        r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": []})
        assert r.status_code == 502 and slept == []


class TestEchoedErrorTurns:
    def _history(self):
        return [
            {"id": "t1", "utterance": "csf donors", "status": "proposal", "message": "ok", "query_spec": SPEC_A},
            {"id": "local:error:The configured convers", "utterance": "now blood",
             "status": "error", "message": "The configured conversational planning service rejected the request (HTTP 502).",
             "query_spec": None},
        ]

    def test_an_error_turn_in_history_is_accepted_not_422(self, client):
        client.model.queue.append(_proposal(SPEC_B))
        r = client.post("/turn", json={"utterance": "now blood", "query_spec": SPEC_A, "turns": self._history()})
        assert r.status_code == 200, r.text
        statuses = [t["status"] for t in r.json()["turns"]]
        assert "error" not in statuses
        assert [t["id"] for t in r.json()["turns"]][0] == "t1"
        assert len(statuses) == 2

    def test_error_turns_do_not_affect_the_query_spec_agreement_check(self, client):
        # Derived state ignores the error turn, so SPEC_A (from t1) still matches.
        client.model.queue.append(_proposal(SPEC_B))
        r = client.post("/turn", json={"utterance": "x", "query_spec": None, "turns": self._history()})
        assert r.status_code == 400

    def test_an_only_error_history_is_a_fresh_conversation(self, client):
        client.model.queue.append(_proposal(SPEC_A))
        r = client.post("/turn", json={"utterance": "csf donors", "query_spec": None, "turns": self._history()[1:]})
        assert r.status_code == 200
        assert len(r.json()["turns"]) == 1

    def test_a_genuinely_unknown_status_is_still_a_422(self, client):
        r = client.post("/turn", json={
            "utterance": "x", "query_spec": None,
            "turns": [{"id": "a", "utterance": "y", "status": "not-a-real-status", "message": "z"}],
        })
        assert r.status_code == 422


class TestSpecPlannerFacade:
    def test_plan_query_spec_retries_transient_errors(self, monkeypatch, slept):
        queue = [SpecAttempt(protocol="tool_call", error=UNAVAILABLE, error_transient=True),
                 SpecAttempt(protocol="tool_call", spec=SPEC_A)]
        monkeypatch.setattr("reel.planner.spec_planner.request_spec", lambda *a, **k: queue.pop(0))
        assert plan_query_spec("csf donors", CAPS) == SPEC_A
        assert slept == [1]

    def test_plan_query_spec_names_a_persistent_outage(self, monkeypatch, slept):
        monkeypatch.setattr("reel.planner.spec_planner.TRANSIENT_RETRIES", 1)
        queue = [SpecAttempt(protocol="tool_call", error=UNAVAILABLE, error_transient=True) for _ in range(2)]
        monkeypatch.setattr("reel.planner.spec_planner.request_spec", lambda *a, **k: queue.pop(0))
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            plan_query_spec("csf donors", CAPS)
