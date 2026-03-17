# ── Python application ───────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

# gcc is needed by some native extensions (e.g. cryptography used by jira)
# weasyprint runtime dependencies (cairo, pango, gdk-pixbuf, fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libcairo2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY rules.yaml .

# Create an empty token cache file so Docker volume mount works
# before setup_onedrive.py has been run. It will be overwritten by the mount.
RUN touch onedrive_token_cache.json

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
