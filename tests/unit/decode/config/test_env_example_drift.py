"""Drift guard: ``.env.example`` ≡ ``Settings`` fields, BOTH directions, NO allowlist (ADR-0021).

``.env.example`` is the documented config surface and ``Settings`` the one reader; a Modal Secret at a
remote ``DECODE_ENV`` is filled in by hand from this file. So an undocumented field is not a docs nit:
it is a knob an operator never learns to set. This makes that a test failure instead.

**No allowlist, by design** — an exclusion set is how a drift test rots. The three process-env-only
operator variables (``DECODE_LOG_FILE``, ``MODAL_TOKEN_ID``, ``MODAL_TOKEN_SECRET``) are handled by
SHAPE, not by an exception list: they are read from ``os.environ`` by the logger and the modal CLI,
never from ``.env``, so ``.env.example`` documents them as prose and the parser below simply never
sees them as keys.
"""

import re
from pathlib import Path

from decode.config.settings import Settings

ENV_EXAMPLE = Path(__file__).parents[4] / ".env.example"

# A documented variable, commented out or live: a commented example line still documents the knob (and
# most of this file's defaults ship commented), so it counts. Prose mentioning a name does NOT — only
# the ``KEY=`` shape does, which is precisely how the os.environ-only trio stays documented without
# being claimed as part of the surface.
_KEY_LINE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=")


def documented_keys() -> set[str]:
    """Every ``KEY=`` line in ``.env.example`` (commented or not)."""
    return {
        match.group(1)
        for line in ENV_EXAMPLE.read_text().splitlines()
        if (match := _KEY_LINE.match(line))
    }


def settings_keys() -> set[str]:
    """The env-var name of every ``Settings`` field (no ``env_prefix``, no aliases: NAME.upper())."""
    return {name.upper() for name in Settings.model_fields}


def test_every_settings_field_is_documented_in_env_example():
    """A field with no ``KEY=`` line is a knob nobody learns to set. Fail, naming it."""
    missing = sorted(settings_keys() - documented_keys())

    assert not missing, (
        f"{len(missing)} Settings field(s) are not documented in .env.example: "
        f"{', '.join(missing)}. Add a line for each (e.g. `# {missing[0]}=<default>`) — "
        "an undocumented field is a knob no operator learns to set, on a laptop or in a "
        "deployment's Modal Secret (ADR-0021)."
    )


def test_every_env_example_key_is_a_real_settings_field():
    """A ``KEY=`` line nothing reads is a lie: a typo, or a knob deleted from Settings. Fail, naming it."""
    extra = sorted(documented_keys() - settings_keys())

    assert not extra, (
        f"{len(extra)} key(s) in .env.example are not Settings fields: {', '.join(extra)}. "
        f"Remove the line, or add the field to config/settings.py. A variable that is genuinely "
        "read from os.environ (never from .env) belongs in .env.example as PROSE, not as a "
        f"`{extra[0]}=` line — nothing would ever load it (ADR-0021)."
    )
