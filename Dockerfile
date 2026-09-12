FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_PATH=/app/data/health.db

EXPOSE 10000

# --workers 1 is deliberate: the background checker thread starts once per
# worker process. Multiple workers would duplicate health checks.
# exec forwards container shutdown signals directly to Gunicorn.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 30 app:app"]
