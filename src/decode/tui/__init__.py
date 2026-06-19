"""The terminal UI layer: ``prompt_toolkit`` input + Rich output.

``app`` owns the interactive REPL (a persistent input line via ``prompt_async()`` inside
``patch_stdout()``); ``render`` holds the pure event-to-renderable functions. Per
ADR-0002 §6 the output is append-style (no full-screen / live region) so the prompt stays
pinned beneath whatever scrolls above it.
"""
