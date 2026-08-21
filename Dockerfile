# Fantasy Football Discord Bot - Dockerfile
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY gamedaybot/ ./gamedaybot/
COPY dev/ ./dev/

# Runtime config (Discord tokens, ESPN cookies, league settings) is supplied
# via docker-compose's env_file (config.env), not baked into the image --
# keeps secrets out of image layers/history.

# Create a non-root user for security
RUN mkdir -p /app/data
RUN useradd --create-home --shell /bin/bash botuser
RUN chown -R botuser:botuser /app
USER botuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/', timeout=5).status == 200 else 1)" || exit 1

# Run the bot + dashboard
CMD ["python", "gamedaybot/run.py"]
