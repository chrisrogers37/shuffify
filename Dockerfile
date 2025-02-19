# Use Python 3.9 slim image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only necessary files
COPY requirements.txt setup.py MANIFEST.in run.py ./
COPY app app/

# Install dependencies and package
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install -e .

# Create directory for Flask sessions
RUN mkdir -p .flask_session

# Set environment variables
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV PORT=8080

# Create a startup script
RUN echo '#!/bin/sh\n\
export PORT=8080\n\
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