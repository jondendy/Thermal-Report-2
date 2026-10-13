FROM python:3.12-slim

# exiftool is required by flirimageextractor 1.3.0 to read the radiometric data
RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app/data
USER appuser

ENV PORT=5000 STORAGE_BACKEND=local STORAGE_ROOT=/app/data
EXPOSE 5000
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 300 wsgi:app"]
