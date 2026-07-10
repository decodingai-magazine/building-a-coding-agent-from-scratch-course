"""The harness: the runtime wrapper around the agent loop (ADR-0002 §4-5).

Owns the two interaction queues (steering + follow-up), the single-flight phase machine
that spans a whole multi-leg turn, and the cooperative-abort flag. The TUI submits user
input here and consumes the event stream; the agent loop plugs in as the turn handler.
"""
