FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY templates ./templates
COPY scripts ./scripts

# Seed database baked into the image (fly machines reset the rootfs to the
# image on every restart/deploy, so this snapshot IS the persistent state).
# Refresh before deploying (sftp get refuses to overwrite, hence the rm):
#   rm backups/fly_seed.db && fly ssh sftp get /app/app.db backups/fly_seed.db
COPY backups/fly_seed.db ./app.db

ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
