FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
    HOME=/app/playwright-data

WORKDIR /app

# Chromium is used only by adapters whose public result page requires a real browser.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app \
    && adduser --system --ingroup app app

COPY pyproject.toml README.md /app/
COPY app /app/app
COPY scripts /app/scripts
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /app/artifacts /app/playwright-data && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
