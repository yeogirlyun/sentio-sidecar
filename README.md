# Sentio Sidecar - Kafka Streaming Backend

**Unified repository for Sentio trading system's Kafka streaming backend**

## Overview

This repository contains the complete Kafka sidecar system that streams trading data in real-time:
- Kafka message producer (Python sidecar)
- Web dashboard for monitoring
- Market data management
- Docker images for easy deployment

## Quick Start

### Using Docker (Recommended)

```bash
docker pull ghcr.io/yeogirlyun/sentio-sidecar:latest
docker run -d -p 5001:5001 ghcr.io/yeogirlyun/sentio-sidecar:latest
```

Visit: http://localhost:5001

### Manual Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run webapp
python tools/kafka_monitor_webapp.py

# Run sidecar
python tools/kafka_sidecar.py --mode replay --test-date 10-27
```

## Components

### 1. Kafka Sidecar (`tools/kafka_sidecar.py`)
- Publishes real-time trading data to Kafka
- Supports replay and live modes
- Integrates with Golden DB
- **v2.0 Features:**
  - Position duration tracking (`barsHeld`)
  - `barId` field for session tracking
  - Enhanced trade messages with risk fields

### 2. Web Dashboard (`tools/kafka_monitor_webapp.py`)
- Real-time SSE streaming
- Trade history with ET timezone
- Position monitoring with duration
- Portfolio metrics
- Interactive charts

### 3. Market Data Manager (`tools/market_data_manager.py`)
- Fetches and caches market data
- Supports multiple data sources
- Historical data replay

## Kafka Message Structure (v2.0)

### Position Message
```json
{
  "symbol": "AAPL",
  "tsET": "2025-10-27T14:30:00-04:00",
  "barId": 301,
  "barsHeld": 15,
  "shares": 100,
  "entryPrice": 150.25,
  "unrealizedPnl": 255.00
}
```

### Trade Message
```json
{
  "symbol": "AAPL",
  "action": "BUY",
  "barId": 16,
  "price": 150.25,
  "stopPrice": 148.00,
  "targetPrice": 153.50
}
```

See `KAFKA_MESSAGE_UPDATE_v2.md` for full API documentation.

## Docker Deployment

### Build Multi-Platform Image
```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --file Dockerfile \
  --tag ghcr.io/yeogirlyun/sentio-sidecar:latest \
  --push \
  .
```

### Run with Docker Compose
```bash
docker-compose up -d
```

## Environment Variables

- `KAFKA_BOOTSTRAP`: Kafka bootstrap servers (default: localhost:19092)
- `MODE`: Operation mode - `replay` or `live`
- `TEST_DATE`: Date for replay mode (format: MM-DD)
- `POLYGON_API_KEY`: Polygon.io API key for live data

## Repository Structure

```
sentio-sidecar/
├── tools/
│   ├── kafka_sidecar.py         # Main Kafka producer
│   ├── kafka_monitor_webapp.py  # Web dashboard
│   ├── market_data_manager.py   # Market data fetching
│   └── templates/
│       └── kafka_monitor.html   # Dashboard UI
├── docker/
│   ├── Dockerfile               # Multi-stage Docker build
│   ├── docker-compose.yml       # Compose configuration
│   └── entrypoint.sh            # Container entrypoint
├── docs/
│   ├── KAFKA_MESSAGE_UPDATE_v2.md
│   └── DEPLOYMENT_GUIDE.md
├── requirements.txt
└── README.md
```

## Version History

### v2.0 (2025-10-28)
- ✅ Fixed position duration tracking (was always "0 bars")
- ✅ Added `barId` field to all Kafka messages
- ✅ Enhanced trade messages with risk management fields
- ✅ Fixed webapp to display Eastern Time (not local)
- ✅ Multi-platform Docker support (amd64 + arm64)

### v1.0
- Initial release with basic Kafka streaming

## For iOS/Mobile Developers

This sidecar provides real-time trading data via Kafka topics:
- `sentio.prices` - Real-time price updates
- `sentio.trades` - Trade execution events
- `sentio.positions` - Current positions
- `sentio.portfolio` - Portfolio metrics

Connect your mobile app to the Kafka stream for live updates.

## Contributing

This is a consolidated repository - all components in one place for easier maintenance.

## License

Proprietary - Internal use only

## Support

For issues or questions, contact the development team or create an issue in this repository.
