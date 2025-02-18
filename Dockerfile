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

# Debug: Print directory structure and contents
RUN echo "Directory structure:" && \
    tree -a -I "venv|__pycache__|*.pyc|.git|.env|.cache*" && \
    echo "App directory contents:" && \
    ls -la app/ && \
    echo "Utils directory contents:" && \
    ls -la app/utils/ && \
    echo "Python path:" && \
    python -c "import sys; print('\n'.join(sys.path))"

# Create directory for Flask sessions
RUN mkdir -p .flask_session

# Set environment variables
ENV FLASK_APP=run.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Test imports
RUN python -c "from app.utils.shuffify import shuffle_playlist; print('Successfully imported shuffle_playlist')"

# Expose port
EXPOSE 8000

# Run gunicorn with debug logging
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--log-level", "debug", "run:app"] 