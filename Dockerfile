# Jobcraft — app image
# Python + LibreOffice (docx→pdf); LLM calls go directly to an OpenAI-compatible API.
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

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY prompts ./prompts
COPY seeds ./seeds
COPY .opencode/skills ./.opencode/skills

ENV APP_DATA_DIR=/data \
    APP_RUNTIME_DIR=/data/runtime \
    APP_SEED_DIR=/seed \
    UV_NO_SYNC=1

EXPOSE 8080
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
