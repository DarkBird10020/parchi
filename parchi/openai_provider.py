"""OpenAI-compatible provider for the one model call.

Parchi's intent check needs a model that can read a merchant's product title and
say whether it is what the human asked for. Anthropic is one way to get one. This
module is the other: any endpoint that speaks the OpenAI `/chat/completions`
shape, pointed at by a base URL. That covers OpenRouter, Together, Groq, a local
vLLM or Ollama, and nano-gpt - which is what this repo is developed against,
because a subscription endpoint makes a 1,000-row batch affordable.

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

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://nano-gpt.com/api/v1"

# Preference order when no model is pinned. GLM is the default family: it is
# strong enough for a one-sentence judgement, cheap enough to run 1,000 of them,
# and available on every endpoint this module has been tested against. The list
# is matched as a prefix against the live catalogue, so a name that has been
# retired upstream silently falls through to the next one instead of 404-ing.
PREFERRED_MODELS = (
    "z-ai/glm-4.7-flash",
    "z-ai/glm-4.6",
    "z-ai/glm-4.5",
    "glm-4-air-0111",
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

    def spend(self) -> None:
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

_CONN: Any = None
_CONN_KEY: tuple | None = None


def _connection(timeout: float):
    """One keep-alive HTTPS connection, reused across calls.

    urllib opens a fresh socket per request, which means a fresh DNS lookup per
    request. Over a 1,000-row batch that is 1,000 lookups in a few minutes, and
    the resolver gives out long before the endpoint does: a measured 25-row run
    lost 6 rows to `getaddrinfo failed`, none of them the model's fault. Those
    rows do not error, they degrade - so the batch completes and quietly reports
    the fallback's answer for a quarter of the data.

    One connection, one lookup, reconnect only when the socket actually breaks.
    """
    global _CONN, _CONN_KEY
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(base_url())
    key = (parsed.hostname, parsed.port, timeout)
    if _CONN is None or key != _CONN_KEY:
        _CONN = http.client.HTTPSConnection(parsed.hostname, parsed.port,
                                            timeout=timeout)
        _CONN_KEY = key
    return _CONN, parsed.path.rstrip("/")


def _drop_connection() -> None:
    global _CONN
    try:
        if _CONN is not None:
            _CONN.close()
    except Exception:
        pass
    _CONN = None


def chat_json(prompt: str, timeout: float, model: str | None = None) -> dict[str, Any]:
    """One completion, JSON object out, no retries.

    `temperature=0` because a payment checkpoint that answers differently on the
    same cart twice is not a checkpoint. `response_format=json_object` asks the
    endpoint to constrain the output; the JSON parse below is still the real
    guarantee, since not every OpenAI-compatible server honours the field.
    """
    budget().spend()
    body = {
        "model": model or resolve_model(),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body).encode()
    headers = {
        "Authorization": "Bearer " + api_key(),
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }

    # Two attempts, and only ever for a broken *transport*. A keep-alive socket
    # that the far end closed between calls fails on write, before the request is
    # processed, so reconnecting is not a second judgement - it is the first one,
    # sent down a live socket. An HTTP status is never retried: a 429 or a 500 is
    # the endpoint's answer, and this call sits in front of a payment.
    last = None
    for attempt in (1, 2):
        try:
            conn, prefix = _connection(timeout)
            conn.request("POST", prefix + "/chat/completions", body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            payload = json.loads(raw)
            break
        except RuntimeError:
            _drop_connection()
            raise
        except Exception as exc:
            last = exc
            _drop_connection()
            if attempt == 2:
                raise RuntimeError(redact(f"{type(exc).__name__}: {exc}")) from None
    else:  # pragma: no cover - the loop always breaks or raises
        raise RuntimeError(redact(str(last)))

    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(_strip_fence(content))
    if "match" not in parsed or "reason" not in parsed:
        raise RuntimeError("model returned JSON without match/reason")
    return parsed


def _strip_fence(text: str) -> str:
    """Some models wrap JSON in a markdown fence even when asked not to."""
    t = str(text).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
