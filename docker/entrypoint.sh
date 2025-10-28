#!/bin/bash
set -e

echo "Starting Sentio Sidecar..."
echo "Mode: ${MODE}"
echo "Kafka Bootstrap: ${KAFKA_BOOTSTRAP_SERVERS}"

# Default to running the webapp
exec python3 /app/tools/kafka_monitor_webapp.py
