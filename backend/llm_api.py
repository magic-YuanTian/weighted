"""Model access. Implement client() for the model you want to run the agent on.

The rest of the codebase talks to the model exclusively through the object this
module returns. On that object, exactly one call shape is used:

    completion = client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "...", "content": "..."}, ...],
        temperature=0.3,
        max_tokens=1600,
        tools=[...],            # present only on tool-calling steps
        tool_choice="auto",     # ditto
    )
    message = completion.choices[0].message
    message.content             # str or None
    message.tool_calls          # optional list; each item carries
                                #   .id
                                #   .function.name
                                #   .function.arguments   (JSON string)

Return any object that satisfies that interface. Keep credentials out of this
repository (environment variables or an untracked local config).

This implementation uses the OpenAI SDK, whose native response already matches
the interface above. The key is read from OPENAI_API_KEY, loaded from the
untracked .env at the repository root.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

MODEL = os.environ.get("WEIGHTTEXT_MODEL", "gpt-5.6-luna")


class _Completions:
    """Translates the call shape above into what this model actually accepts.

    gpt-5.6-luna is a reasoning model, and on /v1/chat/completions it differs
    from the shape above in three ways:

      * ``max_tokens`` must be sent as ``max_completion_tokens``;
      * ``temperature`` accepts only its default, so it is dropped -- callers
        asking for 0 do not get deterministic sampling from this model;
      * function tools are rejected unless ``reasoning_effort`` is "none", so
        tool-calling steps run without reasoning. Calls with no tools (the
        requirement extractor and the judge) keep the model's default effort.

    The callers are left alone -- this module is the documented adaptation
    point -- so the translation happens here. Lifting the third restriction
    would mean porting the callers to /v1/responses.
    """

    def __init__(self, inner):
        self._inner = inner

    def create(self, **kwargs):
        if "max_tokens" in kwargs:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
        kwargs.pop("temperature", None)  # only the default (1) is supported
        if kwargs.get("tools"):
            kwargs.setdefault("reasoning_effort", "none")
        return self._inner.create(**kwargs)


class _Chat:
    def __init__(self, inner):
        self.completions = _Completions(inner.completions)


class _Client:
    def __init__(self, inner):
        self._inner = inner
        self.chat = _Chat(inner.chat)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@lru_cache(maxsize=1)
def client():
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Put it in the untracked .env at the "
            "repository root, or export it in the environment."
        )
    return _Client(OpenAI(api_key=key,
                          base_url=os.environ.get("OPENAI_BASE_URL") or None))
