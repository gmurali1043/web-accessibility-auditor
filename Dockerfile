# Hosted (public) deployment, e.g. on Google Cloud Run. Local use doesn't
# need this file: just `python app.py` as described in the README.
FROM python:3.12-slim

# RUN_JOBS_INLINE: Cloud Run only gives the container CPU while a request
# is open, so audits run inside the request rather than in the background.
# PORT is overridden by the host (Cloud Run sets 8080).
ENV PYTHONUNBUFFERED=1 \
    PUBLIC_MODE=1 \
    RUN_JOBS_INLINE=1 \
    PORT=8080 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd -m -u 1000 user && chown -R user /app
USER user

EXPOSE 8080

# One worker process: jobs and results live in memory, so every request
# must reach the same process (and the host must run a single instance).
# Threads serve concurrent visitors; --timeout 0 because an inline audit
# can legitimately keep a request open for several minutes.
CMD gunicorn --workers 1 --threads 8 --timeout 0 --bind 0.0.0.0:${PORT} app:app
