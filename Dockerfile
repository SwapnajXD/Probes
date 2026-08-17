FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# --workers 1 is deliberate: the background checker thread starts once per
# worker process. Multiple workers would mean multiple threads pinging the
# same services in parallel. (When we cover the Async Job Queue pattern
# later, this is exactly the kind of thing that gets split into its own
# worker Deployment instead of living inside the web process.)
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8080", "app:app"]
