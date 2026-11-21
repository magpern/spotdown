FROM python:3.11-slim

# ffmpeg is required for transcoding.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Overridden by docker-compose for the worker service.
# 0.0.0.0 inside the container is needed for publishing; compose publishes on 127.0.0.1. Forwarded
# headers are ignored (ADR 0003).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
