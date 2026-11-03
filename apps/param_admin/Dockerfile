# Build from the repository root:  docker build -f apps/param_admin/Dockerfile .
# Configured only through environment variables, so the same image can run elsewhere later.
FROM python:3.12-slim

# uv, pinned to the version the project is developed with.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so this layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-default-groups --group app

# Then the package and the app.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-default-groups --group app --no-editable
COPY apps/param_admin ./apps/param_admin

RUN useradd --system --no-create-home app
USER app

WORKDIR /app/apps/param_admin
EXPOSE 8050

# One worker with threads: the idle tracker behind /idle lives in the process, so every request
# has to reach the same one.
CMD ["gunicorn", "--bind", "0.0.0.0:8050", "--workers", "1", "--threads", "4", "app:server"]
