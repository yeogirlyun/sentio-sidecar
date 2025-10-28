# Kafka Sidecar Message Structure Update - v2.0

**Date:** 2025-10-28
**Commit:** ba37aed - "fix(webapp): display ET time in trade history and track position duration"

## Overview

This update enhances the Kafka message structure with improved metadata and fixes critical display issues in the web dashboard.

## Key Changes

### 1. Position Duration Tracking (Critical Fix)
- **Issue:** Position duration always showed "0 bars"
- **Root Cause:** C++ engine doesn't output `entry_time_ms` field
- **Solution:** Python-side tracking using `position_entry_times` dictionary
- **Impact:** Position messages now include accurate `barsHeld` values

### 2. Added `barId` Field (All Topics)
All Kafka messages now include `barId` field (1-391 for intraday):
- **prices** topic: `barId` field added to each price update
- **trades** topic: `barId` field shows when trade executed
- **positions** topic: `barId` field tracks position lifecycle
- **portfolio** topic: `barId` field for portfolio snapshots
- **heartbeat** topic: `barId` field for session tracking

### 3. Enhanced Trade Messages
New optional fields in **trades** topic:
- `stopPrice`: Stop loss price if set
- `targetPrice`: Take profit target if set
- `takeProfitPct`: Take profit percentage if set
- `eventAnnotation`: Trade-specific annotation (sanitized, no legacy "$0 target" text)

### 4. Trade De-duplication
- Merges trades from `trades.jsonl` and Golden DB without duplicates
- Prefers explicit trades.jsonl entries when both sources exist
- Key: (symbol, action, timestamp_ms)

## Message Structure Examples

### Position Message (Enhanced)
```json
{
  "symbol": "AAPL",
  "tsET": "2025-10-27T14:30:00-04:00",
  "barId": 301,
  "hasPosition": true,
  "shares": 100,
  "entryPrice": 150.25,
  "marketPrice": 152.80,
  "unrealizedPnl": 255.00,
  "unrealizedPnlPct": 0.0169,
  "barsHeld": 15,
  "annotation": "Strong momentum, RSI neutral"
}
```

### Trade Message (Enhanced)
```json
{
  "tradeId": "uuid-here",
  "symbol": "AAPL",
  "action": "BUY",
  "tsET": "2025-10-27T09:45:00-04:00",
  "barId": 16,
  "price": 150.25,
  "shares": 100,
  "value": 15025.00,
  "commission": 1.00,
  "pnl": 0.00,
  "pnlPct": 0.00,
  "reason": "ENTRY",
  "stopPrice": 148.00,
  "targetPrice": 153.50,
  "takeProfitPct": 2.0,
  "eventAnnotation": "Breakout confirmed, volume surge",
  "annotation": "Entry setup confirmed"
}
```

### Price Message (Enhanced)
```json
{
  "symbol": "AAPL",
  "tsET": "2025-10-27T14:30:00-04:00",
  "barId": 301,
  "open": 150.25,
  "high": 152.80,
  "low": 150.10,
  "close": 152.50,
  "volume": 1500000,
  "annotation": "Price action bullish"
}
```

## Web Dashboard Fixes

### 1. Trade History Timezone (kafka_monitor.html)
- **Before:** Displayed in browser's local timezone (PST, CST, etc.)
- **After:** Correctly displays Eastern Time (ET)
- **Method:** Direct ISO string parsing instead of `new Date()` conversion

### 2. Position Duration Display
- **Before:** Always showed "0 bars"
- **After:** Shows actual duration like "15 bars", "42 bars"
- **Calculation:** `(current_time_ms - entry_time_ms) / 60000`

## Docker Image Updates

### Image Tag
```
ghcr.io/yeogirlyun/sentio-lite:latest
```

### Platforms Supported
- `linux/amd64` (Intel/AMD 64-bit)
- `linux/arm64` (Apple Silicon, ARM servers)

### Pull Command
```bash
docker pull ghcr.io/yeogirlyun/sentio-lite:latest
```

## Backward Compatibility

✅ **Fully backward compatible** - All existing fields preserved
- Old consumers can ignore new `barId` field
- Old consumers can ignore new trade fields (`stopPrice`, `targetPrice`, `takeProfitPct`)
- `barsHeld` now provides accurate values instead of 0

## Migration Notes

### For Engineers Using the Container

1. **Pull latest image:**
   ```bash
   docker pull ghcr.io/yeogirlyun/sentio-lite:latest
   ```

2. **Restart services:**
   ```bash
   docker-compose down
   docker-compose up -d
   ```

3. **Verify position duration:**
   - Open web dashboard at http://localhost:5001
   - Check positions table shows non-zero "Duration" values
   - Trade history times should show ET (not your local timezone)

### For iOS/Mobile Developers

**New fields available for enhanced UI:**
- Display `barId` to show intraday position in trading session (1-391)
- Show `barsHeld` for "time in position" metric
- Display `stopPrice` and `targetPrice` for risk visualization
- Use `eventAnnotation` for trade-specific signals

## Testing

Tested with:
- ✅ Replay mode (10-27 data)
- ✅ Golden DB integration
- ✅ Web dashboard display
- ✅ Position duration tracking
- ✅ Timezone display (ET)
- ✅ Trade de-duplication

## Files Changed

- `tools/kafka_sidecar.py`: +86 lines (barId, barsHeld, trade enhancements)
- `tools/templates/kafka_monitor.html`: Timezone fix (already committed earlier)
- `docker-sidecar:latest`: Rebuilt with all changes

## Version Info

- **Previous Version:** v1.0 (no barId, barsHeld always 0)
- **Current Version:** v2.0 (barId everywhere, accurate barsHeld, enhanced trades)
- **Git Commit:** ba37aed
- **Build Date:** 2025-10-28

---

For questions or issues, refer to the commit message or contact the development team.
