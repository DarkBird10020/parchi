"""OpenAI-compatible provider for the one model call.

Parchi's intent check needs a model that can read a merchant's product title and
say whether it is what the human asked for. Anthropic is one way to get one. This
module is the other: any endpoint that speaks the OpenAI `/chat/completions`
shape, pointed at by a base URL. That covers OpenRouter, Together, Groq, a local
vLLM or Ollama, and ElectronHub - which is what this repo is developed against,
because a subscription endpoint makes a 1,000-row batch affordable. Portability
is not decoration: this repo has now been moved between two such endpoints, and
what broke in the move was never the HTTP shape, it was the assumptions around
it - a model catalogue that took 42s, reasoning tokens billed against
max_tokens, and a request budget an order of magnitude smaller.

Why hand-rolled urllib rather than the `openai` package
-------------------------------------------------------
This call sits in front of a payment decision. urllib gives an exact wall-clock
timeout with no library-internal retry loop hiding behind it, and it adds no
dependency to a repo whose whole argument is that the deterministic path needs
nothing. The request body is four keys; the SDK buys nothing here.

Key handling
------------
The key is read from the environment, never from a literal in this repository and
never written to the ledger, the scoreboard or a log line. `redact()` scrubs it
out of exception text before anything is printed, because the most common way a
key leaks is a stack trace pasted into an issue.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.electronhub.ai/v1"

# Preference order when no model is pinned. GLM is the default family: it is
# strong enough for a one-sentence judgement, cheap enough to run 1,000 of them,
# and available on every endpoint this module has been tested against. The list
# is matched as a prefix against the live catalogue, so a name that has been
# retired upstream silently falls through to the next one instead of 404-ing.
PREFERRED_MODELS = (
    # Measured on the endpoint, not chosen by tier. GLM flash answers a
    # one-sentence judgement in 6-9s here; the DeepSeek and Kimi entries below
    # took 37-60s for the same prompt, which is why they are last resorts
    # rather than defaults. See `eval/latency.py` and the table in the README.
    "glm-5.3-flash:dev",
    "glm-5.3:dev",
    "minimax-m2.7:dev",
    "mimo-v2.5:dev",
    "kimi-k2.6:dev",
    "deepseek-v4-flash:dev",
)

_MODEL_CACHE: list[str] | None = None


class ProviderNotConfigured(RuntimeError):
    """No API key in the environment, so this provider cannot be used."""


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a .env file, without overwriting real env vars.

    `.env` is in .gitignore. This exists so a key lives in one untracked file
    instead of being pasted into a shell history or, worse, a source literal.
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # A real environment variable always wins: CI sets secrets that way.
            if key and key not in os.environ:
                os.environ[key] = value


def base_url() -> str:
    return os.environ.get("PARCHI_OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def api_key() -> str:
    key = os.environ.get("PARCHI_OPENAI_API_KEY", "")
    if not key:
        raise ProviderNotConfigured(
            "PARCHI_OPENAI_API_KEY is not set. Put it in a .env file "
            "(see .env.example) - .env is gitignored."
        )
    return key


def redact(text: str) -> str:
    """Remove the key from any string that might be printed."""
    key = os.environ.get("PARCHI_OPENAI_API_KEY", "")
    out = str(text)
    if key:
        out = out.replace(key, "***redacted***")
    return out


# --------------------------------------------------------------------------
# spend guard
# --------------------------------------------------------------------------

class CallBudget:
    """A hard ceiling on how many model calls one process may make.

    A bug in a loop over a 1,000-row batch is the realistic way a subscription
    gets burned, and it does not announce itself - it just runs. The budget is
    process-local, checked before every request, and raises rather than silently
    degrading, so an exhausted budget is visible instead of quietly turning the
    whole scoreboard into fallback rows.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def spend(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise RuntimeError(
                    f"model call budget exhausted ({self.limit} calls). "
                    f"Raise PARCHI_MAX_CALLS if this batch really is that large."
                )
            self.used += 1


_BUDGET: CallBudget | None = None


def budget() -> CallBudget:
    global _BUDGET
    if _BUDGET is None:
        _BUDGET = CallBudget(int(os.environ.get("PARCHI_MAX_CALLS", "1200")))
    return _BUDGET


def reset_budget(limit: int | None = None) -> CallBudget:
    global _BUDGET
    _BUDGET = CallBudget(limit if limit is not None else
                         int(os.environ.get("PARCHI_MAX_CALLS", "1200")))
    return _BUDGET


class EmptyMessage(Exception):
    """A 200 whose message content is empty. Retryable, like a dropped socket."""


def _content_of(payload: Any) -> str:
    """The assistant text, or "" if the response carries none."""
    try:
        return payload["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


class CallHealth:
    """Whether the endpoint is actually answering, as opposed to configured.

    A budget counts calls a process is allowed to make. It says nothing about
    whether any of them worked, and the two fail in opposite directions: an
    expired key or an exhausted subscription burns budget on every attempt and
    returns nothing. The console's defence lamp read the budget and reported
    "working" through a dead key, because a call that was refused still counted
    as a call. That is the worst state for an operator to be lied to about.

    Consecutive failures are what matters, not a rate. One timeout in a
    thousand is weather; four in a row is an endpoint that has stopped
    answering, and a fifth is not going to be different.
    """

    def __init__(self) -> None:
        self.ok_calls = 0
        self.failed_calls = 0
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def record_ok(self) -> None:
        with self._lock:
            self.ok_calls += 1
            self.consecutive_failures = 0
            self.last_error = None

    def record_failure(self, reason: str) -> None:
        with self._lock:
            self.failed_calls += 1
            self.consecutive_failures += 1
            self.last_error = redact(str(reason))[:200]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"ok_calls": self.ok_calls,
                    "failed_calls": self.failed_calls,
                    "consecutive_failures": self.consecutive_failures,
                    "last_error": self.last_error}


# Four in a row. Below that a retry is worth having; at that point the caller
# is being told the truth instead, which is that nothing is being adjudicated.
UNHEALTHY_AFTER = 4

_HEALTH: CallHealth | None = None


def health() -> CallHealth:
    global _HEALTH
    if _HEALTH is None:
        _HEALTH = CallHealth()
    return _HEALTH


def reset_health() -> CallHealth:
    global _HEALTH
    _HEALTH = CallHealth()
    return _HEALTH


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------

def list_models(timeout: float = 30.0, refresh: bool = False) -> list[str]:
    """Fetch the endpoint's model catalogue. Cached for the process."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None and not refresh:
        return _MODEL_CACHE
    req = urllib.request.Request(
        base_url() + "/models",
        headers={"Authorization": "Bearer " + api_key()},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"model catalogue failed: HTTP {exc.code} {redact(exc.reason)}"
        ) from None
    except Exception as exc:
        raise RuntimeError(
            f"model catalogue failed: {type(exc).__name__} {redact(exc)}"
        ) from None
    _MODEL_CACHE = sorted(str(m["id"]) for m in payload.get("data", []) if m.get("id"))
    return _MODEL_CACHE


def resolve_model(requested: str | None = None) -> str:
    """Pick the model to use.

    Explicit `--model` wins, then PARCHI_MODEL, then the first entry of
    PREFERRED_MODELS that the endpoint actually offers. Resolving against the
    live catalogue rather than hardcoding one name is the point: a pinned model
    that the provider retires turns every intent check into a degraded row, and a
    degraded row still produces a verdict, so nothing would visibly break.
    """
    chosen = requested or os.environ.get("PARCHI_MODEL")
    if chosen:
        return chosen
    try:
        available = set(list_models())
    except Exception:
        return PREFERRED_MODELS[0]
    for name in PREFERRED_MODELS:
        if name in available:
            return name
    glm = sorted(m for m in available if "glm" in m.lower())
    if glm:
        return glm[0]
    raise RuntimeError("no GLM model offered by this endpoint; set PARCHI_MODEL")


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

# One keep-alive connection PER THREAD, never one shared between them.
#
# http.client.HTTPSConnection is not thread-safe, and the way that surfaces is
# nasty rather than loud: uvicorn runs sync handlers in a threadpool, so two
# concurrent authorizations grabbed the same socket, interleaved their
# request/response pairs, and came back as `ResponseNotReady: Idle` or - worse -
# one thread reading the *other* thread's response body. Measured on the demo
# server: 6 concurrent requests, 4 of them wrong. Both failures degrade rather
# than raise, so the checkpoint stayed safe and the demo silently stopped
# demonstrating anything.
#
# threading.local keeps the reason the pooling exists in the first place: urllib
# opened a fresh socket, and therefore a fresh DNS lookup, per request, and a
# 25-row batch lost 6 rows to `getaddrinfo failed`. Per-thread reuse keeps the
# lookups down without sharing a socket across threads.
_LOCAL = threading.local()


def _connection(timeout: float):
    parsed = urllib.parse.urlparse(base_url())
    key = (parsed.scheme, parsed.hostname, parsed.port, timeout)
    conn = getattr(_LOCAL, "conn", None)
    if conn is None or getattr(_LOCAL, "key", None) != key:
        connection = http.client.HTTPConnection if parsed.scheme == "http" else http.client.HTTPSConnection
        conn = connection(parsed.hostname, parsed.port, timeout=timeout)
        _LOCAL.conn = conn
        _LOCAL.key = key
    return conn, parsed.path.rstrip("/")


def _drop_connection() -> None:
    conn = getattr(_LOCAL, "conn", None)
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass
    _LOCAL.conn = None
    _LOCAL.key = None


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"match": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["match", "reason"],
    "additionalProperties": False,
}

# None = not yet decided, True = endpoint accepts json_schema, False = it does not.
_SUPPORTS_JSON_SCHEMA: bool | None = None


def _response_format() -> dict[str, Any]:
    if _SUPPORTS_JSON_SCHEMA is False:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "intent_verdict", "strict": True,
                        "schema": VERDICT_SCHEMA},
    }


def chat_json_schema(prompt: str, schema: dict[str, Any], keys: tuple[str, ...],
                     timeout: float, model: str | None = None,
                     max_tokens: int = 400,
                     name: str = "verdict") -> dict[str, Any]:
    """One completion, strict JSON out, shaped by `schema` with exactly `keys`.

    The shared parsing machinery with `chat_json` is the point: the fence
    stripping, the envelope unwrapping and the json_schema-to-json_object
    fallback were all paid for once and every model call should inherit them.
    Type checks stay with the caller, which knows what each field means; this
    layer only guarantees the exact key set.
    """
    if _SUPPORTS_JSON_SCHEMA is False:
        fmt = {"type": "json_object"}
    else:
        fmt = {"type": "json_schema",
               "json_schema": {"name": name, "strict": True, "schema": schema}}
    content = _request(prompt, timeout, model, fmt, max_tokens=max_tokens)
    if not (content or "").strip():
        # Measured on the TEE GLM tier: the endpoint sometimes answers 200 with
        # an empty message. That is a transport-grade failure wearing a success
        # status, and "empty string" must not masquerade as a verdict.
        raise RuntimeError("model returned an empty message")
    parsed = _unwrap(json.loads(_strip_fence(content)))
    if not isinstance(parsed, dict) or set(parsed) != set(keys):
        raise RuntimeError(f"model returned JSON outside the {name} schema")
    return parsed


def chat_json(prompt: str, timeout: float, model: str | None = None) -> dict[str, Any]:
    """One completion, JSON object out, no retries.

    `temperature=0` because a payment checkpoint that answers differently on the
    same cart twice is not a checkpoint.

    The response format is a strict json_schema, not a bare json_object, and the
    difference is measured rather than assumed. Over 32 calls each against
    GLM-4.7-flash on the same cart:

        json_object          29/32 usable
        json_schema strict   32/32 usable

    The three failures were all the same shape - `{"answer": false}`: the right
    judgement, no reason attached. That has to be refused, because an unexplained
    `true` would move money with nothing in the ledger to justify it, so every one
    became a needless STEP_UP. Constraining the shape at the source removes the
    failure instead of teaching the parser to guess.

    Not every OpenAI-compatible server implements json_schema, and portability is
    this module's whole point, so a rejection falls back to json_object once and
    is remembered for the process.
    """
    parsed = chat_json_schema(prompt, VERDICT_SCHEMA, ("match", "reason"),
                              timeout, model, name="intent_verdict")
    if type(parsed["match"]) is not bool:
        raise RuntimeError("model returned a non-boolean match")
    if not isinstance(parsed["reason"], str) or not parsed["reason"].strip():
        raise RuntimeError("model returned an invalid reason")
    return parsed


def _unwrap(parsed: Any, depth: int = 0) -> Any:
    """Dig the verdict out of an envelope the model wrapped it in.

    Measured against GLM: roughly one call in twelve answers correctly but
    double-encodes it, e.g.

        {"answer": "{\\"match\\": false, \\"reason\\": \\"...\\"}"}

    The judgement inside is right. Before this, every one of those raised, took
    the degraded path and became a STEP_UP - so a correct BLOCK turned into
    "are you sure?" for the customer, and on the demo it read as the checkpoint
    failing. That is a parser bug being paid for as friction.

    Deliberately narrow: it unwraps a single-key object whose value is either a
    JSON string or a nested object, at most twice, and the caller still enforces
    the exact {match, reason} shape and the types. It never invents a verdict and
    never relaxes what counts as one - an unrecognised envelope still degrades.
    """
    if depth >= 2 or not isinstance(parsed, dict):
        return parsed
    if "match" in parsed and "reason" in parsed:
        return {"match": parsed["match"], "reason": parsed["reason"]}
    if len(parsed) != 1:
        return parsed
    inner = next(iter(parsed.values()))
    if isinstance(inner, str):
        try:
            inner = json.loads(_strip_fence(inner))
        except (ValueError, TypeError):
            return parsed
    return _unwrap(inner, depth + 1)


def _strip_fence(text: str) -> str:
    """Some models wrap JSON in a markdown fence even when asked not to."""
    t = str(text).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _request(prompt: str, timeout: float, model: str | None,
             response_format: dict[str, Any], max_tokens: int) -> str:
    """POST one completion and return the raw message content.

    Everything fragile lives here and only here: the spend budget, the keep-alive
    connection, the reconnect-once-on-transport-error rule, key redaction, and the
    fallback for endpoints that do not implement json_schema.
    """
    body = {
        "model": model or resolve_model(),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": response_format,
        # Every model on the current endpoint is a reasoning model, and its
        # reasoning tokens are spent from `max_tokens` before a single
        # character of content is emitted. A 300-token ceiling that used to be
        # generous for a one-line verdict now returns an EMPTY message: the
        # model thought for 52 tokens and had nothing left to answer with.
        # Asking for no reasoning is a request, not a guarantee - the endpoint
        # still returned 300-800 characters of it - but it measured 6-9s per
        # call instead of 58-83s, which is the difference between a checkpoint
        # and a timeout. An endpoint that does not know the parameter ignores
        # it; none of the eight tested rejected it.
        "reasoning_effort": "none",
    }
    data = json.dumps(body).encode()
    headers = {
        "Authorization": "Bearer " + api_key(),
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }

    # Two attempts, and only ever for a broken *transport*. A keep-alive socket
    # that the far end closed between calls fails on write, before the request is
    # processed, so reconnecting is not a second judgement, it is the first one
    # sent down a live socket. An HTTP status is never retried: a 429 or a 500 is
    # the endpoint's answer, and this call sits in front of a payment.
    transport_failures = 0
    while True:
        try:
            budget().spend()
            conn, prefix = _connection(timeout)
            conn.request("POST", prefix + "/chat/completions", body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status in (400, 422) and "json_schema" in json.dumps(body):
                # This endpoint does not implement structured outputs. Remember
                # that, drop to json_object, and try once more: the alternative is
                # a provider that is permanently unusable for a portability
                # feature that was supposed to be optional.
                global _SUPPORTS_JSON_SCHEMA
                _SUPPORTS_JSON_SCHEMA = False
                body["response_format"] = {"type": "json_object"}
                data = json.dumps(body).encode()
                _drop_connection()
                continue
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            if _SUPPORTS_JSON_SCHEMA is None and "json_schema" in json.dumps(body):
                _SUPPORTS_JSON_SCHEMA = True
            payload = json.loads(raw)
            # An empty message is a transport-grade failure wearing a success
            # status, and it is the one this endpoint actually produces: a 200
            # whose content is "" while the reasoning field is full. Raising
            # here degraded the intent check to STEP_UP, so the demo answered
            # "ask a human" to a prompt injection it had every ability to
            # block. Retried once, on the same terms as a dropped socket,
            # because that is the same class of fault - the endpoint did not
            # give an answer, it failed to give one. Still bounded by
            # transport_failures, so a persistently empty model raises rather
            # than looping.
            if not (_content_of(payload) or "").strip():
                raise EmptyMessage("model returned an empty message")
            health().record_ok()
            break
        except EmptyMessage as exc:
            transport_failures += 1
            _drop_connection()
            if transport_failures >= 2:
                health().record_failure(f"{type(exc).__name__}: {exc}")
                raise RuntimeError(str(exc)) from None
        except RuntimeError as exc:
            # An HTTP status the endpoint chose to return: 401 on a dead key,
            # 402 or 429 on an exhausted subscription. Never retried, always
            # recorded, because this is exactly the shape of failure the
            # defence lamp used to report as "working".
            health().record_failure(f"{type(exc).__name__}: {exc}")
            _drop_connection()
            raise
        except Exception as exc:
            transport_failures += 1
            _drop_connection()
            if transport_failures >= 2:
                health().record_failure(f"{type(exc).__name__}: {exc}")
                raise RuntimeError(redact(f"{type(exc).__name__}: {exc}")) from None
    return payload["choices"][0]["message"]["content"]


def complete_json(prompt: str, timeout: float, model: str | None = None,
                  schema: dict[str, Any] | None = None,
                  max_tokens: int = 900) -> Any:
    """A JSON completion with no opinion about the shape that comes back.

    Deliberately NOT used for the intent check. That call decides whether money
    moves, and its schema is enforced in exactly one place on purpose.
    """
    fmt = ({"type": "json_schema",
            "json_schema": {"name": "reply", "strict": True, "schema": schema}}
           if schema and _SUPPORTS_JSON_SCHEMA is not False
           else {"type": "json_object"})
    return json.loads(_strip_fence(_request(prompt, timeout, model, fmt, max_tokens)))
