# Use Python 3.12 slim image (StrEnum requires 3.11+)
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements/ requirements/

# Upgrade pip, setuptools, and wheel to patched releases
RUN pip install --no-cache-dir --upgrade "pip>=26.0" "setuptools>=78.1.1" "wheel>=0.46.2"

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements/prod.txt

# Copy application code
COPY . .

# Create session directory with proper permissions
RUN mkdir -p .flask_session && \
    chown -R nobody:nogroup .flask_session && \
    chmod 755 .flask_session

# Set environment variables
ENV FLASK_APP=run.py
ENV APP_CONFIG=production

# Expose port
EXPOSE 8000

# Add health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "scripts/container-health.py"]

# Drop root privileges: run the app as the non-root 'nobody' user.
# The .flask_session dir above is chowned to nobody:nogroup so the filesystem
# session fallback stays writable; gunicorn binds :8000 (>1024, no root needed).
USER nobody

# Startup only forwards the selected service process. Run scripts/pre-deploy.sh
# separately before starting services that require the upgraded schema.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]

# Run the application
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
