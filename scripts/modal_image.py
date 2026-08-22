"""The one image both Modal apps run on, and the fixed layout inside it (ADR-0020 §2).

Two apps need the same container — the Modal Headless App (``scripts/modal_headless.py``, task 142)
and the Modal-hosted Kitaru Worker (``scripts/modal_kitaru_worker.py``, task 145) — so the build
lives here once instead of being copy-pasted into both. It stays in ``scripts/``: this is operator
tooling, outside the ``decode`` import graph.

The layout is the load-bearing part. The Worker spawns replays from an **Agent Version** whose run
spec names :data:`DECODE_BIN` and :data:`HARNESS_HOME` as absolute in-image paths (registered from a
laptop that cannot stat them — ``scripts/register_kitaru_agent.py --skip-bin-check``). A path that
drifts here is not a test failure, it is every replay failing to spawn, hours later, on a machine
nobody is watching. Hence: ONE definition, imported by both scripts and pinned by unit tests on both
sides.

Built in-app with :class:`modal.Image` — no Dockerfile, no registry (ADR-0020 §2):

1. ``debian_slim`` + ``git`` (clones, Hand-back) — the base layer.
2. ``Image.uv_sync()`` — the locked third-party deps, and nothing of this project. The expensive
   layer, and the one that survives every source edit.
3. this repo's source, installed with ``--no-deps`` so layer 2 is re-used verbatim.

``uv_sync`` builds its venv at ``/.uv/.venv``, so both console scripts sit at ONE absolute path no
``PATH`` set-up in any shell can move: ``decode`` for the harness, ``kitaru`` for the Worker.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import modal

REPO_ROOT = Path(__file__).resolve().parents[1]

# Where the repo source is baked, and the venv ``Image.uv_sync()`` builds beside it.
IMAGE_SOURCE_DIR = "/opt/decode"
VENV_DIR = "/.uv/.venv"

# The two console scripts an operator surface ever spawns, at absolute in-image paths.
DECODE_BIN = f"{VENV_DIR}/bin/decode"
KITARU_BIN = f"{VENV_DIR}/bin/kitaru"

# The Harness Home: every harness artifact (``.decode/sessions``, logs, ``.decode/sandbox``) anchors
# here, OUTSIDE any repo checkout (ADR-0012 §6). Also the Kitaru Worker's working dir, because it is
# the cwd every spawned replay inherits.
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


def build_image(*, extra_dirs: Sequence[str] = ()) -> modal.Image:
    """The image both apps run on: locked deps, this repo's source, the fixed directories.

    ``extra_dirs`` are created alongside :data:`HARNESS_HOME` — the headless app's harness-side repo
    clone is the only one so far. They are part of the image rather than a runtime ``mkdir`` for the
    same reason the paths are constants: a directory that only exists when some code remembered to
    create it is a directory that is missing the one time it matters.

    Args:
        extra_dirs: Absolute in-image directories to create besides the Harness Home.

    Returns:
        The image, ready to hand to ``@app.function(image=…)``.
    """
    return (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("git", "curl", "ca-certificates")
        # Locked third-party deps only (uv_sync never installs the project itself) — the cached layer.
        .uv_sync(uv_project_dir=str(REPO_ROOT))
        # decode's own source, on top, installed without deps so the layer above is reused verbatim.
        .add_local_dir(REPO_ROOT, IMAGE_SOURCE_DIR, copy=True, ignore=_SOURCE_IGNORE)
        .run_commands(
            f"/.uv/uv pip install --no-deps --python {VENV_DIR}/bin/python {IMAGE_SOURCE_DIR}"
        )
        .run_commands(f"mkdir -p {' '.join([HARNESS_HOME, *extra_dirs])}")
        # ADR-0020 §4: one config surface, fed by the Secret's process env — never an Environment
        # Bucket, so nothing here imports kitaru at settings load (ADR-0015).
        .env({"DECODE_ENV": "local"})
        # LAST, and it has to be: both apps import THIS module, and a container's sys.path is not the
        # laptop's — without the local ``scripts`` package on it the Function dies at import, before
        # it runs a line (``ModuleNotFoundError: No module named 'scripts'``, found on the worker's
        # first run). Modal refuses any build step after an ``add_local_*``, so this stays the tail.
        .add_local_python_source("scripts")
    )
