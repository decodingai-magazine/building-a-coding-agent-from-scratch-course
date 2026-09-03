"""Mirror the local ``.env`` into the Kitaru Environment Bucket ``decode-<env>`` (ADR-0015 §7).

    make sync-secrets ENV=staging          # the operator entrypoint
    uv run python scripts/sync_secrets.py --env staging [--env-file .env] [--yes]

An operator script, not library code: it talks to the operator with ``click.echo`` and imports kitaru
directly. It lives outside the ``decode`` import graph, so importing kitaru here cannot touch the
"at ``DECODE_ENV=local``, decode never imports kitaru" invariant.

Three properties are the whole design:

* **ONE full-surface call.** A secret's ``values`` map is written whole — ``create`` sets it,
  ``update`` REPLACES it (kitaru's PATCH swaps the entire key set, it does not merge). So the push
  carries every key in one call: it is a **mirror** of the file, never a merge into the bucket. A
  partial update is not a lesser version of this script; it is data loss.
* **Key NAMES only.** Values are never echoed and never logged, at any verbosity — not in the diff, not
  in the confirmation, not in an error. Changed-detection compares values in memory; only names print.
* **One-way.** ``.env`` → Kitaru. There is deliberately no read-back path: dumping a bucket to disk
  would put production secrets in a developer's working tree.

The transport is the kitaru 0.22.2 client (ADR-0019 §5): named secrets on the managed workspace,
reached with the client's own ``KITARU_API_URL`` / ``KITARU_API_KEY`` conventions (else the on-disk
``kitaru login`` store). There is no ``kitaru secrets`` CLI to shell out to any more.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

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


def with_client[T](operation: Callable[[Any], Awaitable[T]]) -> T:
    """Run ``operation`` against a fresh Kitaru client, closing it afterwards.

    The client is imported lazily and constructed INSIDE the coroutine so its httpx pool binds to
    the loop that will use it. This is the script's only kitaru entry point — one place to change
    when the SDK moves again.
    """
    from kitaru.client import KitaruClient

    async def run() -> T:
        client = KitaruClient()
        try:
            return await operation(client)
        finally:
            await client.close()

    return asyncio.run(run())


async def find_secret(client: Any, bucket: str) -> Any:
    """The workspace's secret named ``bucket``, or ``None`` — the resource has no get-by-name."""
    from kitaru.api_models.v1.filter import FilterCondition
    from kitaru.api_models.v1.secret import SecretListParams

    page = await client.api.secrets.list(
        SecretListParams(filter=FilterCondition(field="name", op="eq", value=bucket))
    )
    return next((item for item in page.items if item.name == bucket), None)


def redact(text: str, values: Iterable[str]) -> str:
    """Scrub every known secret value out of a string before it is shown to the operator.

    A rejected write echoes the request back (FastAPI's 422 body carries the offending input), so an
    API error cannot be printed raw. Longest-first, so a value that contains another is not
    partially unmasked.
    """
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def fetch_bucket(bucket: str, known_values: Iterable[str] = ()) -> dict[str, str] | None:
    """The bucket's current contents, or ``None`` when it does not exist yet (the create path).

    A genuinely absent secret is the create path; ANY other failure (unreachable workspace, expired
    login) is reported instead of being swallowed — a silent ``None`` there would print "it will be
    created" and show every key as added, which is a lie about what is in the bucket. The reported
    error is scrubbed of ``known_values`` (the file's values) all the same: names-only holds on
    every path, and the file's values are exactly the ones a re-run expects to find in the bucket.
    """

    async def read(client: Any) -> dict[str, str] | None:
        found = await find_secret(client, bucket)
        if found is None:
            return None
        secret = await client.api.secrets.get(found.id, include_values=True)
        return {key: value.get_secret_value() for key, value in secret.values.items()}

    try:
        return with_client(read)
    except Exception as exc:
        detail = redact(f"{type(exc).__name__}: {exc}", known_values)
        raise click.ClickException(
            f"could not read {bucket} from the Kitaru workspace ({detail}) — "
            "check `kitaru login` / KITARU_API_URL."
        ) from None


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


def push(bucket: str, desired: dict[str, str]) -> None:
    """Write the whole key set in ONE call: create the named secret, or replace its values."""

    async def write(client: Any) -> None:
        from kitaru.api_models.v1.secret import SecretCreateRequest, SecretUpdateRequest

        found = await find_secret(client, bucket)
        if found is None:
            await client.api.secrets.create(SecretCreateRequest(name=bucket, values=desired))
        else:
            await client.api.secrets.update(found.id, SecretUpdateRequest(values=desired))

    try:
        with_client(write)
    except Exception as exc:
        # Never print the raw error — a rejected write echoes the values back inside it.
        detail = redact(f"{type(exc).__name__}: {exc}", desired.values())
        raise click.ClickException(
            f"writing {bucket} to the Kitaru workspace failed — {detail}"
        ) from None


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
    current = fetch_bucket(bucket, desired.values())

    click.echo(f"Mirroring {env_file} → {bucket} (key names only; values are never printed).")
    if current is None:
        click.echo(f"{bucket} does not exist yet — it will be created.")
    if skipped:
        click.echo(f"Skipped (not a Settings field): {', '.join(skipped)}")
    click.echo("\n".join(format_diff(current or {}, desired)))
    click.echo(
        f"This REPLACES the entire contents of {bucket} with these {len(desired)} key(s) "
        "— the write swaps the secret's whole key set, it does not merge into it."
    )

    if not yes and not click.confirm("Proceed?", default=False):
        raise click.ClickException(f"Aborted — nothing was written to {bucket}.")

    push(bucket, desired)
    click.echo(f"Mirrored {len(desired)} key(s) into {bucket}.")


if __name__ == "__main__":
    sys.exit(main())
