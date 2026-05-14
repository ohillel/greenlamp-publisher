FROM python:3.11-slim

WORKDIR /app

# Install requirements first — separate layer so rebuilds are fast when only
# code changes (requirements layer is cached as long as requirements.txt doesn't change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium and all its system-level OS dependencies.
# `--with-deps` runs apt-get internally to install libnss3, libatk, libgbm, etc.
# This must happen AFTER `pip install playwright` puts the CLI on the PATH.
RUN playwright install chromium --with-deps

# Copy application code.
# Sessions and debug screenshots are excluded via .dockerignore.
COPY . .

# Ensure the scraper runtime directories exist in the image.
# They will be populated at runtime (login sessions) or on demand (screenshots).
RUN mkdir -p scraper/sessions scraper/debug_screenshots

EXPOSE 8000

# $PORT is injected by Railway at runtime. Fall back to 8000 for local Docker use.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
