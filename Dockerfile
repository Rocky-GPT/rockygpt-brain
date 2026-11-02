# The production Brain image (render.yaml). No secrets are baked in: every
# credential in .env.example arrives as an environment variable at deploy time.

FROM python:3.13-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md requirements.lock ./
COPY src ./src

# Exactly the locked versions (hash-checked), the same ones the laptop and CI install, then the
# Brain itself without letting pip pick any newer dependency.
RUN python -m pip install --no-cache-dir --prefix=/install --require-hashes -r requirements.lock \
 && python -m pip install --no-cache-dir --prefix=/install --no-deps .

FROM python:3.13-slim AS runtime

RUN groupadd --system rockygpt && useradd --system --gid rockygpt --no-create-home rockygpt

WORKDIR /app

# The installed package includes provider-release.json and the current runtime code.
COPY --from=builder /install /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER rockygpt

EXPOSE 8000

# Shell form so Render's PORT expands; exec hands uvicorn the stop signal.
CMD exec uvicorn rockygpt_brain.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
