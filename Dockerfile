# Jobcraft — app image
# Python + LibreOffice (docx→pdf); LLM calls go directly to an OpenAI-compatible API.
# Vacancy similarity runs on the CPU with a small static model baked into the image.
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

RUN pip install --no-cache-dir uv \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

# The local meaning index (app/job_index.py): a pinned static model, stored as float16.
# Downloaded once here so the running app never fetches anything. The app runs as
# the unprivileged app user, so the files must be world-readable.
COPY docker/fetch_model.py /tmp/fetch_model.py
RUN /app/.venv/bin/python /tmp/fetch_model.py /opt/models/potion-multilingual-128M \
    && chmod -R a+rX /opt/models \
    && rm -rf /root/.cache/huggingface /tmp/fetch_model.py

COPY app ./app
COPY prompts ./prompts
COPY seeds ./seeds
COPY .opencode/skills ./.opencode/skills
COPY docker/entrypoint.sh /usr/local/bin/jobcraft-entrypoint

ENV APP_DATA_DIR=/data \
    APP_RUNTIME_DIR=/data/runtime \
    APP_SEED_DIR=/seed \
    UV_NO_SYNC=1 \
    HOME=/home/app \
    UV_CACHE_DIR=/tmp/uv-cache

EXPOSE 8080
ENTRYPOINT ["jobcraft-entrypoint"]
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
