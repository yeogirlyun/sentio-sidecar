#!/bin/sh
set -e

# Ensure dirs
mkdir -p /app/logs/dashboard

MODE_UPPER=$(echo "$MODE" | tr '[:lower:]' '[:upper:]')

# Legacy REPLAY mode (batch processing + replay)
# Kept for backward compatibility, but not recommended
if [ "$MODE_UPPER" = "REPLAY" ]; then
  echo "[unified] Starting REPLAY engine run for ${TEST_DATE}"
  STRATEGY_LOWER=$(echo "$STRATEGY" | tr '[:upper:]' '[:lower:]')
  /usr/local/bin/sentio_lite mock --strategy ${STRATEGY_LOWER} --date ${TEST_DATE} \
    --data-dir ${DATA_DIR} --extension ${EXT} --no-dashboard
  echo "[unified] Engine replay completed; launching sidecar replay publisher"
  exec python3 /app/tools/kafka_sidecar.py --mode replay
fi

# New MOCK-LIVE mode (real-time bar-by-bar simulation)
# This mode runs the engine first to generate trades, then streams to Kafka
# Note: For true real-time, we'd feed bars to engine via stdin/ZMQ (future enhancement)
if [ "$MODE_UPPER" = "MOCK-LIVE" ] || [ "$MODE_UPPER" = "MOCK_LIVE" ]; then
  echo "[unified] Starting MOCK-LIVE real-time simulation for ${TEST_DATE}"
  if [ -z "$TEST_DATE" ]; then
    echo "TEST_DATE is required for mock-live mode" >&2
    exit 1
  fi

  # Step 1: Run engine to generate trades.jsonl
  echo "[unified] Running engine to generate trades..."

  # Remove stale results files to ensure fresh generation
  rm -f /app/results.json /app/trades.jsonl /app/output/results.json /app/output/trades.jsonl

  STRATEGY_LOWER=$(echo "$STRATEGY" | tr '[:upper:]' '[:lower:]')
  /usr/local/bin/sentio_lite mock --strategy ${STRATEGY_LOWER} --date ${TEST_DATE} \
    --data-dir ${DATA_DIR} --extension ${EXT} --no-dashboard

  # Step 2: Wait for Kafka to be ready
  echo "[unified] Waiting for Kafka to be ready..."
  max_wait=30
  elapsed=0
  while [ $elapsed -lt $max_wait ]; do
    if python3 -c "from confluent_kafka.admin import AdminClient; AdminClient({'bootstrap.servers': '${KAFKA_BOOTSTRAP_SERVERS}'}).list_topics(timeout=1)" 2>/dev/null; then
      echo "[unified] Kafka is ready!"
      break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  if [ $elapsed -ge $max_wait ]; then
    echo "[unified] WARNING: Kafka not ready after ${max_wait}s, proceeding anyway..."
  fi

  # Step 3: Stream results to Kafka in real-time
  echo "[unified] Streaming results to Kafka..."
  exec python3 /app/tools/kafka_sidecar.py \
    --mode replay \
    --results /app/results.json \
    --golden /app/golden_db.json \
    --trades /app/trades.jsonl \
    --speed-ms "${REPLAY_SPEED_MS:-1000}"
fi

# LIVE mode (real-time Polygon WebSocket)
if [ "$MODE_UPPER" = "LIVE" ]; then
  echo "[unified] Starting LIVE real-time trading with Polygon feed"
  if [ -z "$POLYGON_API_KEY" ]; then
    echo "POLYGON_API_KEY is required for live mode" >&2
    exit 1
  fi
  exec python3 /app/tools/realtime_kafka_bridge.py \
    --mode live \
    --polygon-key "${POLYGON_API_KEY}"
fi

echo "Unknown MODE=${MODE}; expected replay, mock-live, or live" >&2
exit 1


