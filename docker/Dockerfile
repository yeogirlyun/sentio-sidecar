# Unified sidecar: builds engine + runs Python sidecar (replay/live)

# ============================
# Builder image (engine)
# ============================
FROM debian:bookworm AS builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    pkg-config \
    libeigen3-dev \
    librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN rm -rf build && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j

# ============================
# Runtime image (engine + python sidecar)
# ============================
FROM python:3.11-slim AS runtime

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libstdc++6 \
    libgcc-s1 \
    libeigen3-dev \
    librdkafka1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy engine binary
COPY --from=builder /app/build/sentio_lite /usr/local/bin/sentio_lite

# Copy repo for tools and configs
COPY . /app

# Python deps for sidecar
RUN pip install --no-cache-dir \
    confluent-kafka \
    polygon-api-client

# Defaults (override at runtime)
ENV MODE=replay \
    STRATEGY=sigor \
    TEST_DATE=2025-10-22 \
    REPLAY_SPEED_MS=60000 \
    HEARTBEAT_INTERVAL_MS=10000 \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    TOPIC_PREFIX=sentio. \
    DATA_DIR=/app/data \
    EXT=.bin

# Note: Removed VOLUME directive to allow config files to be baked into image
# Volumes are defined in docker-compose.yml instead

# Entrypoint
COPY docker/entrypoint.unified.sh /usr/local/bin/entrypoint.unified.sh
RUN chmod +x /usr/local/bin/entrypoint.unified.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.unified.sh"]


