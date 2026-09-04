# Sahara — the recovery agent, in one container.
#
# One process: the FastAPI app, the webhook receiver, the background tick loop and the
# static vanilla dashboard. There is no job queue and no worker pool by design, so
# there is nothing here to orchestrate — which is why this is a Dockerfile and not a
# compose file.
#
#   docker build -t sahara .
#   docker run -p 8000:8000 sahara
#
# Boots with a seeded, sanitised database and every write route disabled. See
# PUBLIC_DEMO below and app/config.py: the demo mode REFUSES TO BOOT if a provider
# credential is present, so a public deployment cannot be made to move real money by
# adding an env var.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a source change does not re-resolve the whole tree.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY dashboard/ ./dashboard/
COPY docs/ ./docs/
COPY schema.sql README.md ./

# Bake the batch into the image rather than running it at boot.
#
# A container that replays 88 cases on startup is a container whose first request waits
# on a simulation, and whose numbers differ from the ones in the README if anything in
# the environment drifts. Running it HERE means the image either contains a batch whose
# acceptance checks all passed, or the build fails — the checks exit non-zero, and that
# non-zero stops the build.
RUN LLM_PROVIDER=none python scripts/generate_synthetic.py --n 80 --seed 42 \
        --out synthetic_cases.json \
 && LLM_PROVIDER=none python scripts/run_batch.py --cases synthetic_cases.json \
        --db /app/recovery.db --seed 42 --holdout 0.35

# Non-root, and it owns only what it needs to read.
RUN useradd --create-home --uid 10001 sahara && chown -R sahara:sahara /app
USER sahara

# The demo defaults. Every one of these is a REFUSAL, and each is enforced in code
# rather than by this file alone — a container is a deployment detail and the bounds of
# the agent are source code.
ENV PUBLIC_DEMO=true \
    CONTROL_API_ENABLED=false \
    LLM_PROVIDER=none \
    VOICE_REAL_SEND_ENABLED=false \
    DB_PATH=/app/recovery.db

EXPOSE 8000

# No shell form: the app must be PID 1 so a SIGTERM reaches uvicorn and the tick loop
# gets its shutdown handler rather than being killed after the grace period.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
