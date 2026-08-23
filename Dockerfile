# Multi-stage build: compile the wheel + dependencies in a builder stage,
# then copy only the installed site-packages/console-scripts into a
# minimal, non-root runtime image. No secrets are baked in — every
# credential in .env.example is supplied at deploy time via environment
# variables.

FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

# Uses the base image's own pinned pip rather than an unpinned
# `pip install --upgrade pip`, to keep the build's tool versions
# reproducible and avoid pulling in whatever pip happens to be latest at
# build time.
RUN python -m pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime

RUN groupadd --system rockygpt && useradd --system --gid rockygpt --no-create-home rockygpt

WORKDIR /app

# The installed package (site-packages + the `rockygpt-brain` console
# script) is the deployed artifact; there is no separate `COPY src ./src`
# here since that would just duplicate what installation already placed
# under /usr/local.
COPY --from=builder /install /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

USER rockygpt

EXPOSE 8000

# Reads PORT from the environment in Python (os.environ), not shell
# expansion, so this works the same regardless of exec/shell form and
# always checks whatever port the container was actually configured with.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request; port=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2).status == 200 else 1)"

# rockygpt-brain (pyproject.toml's console_scripts entry point) calls
# main.run(), which reads HOST/PORT from Settings at startup — so this
# CMD is correct regardless of what HOST/PORT are set to at deploy time,
# unlike a hardcoded `uvicorn ... --host --port` invocation would be.
CMD ["rockygpt-brain"]
