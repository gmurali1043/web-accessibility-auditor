# Hosted (public) deployment. Local use doesn't need this file: just
# `python app.py` as described in the README.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PUBLIC_MODE=1 \
    PORT=7860 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .

# Hugging Face Spaces runs containers as UID 1000.
RUN useradd -m -u 1000 user && chown -R user /app
USER user

EXPOSE 7860

# One worker process: jobs and results live in memory, so every request
# must reach the same process. Threads serve concurrent visitors.
CMD gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT} app:app
