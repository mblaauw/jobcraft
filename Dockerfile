# Jobcraft — app image
# Python + LibreOffice (docx→pdf) + pinned OpenCode v2 runtime.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates bash unzip libstdc++6 libgcc-s1 \
      libreoffice-writer \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation \
      poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# OpenCode v2, pinned. Installs to /root/.opencode/bin.
RUN curl -fsSL https://opencode.ai/v2/install | bash -s -- --version 2.0.28 \
    && /root/.opencode/bin/opencode --version
ENV PATH="/root/.opencode/bin:${PATH}"

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY agents ./agents
COPY prompts ./prompts
COPY seeds ./seeds
COPY .opencode/skills ./.opencode/skills

ENV APP_DATA_DIR=/data \
    APP_RUNTIME_DIR=/data/runtime \
    APP_SEED_DIR=/seed \
    OPENCODE_HOST=127.0.0.1 \
    OPENCODE_PORT=4190 \
    UV_NO_SYNC=1

EXPOSE 8080
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
