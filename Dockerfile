FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_PATH=/app/data/health.db

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/data \
    && chown app:app /app/data

USER app

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '10000') + '/health/live', timeout=2)"

# --workers 1 is deliberate: the background checker thread starts once per
# worker process. Multiple workers would duplicate health checks.
# exec forwards container shutdown signals directly to Gunicorn.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 30 app:app"]
