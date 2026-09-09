# Cloud Run build. Kept deliberately plain — no multi-stage, no compiled deps.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependency layer first so app edits don't invalidate the pip cache.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

# Cloud Run sets $PORT at runtime and it is NOT guaranteed to be 8080.
# Shell form so the variable is expanded by the shell, not passed literally.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
