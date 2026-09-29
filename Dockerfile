FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Kyiv \
    DB_PATH=/app/data/ads.db

# tzdata потрібна, щоб datetime.now() у базі писав київський час, а не UTC
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Запуск не від root; UID 1000 — щоб папку ./data на хості можна було віддати цьому користувачу
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin bot

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=bot:bot . .
RUN mkdir -p /app/data && chown bot:bot /app/data

USER bot

VOLUME ["/app/data"]

CMD ["python", "bot.py"]
