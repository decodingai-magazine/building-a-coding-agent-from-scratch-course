"""The Pydantic AI ReAct loop (LLM ⇄ tools): factory builds the Agent, loop drives it.

:mod:`decode.agent.loop` drives ``agent.iter()`` as the harness ``TurnHandler``, streaming
model nodes into the canonical :mod:`decode.entities.events` (ADR-0002 §1-2).
"""
