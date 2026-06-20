# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps for Pillow (works on both amd64 and arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    libpng-dev \
    libwebp-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

WORKDIR /app/src

CMD ["python", "-u", "bot.py"]