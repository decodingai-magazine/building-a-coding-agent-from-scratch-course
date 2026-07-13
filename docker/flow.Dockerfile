# The flow container for `decode run` on a remote Kitaru stack (INFRA.md §4).
#
# Kitaru/ZenML layers the flow code and its entrypoint on top of this image, so all this has to do
# is install decode and its dependencies. `git` is here because the Workspace is a `git clone` and
# Hand-back shells out to git (ADR-0012 §8).
#
# The `remote` dependency group is mandatory, not optional: the flow container loads the active
# stack, and ZenML only registers the `gcp` connector/artifact-store flavors when EVERY package in
# its gcp integration list is importable.
FROM python:3.12-slim

# UV_SYSTEM_PYTHON: ZenML layers its own `uv pip install -r .zenml_stack_integration_requirements`
# on top of this image WITHOUT `--system`, and uv refuses to install when it finds no virtualenv
# (exit 2). This points uv at the system interpreter instead of a venv it will never find.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_SYSTEM_PYTHON=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /decode

# Dependencies first (cached across code-only edits), then the package itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --group remote --no-emit-project \
    --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt

COPY src ./src
RUN uv pip install --system --no-deps .
