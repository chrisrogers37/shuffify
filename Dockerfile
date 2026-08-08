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

# Upgrade pip, setuptools, and wheel to fix security vulnerabilities
# CVE-2025-8869 + CVE-2026-1703 (pip >=26.0), CVE-2024-6345 (setuptools), CVE-2026-24049 (wheel)
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
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Drop root privileges: run the app as the non-root 'nobody' user.
# The .flask_session dir above is chowned to nobody:nogroup so the filesystem
# session fallback stays writable; gunicorn binds :8000 (>1024, no root needed).
USER nobody

# Apply migrations before the server starts, on any platform that honours
# ENTRYPOINT.
#
# CORRECTION -- the original rationale here was wrong, and the error was the
# proximate cause of a production incident. It claimed ENTRYPOINT was chosen
# so the migration step "survives a platform-level run-command override,
# because those replace CMD". DigitalOcean App Platform's `run_command`
# replaces the ENTRYPOINT too, so on production this line is inert and the
# migration step has never run. See scripts/docker-entrypoint.sh and
# https://github.com/chrisrogers37/shuffify/issues/531.
#
# Keep it: it is correct for `docker run`, docker-compose, and any platform
# that does honour ENTRYPOINT. Just do not read it as the production path.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--preload", "run:app"]