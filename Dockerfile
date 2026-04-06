FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY spotify_exporter.py auth.py web.py ./
COPY shared/ ./shared/
COPY templates/ ./templates/

EXPOSE 8080

# Create directories for cache and exports
RUN mkdir -p /app/exports /app/.cache

# Set cache directory for spotipy
ENV SPOTIPY_CACHE_PATH=/app/.cache/.spotipy_cache

CMD ["python", "-u", "spotify_exporter.py"]
