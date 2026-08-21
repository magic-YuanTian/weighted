"""WeightText v4 — requirement-steered agent loop.

See AGENT_UI_DESIGN.md. The package is deliberately small and dependency-free
beyond what the v3 backend already uses:

    llm.py           tool-calling wrapper over the model in llm_api.py
    requirements.py  the requirement model + weighted prompt weaving
    tools.py         workspace tools (Tier-0 enforcement lives here)
    verifier.py      code / rule / judge routing, typed evidence, staleness
    extract.py       task brief -> proposed requirements + clarifications
    session.py       session state, workspace, append-only event log
    loop.py          one step of the agent loop (the UI drives stepping)
"""
