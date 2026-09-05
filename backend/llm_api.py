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

# The AGENT — the model that plans and acts in the workspace — can be run on
# a different model from the extractor and the judge, whose verdicts the
# participant is asked to trust. By default it is the same model. gpt-5-nano
# was tried here on 2026-09-04 as a weaker agent (it needs reasoning_effort
# "low" to use its tools at all, never fills `targets`, and replies instead
# of acting about one turn in four); the trial was stopped and the default
# put back while the study design is reconsidered. Set WEIGHTTEXT_AGENT_MODEL
# to try it again.
AGENT_MODEL = os.environ.get("WEIGHTTEXT_AGENT_MODEL", MODEL)


def lowest_effort(model):
    """The least reasoning a model can be run at HERE. gpt-5.6 takes "none".
    The gpt-5 family (gpt-5, gpt-5-mini, gpt-5-nano) rejects "none" and
    accepts "minimal", but at "minimal" gpt-5-nano does not use its tools: on
    the agent's first turn over a real brief it pasted the whole file into
    the chat instead of calling write_file, four times in four, which ends the
    run with nothing on disk. At "low" it called write_file three times in
    three, with the one-sentence "why" the prompt asks for; at "medium" it
    came back with neither a call nor a word. So "low" is the floor for that
    family, measured rather than nominal."""
    name = (model or "").lower()
    if name.startswith("gpt-5-") or name in ("gpt-5", "gpt-5-chat"):
        return "low"
    return "none"


class _Completions:
    """Translates the call shape above into what this model actually accepts.

    gpt-5.6-luna is a reasoning model, and on /v1/chat/completions it differs
    from the shape above in three ways:

      * ``max_tokens`` must be sent as ``max_completion_tokens``;
      * ``temperature`` accepts only its default, so it is dropped -- callers
        asking for 0 do not get deterministic sampling from this model;
      * every call runs at ``reasoning_effort`` from WEIGHTTEXT_REASONING,
        default the lowest the model accepts (lowest_effort) -- the study
        pins the lowest effort everywhere. Calls WITH tools are forced to
        that floor regardless: gpt-5.6-luna rejects function tools at any
        other effort, and gpt-5-nano does not know "none" at all.

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
        floor = lowest_effort(kwargs.get("model"))
        if kwargs.get("tools"):
            kwargs["reasoning_effort"] = floor
        else:
            kwargs.setdefault("reasoning_effort",
                              os.environ.get("WEIGHTTEXT_REASONING", floor))
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
