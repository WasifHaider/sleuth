# Backend-only image (see CLAUDE.md / README "Running locally" for context).
# The frontend (web/) is deployed separately on Vercel, not containerized here.
#
# Needs a real C toolchain, not just python:3.11-slim as-is: tree-sitter-vue
# (vendor/tree-sitter-vue, see its NOTICE.md) has no prebuilt wheel and
# compiles its grammar from source on install. Skipping build-essential here
# reproduces the exact "no MSVC Build Tools" failure CLAUDE.md documents for
# the native-Windows .venv-win — this image must build clean without that
# workaround since it has no separate WSL-equivalent to fall back to.
FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements + the vendored tree-sitter-vue source before the rest of
# the app so this (slow) layer is only rebuilt when dependencies actually
# change, not on every source edit.
COPY requirements.txt ./
COPY vendor/ ./vendor/
RUN pip install --no-cache-dir -r requirements.txt

COPY sleuth/ ./sleuth/
COPY schema.sql ./schema.sql

EXPOSE 8000

# --host 0.0.0.0 is mandatory inside Docker (see deployment guide) — without
# it uvicorn only listens on localhost inside the container, unreachable
# from the host's port mapping.
CMD ["uvicorn", "sleuth.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
