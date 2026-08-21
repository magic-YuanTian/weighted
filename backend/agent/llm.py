"""LLM wrapper for the agent loop.

All model traffic goes through ``llm_api.client()`` — see backend/llm_api.py,
which is the single place to wire in a concrete model.
"""

import json
import re

from tenacity import (retry, retry_if_not_exception_type,
                      stop_after_attempt, wait_random_exponential)

from llm_api import MODEL, client

JUDGE_MODEL = MODEL


@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(4),
       retry=retry_if_not_exception_type(NotImplementedError), reraise=True)
def chat(messages, tools=None, temperature=0.3, model=None, max_tokens=1600):
    """One completion. Returns the raw message object (may carry tool_calls)."""
    kwargs = {
        "model": model or MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    completion = client().chat.completions.create(**kwargs)
    return completion.choices[0].message


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def chat_json(messages, temperature=0, model=None, max_tokens=2000):
    """Completion parsed as JSON. Returns {} when the model returns junk —
    callers degrade gracefully rather than failing a turn."""
    msg = chat(messages, temperature=temperature, model=model,
               max_tokens=max_tokens)
    raw = (msg.content or "").strip()
    raw = _FENCE.sub("", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
    print(f"[agent.llm] non-JSON response: {raw[:200]!r}")
    return {}


def estimate_tokens(text):
    """Cheap character-based estimate — the context inspector shows relative
    weight, not a billing figure."""
    return max(1, len(text or "") // 4)
