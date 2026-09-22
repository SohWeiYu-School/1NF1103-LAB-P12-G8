FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached separately from code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create runtime directories and assign ownership before switching to non-root user
RUN mkdir -p /app/data/runtime/cache /app/logs \
    && useradd --create-home --shell /bin/sh appuser \
    && chown -R appuser:appuser /app/data /app/logs

USER appuser

CMD ["python", "main.py"]
