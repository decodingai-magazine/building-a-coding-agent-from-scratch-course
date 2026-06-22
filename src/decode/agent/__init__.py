"""The Pydantic AI ReAct loop (LLM ⇄ tools).

ADR-0002 §1-2: ``decode``'s agent is a Pydantic AI :class:`~pydantic_ai.Agent` on Gemini.
:mod:`decode.agent.factory` builds it; :mod:`decode.agent.loop` drives ``agent.iter()`` as
the harness :data:`~decode.harness.runner.TurnHandler`, streaming model nodes into the
canonical :mod:`decode.entities.events`. Chat-only in task 004 (no tools yet); tools + the
permission gate land in task 005+.
"""
