# Sentio Sidecar - Python-only image for Kafka streaming backend
# This image contains the Kafka sidecar and web dashboard
# The C++ engine binary should be provided separately or run on host

FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    librdkafka1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy all application files
COPY tools/ /app/tools/
COPY requirements.txt /app/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    chmod +x /usr/local/bin/entrypoint.sh

# Environment variables
ENV MODE=replay \
    TEST_DATE=2025-10-27 \
    KAFKA_BOOTSTRAP_SERVERS=localhost:19092 \
    FLASK_PORT=5001

# Expose webapp port
EXPOSE 5001

# Default command runs the webapp
CMD ["python3", "/app/tools/kafka_monitor_webapp.py"]
