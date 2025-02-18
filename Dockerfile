# Use Python 3.9 slim image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    tree \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Debug: Print directory structure and contents before install
RUN echo "Directory structure before install:" && \
    tree -a -I "venv|__pycache__|*.pyc|.git|.env|.cache*"

# Install the package in development mode and verify installation
RUN pip install -e . && \
    pip list | grep shuffify

# Debug: Print directory structure and contents after install
RUN echo "Directory structure after install:" && \
    tree -a -I "venv|__pycache__|*.pyc|.git|.env|.cache*" && \
    echo "App directory contents:" && \
    ls -la app/ && \
    echo "Utils directory contents:" && \
    ls -la app/utils/

# Create directory for Flask sessions
RUN mkdir -p .flask_session

# Set environment variables
ENV FLASK_APP=run.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8080

# Create a startup script
RUN echo '#!/bin/sh\n\
export PYTHONPATH=/app\n\
export PORT=8080\n\
echo "Starting gunicorn on port $PORT..."\n\
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --log-level debug \
    --timeout 120 \
    --workers 2 \
    --preload \
    run:app\n\
' > /app/start.sh && chmod +x /app/start.sh

# Expose the correct port
EXPOSE 8080

# Run the startup script
CMD ["/app/start.sh"] 