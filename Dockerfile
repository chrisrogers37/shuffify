# Use Python 3.9 slim image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    tree \
    && rm -rf /var/lib/apt/lists/*

# Copy package files first
COPY setup.py MANIFEST.in requirements.txt ./

# Copy application files
COPY config.py run.py ./
COPY app app/

# Verify file structure
RUN echo "=== Verifying file structure ===" && \
    tree -a && \
    echo "=== Checking app directory ===" && \
    ls -la app/ && \
    echo "=== Checking app/utils ===" && \
    ls -la app/utils/ && \
    echo "=== Checking app/spotify ===" && \
    ls -la app/spotify/

# Install dependencies and package
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install -e . && \
    echo "=== Installed packages ===" && \
    pip list

# Create directory for Flask sessions
RUN mkdir -p .flask_session

# Set environment variables
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8000

# Verify Python can import our modules
RUN echo "=== Testing imports ===" && \
    python -c "from app import application; print('Successfully imported application')" && \
    python -c "from app.utils.shuffify import shuffle_playlist; print('Successfully imported shuffle_playlist')" && \
    python -c "from config import config; print('Successfully imported config')"

# Create a startup script
RUN echo '#!/bin/sh\n\
echo "=== Environment variables ==="\n\
env | sort\n\
echo "=== Current directory ==="\n\
pwd && ls -la\n\
echo "=== Starting gunicorn ==="\n\
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --log-level debug \
    --timeout 120 \
    --workers 2 \
    --preload \
    run:app\n\
' > /app/start.sh && chmod +x /app/start.sh

# Expose the correct port
EXPOSE 8000

# Run the startup script
CMD ["/app/start.sh"] 