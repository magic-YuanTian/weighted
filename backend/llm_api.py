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
"""

MODEL = "your-model-name"


def client():
    raise NotImplementedError(
        "backend/llm_api.py: implement client() to return your model client."
    )
