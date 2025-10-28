# Kafka Message & WebApp Update - v2.0 Brief

**Date:** 2025-10-28
**Version:** 2.0
**Docker Image:** `ghcr.io/yeogirlyun/sentio-lite:latest`

## Kafka Message Changes

### 1. New Field: `barId` (All Topics)
- **Added to:** prices, trades, positions, portfolio, heartbeat
- **Type:** Integer (1-391 for intraday trading session)
- **Purpose:** Track intraday bar progression consistently across all messages
- **Example:** `"barId": 301` means 301st minute of trading day

### 2. Position Messages - Duration Tracking
**Problem:** `barsHeld` always showed 0
**Solution:** Python-side tracking of position entry times
**Result:** Accurate duration like "15 bars", "42 bars"

```json
{
  "symbol": "AAPL",
  "barsHeld": 15,  // ← NOW ACCURATE (was always 0)
  "entryPrice": 150.25,
  "unrealizedPnl": 255.00
}
```

### 3. Trade Messages - Risk Management Fields
**New optional fields:**
- `stopPrice`: Stop loss price
- `targetPrice`: Take profit target
- `takeProfitPct`: Take profit percentage

```json
{
  "symbol": "AAPL",
  "action": "BUY",
  "price": 150.25,
  "stopPrice": 148.00,      // ← NEW
  "targetPrice": 153.50,    // ← NEW
  "takeProfitPct": 2.0      // ← NEW
}
```

### 4. Trade De-duplication
- Merges trades from `trades.jsonl` + Golden DB
- Eliminates duplicates using key: (symbol, action, timestamp_ms)
- Prefers explicit trades.jsonl entries when both sources exist

## WebApp Changes

### 1. Trade History - Eastern Time Display
**Problem:** Times showed in browser's local timezone (PST, CST, etc.)
**Solution:** Direct ISO string parsing without timezone conversion
**Result:** All times display in Eastern Time

**Before:** `10-27 11:30` (PST - wrong)
**After:** `10-27 14:30` (ET - correct)

### 2. Position Duration - Accurate Display
**Problem:** Duration column always showed "0 bars"
**Solution:** Backend tracks position entry times and calculates duration
**Result:** Correct values like "15 bars", "42 bars"

| Symbol | Duration |
|--------|----------|
| AAPL   | 15 bars  |
| GOOGL  | 42 bars  |
| MSFT   | 8 bars   |

### 3. Real-time Updates
- SSE stream now includes `barId` in all messages
- Dashboard can track trading session progress (bar 1-391)
- Consistent timestamps across all displays

## Backward Compatibility

✅ **100% Backward Compatible**
- All existing fields preserved
- New fields are additions only
- Old clients can safely ignore new fields
- No breaking changes

## Migration

### For Backend/Docker Users:
```bash
docker pull ghcr.io/yeogirlyun/sentio-lite:latest
docker-compose down
docker-compose up -d
```

### For WebApp Users:
- No changes needed - auto-updates on page refresh
- Trade history now shows ET time automatically
- Position duration displays correctly

### For Mobile/iOS Developers:
**New fields available:**
- `barId` - Show trading session progress
- `barsHeld` - Display "time in position"
- `stopPrice`/`targetPrice` - Visualize risk levels

**Example UI:**
```
Position: AAPL +$255.00
Held: 15 bars (15 minutes)
Entry: $150.25 | Stop: $148.00 | Target: $153.50
Bar: 301/391 (77% through trading day)
```

## Summary

**Kafka Messages:**
- Added `barId` field everywhere for session tracking
- Fixed `barsHeld` calculation (was broken, now works)
- Added risk management fields to trades

**WebApp:**
- Trade history shows Eastern Time (not local time)
- Position duration shows correct values (not "0 bars")
- All displays synchronized with `barId`

**Impact:**
- Better user experience with accurate time displays
- Enhanced risk visualization capabilities
- Consistent session tracking across all components
