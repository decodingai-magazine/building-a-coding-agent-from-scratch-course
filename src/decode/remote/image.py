"""The image the Modal Headless App runs on, and the fixed layout inside it (ADR-0020 §2).

Kept apart from :mod:`decode.remote.app` because the launcher needs pieces of it without importing
``modal``: :func:`repo_root_error` is checked by ``decode remote deploy`` on the laptop, and the
in-image paths are read by :mod:`decode.remote.headless`. It is deploy-time code otherwise: the
image itself is built by ``modal deploy``, never by the REPL or ``decode run``.

The layout is the load-bearing part: :data:`DECODE_BIN` and :data:`HARNESS_HOME` are the absolute
in-image paths every remote run spawns from and anchors its harness artifacts to. ONE definition,
pinned by unit tests.

Built in-app with :class:`modal.Image` — no Dockerfile, no registry (ADR-0020 §2):

1. ``debian_slim`` + ``git`` (clones, Hand-back) — the base layer.
2. ``Image.uv_sync()`` — the locked third-party deps, and nothing of this project. The expensive
   layer, and the one that survives every source edit.
3. this repo's source, installed with ``--no-deps`` so layer 2 is re-used verbatim.

``uv_sync`` builds its venv at ``/.uv/.venv``, so the ``decode`` console script sits at ONE
absolute path no ``PATH`` set-up in any shell can move.

The build needs the repo CHECKOUT (``pyproject.toml`` + ``uv.lock`` + ``src/``): :data:`REPO_ROOT`
is resolved from this file, so an installed wheel cannot deploy — ``decode remote deploy`` says so
in one line (:func:`repo_root_error`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if (
    TYPE_CHECKING
):  # ``modal`` is imported inside ``build_image`` — deploy time only, never by the CLI.
    import modal

# ``src/decode/remote/image.py`` → the checkout root, three levels up.
REPO_ROOT = Path(__file__).resolve().parents[3]

# What a deploy from outside a checkout says — ``uv_sync`` needs the lockfile, the source layer
# needs ``src/``; an installed wheel has neither.
NOT_A_CHECKOUT_FORMAT = (
    "Decode: the Modal image is built from the decode repo checkout, and {root} holds no "
    "pyproject.toml — run `decode remote deploy` from a clone of the repository."
)

# Where the repo source is baked, and the venv ``Image.uv_sync()`` builds beside it.
IMAGE_SOURCE_DIR = "/opt/decode"
VENV_DIR = "/.uv/.venv"

# The console script a remote run spawns, at an absolute in-image path.
DECODE_BIN = f"{VENV_DIR}/bin/decode"

# The Harness Home: every harness artifact (``.decode/sessions``, logs, ``.decode/sandbox``) anchors
# here, OUTSIDE any repo checkout (ADR-0012 §6).
HARNESS_HOME = "/harness"

# Build artefacts and local state that must never be baked into the image.
_SOURCE_IGNORE = [
    "**/.git",
    "**/.venv",
    "**/.decode",
    "**/__pycache__",
    "**/*.pyc",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/node_modules",
]


def repo_root_error(root: Path = REPO_ROOT) -> str | None:
    """ONE friendly line if ``root`` is not the repo checkout the image is built from, else ``None``."""
    if (root / "pyproject.toml").is_file() and (root / "uv.lock").is_file():
        return None
    return NOT_A_CHECKOUT_FORMAT.format(root=root)


def extra_packages_command(packages: Sequence[str]) -> str:
    """The ``uv pip install`` that puts ``packages`` into the SAME venv ``uv_sync`` built.

    Not ``Image.uv_pip_install`` — that targets the container's interpreter, and the Functions run
    on ``/.uv/.venv``'s. Same idiom as the source install one layer down.
    """
    return f"/.uv/uv pip install --python {VENV_DIR}/bin/python {' '.join(packages)}"


def build_image(
    *,
    decode_env: str,
    extra_dirs: Sequence[str] = (),
    extra_packages: Sequence[str] = (),
) -> modal.Image:
    """The headless app's image: locked deps, this repo's source, the fixed directories.

    ``extra_dirs`` are created alongside :data:`HARNESS_HOME` — the headless app's harness-side repo
    clone is the only one so far. They are part of the image rather than a runtime ``mkdir`` for the
    same reason the paths are constants: a directory that only exists when some code remembered to
    create it is a directory that is missing the one time it matters.

    ``extra_packages`` are pip requirements the app needs that decode itself does not — the
    webhook endpoint needs ``fastapi``. They install into the same venv, between the locked deps and
    the source, so a source edit still rebuilds only the tail.

    ``decode_env`` is BAKED into the image (ADR-0021 §3). It is a deploy-time decision, read from the
    deploying laptop's env, and it already picked this app's name and Secret name — baking it keeps
    those three from disagreeing. A Secret is credentials; the environment is not a credential.

    Args:
        decode_env: The environment this deployment is, baked as ``DECODE_ENV``.
        extra_dirs: Absolute in-image directories to create besides the Harness Home.
        extra_packages: Extra pip requirements for this app's Functions.

    Returns:
        The image, ready to hand to ``@app.function(image=…)``.
    """
    import modal  # deploy-time only: ``decode remote deploy`` must not cost the REPL a modal import

    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("git", "curl", "ca-certificates")
        # Locked third-party deps only (uv_sync never installs the project itself) — the cached layer.
        .uv_sync(uv_project_dir=str(REPO_ROOT))
    )
    if extra_packages:
        image = image.run_commands(extra_packages_command(extra_packages))
    return (
        image
        # decode's own source, on top, installed without deps so the layer above is reused verbatim.
        .add_local_dir(REPO_ROOT, IMAGE_SOURCE_DIR, copy=True, ignore=_SOURCE_IGNORE)
        .run_commands(
            f"/.uv/uv pip install --no-deps --python {VENV_DIR}/bin/python {IMAGE_SOURCE_DIR}"
        )
        .run_commands(f"mkdir -p {' '.join([HARNESS_HOME, *extra_dirs])}")
        # ``DECODE_ENV`` is baked, not carried by the Secret (ADR-0021 §3): it is the same deploy-time
        # value that named this app and its Secret, so the three cannot drift. It changes names only
        # — the Secret's process env is still the whole config surface (ADR-0021 §1).
        .env({"DECODE_ENV": decode_env})
    )
