"""Mirror the local ``.env`` into the Kitaru Environment Bucket ``decode-<env>`` (ADR-0015 §7).

    make sync-secrets ENV=staging          # the operator entrypoint
    uv run python scripts/sync_secrets.py --env staging [--env-file .env] [--yes]

An operator script, not library code: it talks to the operator with ``click.echo`` and imports kitaru
directly. It lives outside the ``decode`` import graph, so importing kitaru here cannot touch the
"at ``DECODE_ENV=local``, decode never imports kitaru" invariant.

Three properties are the whole design:

* **ONE full-surface call.** ``kitaru secrets set`` REPLACES the entire key set — setting one key
  destroys the others (verified live). So the push is a single invocation carrying every key: it is a
  **mirror** of the file, never a merge into the bucket. A partial update is not a lesser version of
  this script; it is data loss.
* **Key NAMES only.** Values are never echoed and never logged, at any verbosity — not in the diff, not
  in the confirmation, not in an error. Changed-detection compares values in memory; only names print.
* **One-way.** ``.env`` → Kitaru. There is deliberately no read-back path: dumping a bucket to disk
  would put production secrets in a developer's working tree.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import click
from dotenv import dotenv_values

from decode.config.settings import Settings, environment_bucket_name

# ``DECODE_ENV`` is the bootstrap gate: it decides whether the bucket is read at all, so it can never
# live INSIDE the bucket that it names. A stale ``DECODE_ENV=dev`` sitting in ``decode-prod`` would be a
# pure footgun (a bucket contradicting the environment it is named for), so it is dropped on the way in.
EXCLUDED_KEYS = frozenset({"DECODE_ENV"})

_SET_MISSING = object()  # "this key is absent", distinct from "this key is set to an empty value"


def syncable_keys(env_values: dict[str, str | None]) -> dict[str, str]:
    """The subset of the env file that belongs in the bucket: ``Settings`` fields, minus the gate.

    The config **surface** is the ``Settings`` fields — the Environment-Bucket settings source ignores
    every other key on the way in (``config/settings.py``), so mirroring one would only add a secret to
    the store that nothing can ever read. Process-env-only operator variables (``MODAL_TOKEN_ID``,
    ``DECODE_LOG_FILE``, …) are exactly that case. An empty value is legal and is mirrored.
    """
    fields = Settings.model_fields
    return {
        key: value or ""
        for key, value in env_values.items()
        if key not in EXCLUDED_KEYS and key.lower() in fields
    }


def fetch_bucket(bucket: str) -> dict[str, str] | None:
    """The bucket's current contents, or ``None`` when it does not exist yet (the create path)."""
    from kitaru import get_secret  # lazy: only an actual sync needs kitaru

    try:
        return dict(get_secret(bucket).values)
    except Exception:  # broad on purpose: a missing bucket is a create, not a failure
        return None


def format_diff(current: dict[str, str], desired: dict[str, str]) -> list[str]:
    """The key-NAME-only diff: ``+`` added, ``-`` removed, ``~`` changed, ``=`` unchanged.

    Values are compared here (in memory) to classify a key, and are never part of the returned lines.
    """
    lines = []
    for key in sorted(set(current) | set(desired)):
        before = current.get(key, _SET_MISSING)
        after = desired.get(key, _SET_MISSING)
        if before is _SET_MISSING:
            mark = "+"
        elif after is _SET_MISSING:
            mark = "-"  # replace semantics: a key absent from the file is GONE from the bucket
        else:
            mark = "=" if before == after else "~"
        lines.append(f"  {mark} {key}")
    return lines


def redact(text: str, values: Iterable[str]) -> str:
    """Scrub every known secret value out of a string before it is shown to the operator.

    kitaru echoes the failing argv (``--KEY=value``) on error, so its stderr cannot be printed raw.
    Longest-first, so a value that contains another is not partially unmasked.
    """
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def push(bucket: str, desired: dict[str, str]) -> None:
    """Replace the bucket's whole key set in ONE ``kitaru secrets set`` call (list argv, no shell)."""
    argv = ["kitaru", "secrets", "set", bucket, "--private"]
    argv += [f"--{key}={value}" for key, value in desired.items()]

    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Never print kitaru's raw streams — the argv (values and all) is echoed back in them.
        detail = redact((result.stderr or result.stdout or "").strip(), desired.values())
        raise click.ClickException(
            f"`kitaru secrets set {bucket}` failed (exit {result.returncode}):\n{detail}"
        )


@click.command()
@click.option(
    "--env",
    "decode_env",
    required=True,
    type=click.Choice(["local", "dev", "staging", "prod"]),
    help="The target environment; the bucket name is derived as decode-<env>.",
)
@click.option(
    "--env-file",
    default=".env",
    show_default=True,
    type=click.Path(path_type=Path),
    help="The env file to mirror.",
)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt (CI).")
def main(decode_env: str, env_file: Path, yes: bool) -> None:
    """Mirror an env file into the Kitaru Environment Bucket decode-<env>. One-way; names-only output."""
    if decode_env == "local":
        raise click.ClickException(
            "`local` reads your .env directly — there is nothing to sync. Pick dev, staging or prod."
        )
    if not env_file.is_file():
        raise click.ClickException(f"env file not found: {env_file}")

    file_values = dotenv_values(env_file)
    desired = syncable_keys(file_values)
    skipped = sorted(set(file_values) - set(desired) - EXCLUDED_KEYS)
    if not desired:
        raise click.ClickException(
            f"{env_file} has no key matching a Settings field — refusing to wipe the bucket."
        )

    bucket = environment_bucket_name(decode_env)
    current = fetch_bucket(bucket)

    click.echo(f"Mirroring {env_file} → {bucket} (key names only; values are never printed).")
    if current is None:
        click.echo(f"{bucket} does not exist yet — it will be created.")
    if skipped:
        click.echo(f"Skipped (not a Settings field): {', '.join(skipped)}")
    click.echo("\n".join(format_diff(current or {}, desired)))
    click.echo(
        f"This REPLACES the entire contents of {bucket} with these {len(desired)} key(s) "
        "— `kitaru secrets set` overwrites the whole key set."
    )

    if not yes and not click.confirm("Proceed?", default=False):
        raise click.ClickException(f"Aborted — nothing was written to {bucket}.")

    push(bucket, desired)
    click.echo(f"Mirrored {len(desired)} key(s) into {bucket}.")


if __name__ == "__main__":
    sys.exit(main())
