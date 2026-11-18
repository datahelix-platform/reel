"""What Reel's failure logs may say about a user's words.

Reel's WARNING lines for a failed turn (reel#11) are written to the container
log, which is kept and read far more widely than the conversation itself. An
utterance is whatever the user typed, and a model's parse error can quote values
from the query it built (``got 'maybe'``, ``message='...'``), so by default
neither reaches the log. What does: lengths, a short stable hash (enough to tell
whether two failures were the same utterance, and to match a log line against a
report from the user who typed it), exception types and the name of the field the
parser rejected.

Set ``REEL_LOG_UTTERANCES=1`` to log the full utterance and the full parse error
instead, for a debugging session on a deployment whose log you control. The flag
is read on every call, not at import, so it can be flipped in a test or a REPL
without reloading anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

LOG_UTTERANCES_ENV = "REEL_LOG_UTTERANCES"


def log_full_text() -> bool:
    """Whether failure logs may carry utterances and parse errors verbatim."""
    return os.environ.get(LOG_UTTERANCES_ENV, "").strip() == "1"


def text_fingerprint(text: str | None) -> str:
    """``len=N sha256=<12 hex>`` -- identifies a text without revealing it."""
    text = text or ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"len={len(text)} sha256={digest}"


def describe_utterance(text: str | None) -> str:
    """The utterance as a failure log may show it."""
    if log_full_text():
        return f"utterance={(text or '')[:200]!r}"
    return f"utterance {text_fingerprint(text)}"


#: A quoted identifier that is not the right-hand side of ``name=``: the shape
#: in which the normalizers name the key they rejected (``'status' must be ...``,
#: ``requires a 'query_spec' object``). Values follow ``got`` or ``=``.
_QUOTED_NAME = re.compile(r"(?<![=\w])'([A-Za-z_][A-Za-z0-9_]{0,63})'")


def _schema_keys(schema, out: set) -> set:
    """Every property name declared anywhere in a JSON schema."""
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            out.update(props)
        for v in schema.values():
            _schema_keys(v, out)
    elif isinstance(schema, list):
        for v in schema:
            _schema_keys(v, out)
    return out


_KNOWN_KEYS: set | None = None


def _known_keys() -> set:
    """The field names a parse error may name: the keys the turn and QuerySpec
    tool schemas declare. Only a name on this list is logged, so a value that
    happens to look like an identifier can never pass for a field name."""
    global _KNOWN_KEYS
    if _KNOWN_KEYS is None:
        from .planner.spec_planner import SPEC_TOOL
        from .story.turn import TURN_TOOL

        _KNOWN_KEYS = _schema_keys([TURN_TOOL, SPEC_TOOL], set())
    return _KNOWN_KEYS


def parse_error_field(exc: BaseException) -> str | None:
    """The name of the key a parse error is about, or None if it names none we
    know. Never returns a value from the payload."""
    if isinstance(exc, json.JSONDecodeError):
        return None
    if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str):
        candidates = [exc.args[0]]
    else:
        candidates = _QUOTED_NAME.findall(str(exc))
    known = _known_keys()
    for name in candidates:
        if name in known:
            return name
    return None


def redact_parse_error(exc: BaseException) -> str:
    """``ValueError(field='status')`` -- the exception type and the key, no values."""
    field = parse_error_field(exc)
    return f"{type(exc).__name__}(field={field!r})" if field else type(exc).__name__


def redact_provider_error(error: str | None) -> str:
    """A provider error (``"Type: message"``) reduced to its type: provider
    messages can echo the request they rejected."""
    return (error or "").split(":", 1)[0].strip() or "unknown"
