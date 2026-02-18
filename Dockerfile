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
ENV FLASK_ENV=production

# Expose port
EXPOSE 8000

# Add health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--preload", "run:app"]