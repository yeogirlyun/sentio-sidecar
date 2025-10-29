#!/usr/bin/env python3
"""
Real-time Kafka Monitor WebApp
Streams live data from Kafka topics and displays in a web dashboard
"""

import json
import os
import subprocess
import time
from datetime import datetime
from collections import deque
from threading import Thread, Lock
from flask import Flask, render_template, Response, jsonify, request
from confluent_kafka import Consumer, KafkaError

# Initialize Flask app with explicit template and static paths
app = Flask(__name__,
           template_folder='templates',
           static_folder='static')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# Data structures to hold latest state (thread-safe)
data_lock = Lock()
latest_prices = {}  # symbol -> price data
price_history = {}  # symbol -> list of {tsET,o,h,l,c}
latest_portfolio = {}
latest_positions = {}  # symbol -> position data
# Store all recent trades for the active session (unbounded list for
# full visibility)
recent_trades = []
recent_trade_ids = set()  # Dedup and freeze annotations per trade
heartbeat_status = {"status": "waiting", "lastUpdate": None}
market_time = {"date": "", "time": "", "timestamp": ""}
current_session_date = None  # Track current trading session date
connection_info = {
    "clusterId": None,
    "messageCount": 0,
    "testDate": os.getenv("TEST_DATE", ""),
    "publisher": {"strategy": None, "env": None, "runId": None, "engine": None},
    # Enhanced fields
    "bootstrap": "",
    "mode": None,
    "throughput5s": 0.0,
    "throughput60s": 0.0,
    "latencySec": None,
    "lastByTopic": {},
    "lastTsETByTopic": {},
    "sessionStart": None,
    "uptimeSec": 0,
    "topics": []
}  # Connection debugging info

# Session management (multi-session support)
sessions = {}  # runId -> {testDate, strategy, env, firstSeen, lastSeen}
active_session_id = None  # Auto-follow latest runId or user-selected
auto_follow_latest = True  # Auto-switch to newest session
# Throughput tracking windows
recv_times_5s = deque()
recv_times_60s = deque()


# Replay control state
replay_state = {
    "status": "stopped",  # stopped, starting, running, completed, error
    "selectedDate": None,
    "availableDates": [],
    "error": None
}
replay_process = None  # Docker compose process

# Kafka configuration (prefer env overrides)
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:19092"))
# Paths for Docker Compose (short aliases to keep lines short)
PROJECT_ROOT = "/Volumes/MyBookDuo/Projects/sentio_lite"
COMPOSE_FILE = os.path.join(PROJECT_ROOT, "docker/docker-compose.unified.yml")
TOPICS = [
    'sentio.prices.minute.v1',
    'sentio.portfolio.minute.v1',
    'sentio.heartbeat.v1',
    'sentio.trades.executed.v1',
    'sentio.positions.state.v1'
]


def kafka_consumer_thread():
    """Background thread that consumes Kafka messages"""
    # Use a stable consumer group and start from latest for faster warm-up
    group_id = os.getenv('WEBAPP_CONSUMER_GROUP', 'webapp-monitor')

    consumer = Consumer({
        'bootstrap.servers': KAFKA_BOOTSTRAP,
        'group.id': group_id,
        'auto.offset.reset': 'latest',
        'enable.auto.commit': True
    })
    print(f"[webapp] Using consumer group: {group_id}", flush=True)

    consumer.subscribe(TOPICS)
    print(f"Kafka consumer subscribed to: {TOPICS}", flush=True)
    with data_lock:
        connection_info["bootstrap"] = KAFKA_BOOTSTRAP
        connection_info["topics"] = list(TOPICS)

    # Get cluster ID from broker metadata
    try:
        cluster_metadata = consumer.list_topics(timeout=5)
        with data_lock:
            connection_info["clusterId"] = cluster_metadata.cluster_id
        print(f"[webapp] Connected to Kafka cluster: {cluster_metadata.cluster_id}", flush=True)
    except Exception as e:
        print(f"[webapp] Failed to get cluster metadata: {e}", flush=True)

    message_count = 0
    while True:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            else:
                print(f"Consumer error: {msg.error()}", flush=True)
                continue

        try:
            message_count += 1
            now_ts = time.time()
            # Update sliding windows for throughput
            recv_times_5s.append(now_ts)
            recv_times_60s.append(now_ts)
            while recv_times_5s and now_ts - recv_times_5s[0] > 5:
                recv_times_5s.popleft()
            while recv_times_60s and now_ts - recv_times_60s[0] > 60:
                recv_times_60s.popleft()
            if message_count == 1 or message_count % 100 == 0:
                print(f"[webapp] Processed {message_count} messages", flush=True)

            topic = msg.topic()
            data = json.loads(msg.value().decode('utf-8'))

            # Extract publisher info from headers
            headers = {}
            if msg.headers():
                for key, value in msg.headers():
                    if value:
                        headers[key] = value.decode('utf-8')

            with data_lock:
                # Extract session info from headers
                run_id = headers.get("runId")
                test_date = headers.get("testDate") or connection_info.get("testDate", "")
                # Mode: prefer MODE env (e.g., mock-live), else header env; normalize
                mode_env = os.getenv("MODE")
                header_env = headers.get("env")
                effective = mode_env or header_env or ""
                eff_l = effective.lower()
                if eff_l in ("mock-live", "live"):
                    connection_info["mode"] = eff_l
                elif effective.upper() == "MOCK":
                    connection_info["mode"] = "mock-live"
                else:
                    connection_info["mode"] = effective
                # Per-topic last received
                connection_info["lastByTopic"][topic] = datetime.now().isoformat()
                # Last message tsET per topic (if available) and latency
                ts_et = data.get('tsET')
                if ts_et:
                    connection_info["lastTsETByTopic"][topic] = ts_et
                    try:
                        # Compute latency seconds approx (now - tsET)
                        dt = datetime.fromisoformat(ts_et.replace('Z', '+00:00'))
                        connection_info["latencySec"] = max(0.0, (datetime.now().astimezone(dt.tzinfo) - dt).total_seconds())
                    except Exception:
                        pass
                # Throughput
                connection_info["throughput5s"] = round(len(recv_times_5s) / 5.0, 2)
                connection_info["throughput60s"] = round(len(recv_times_60s) / 60.0, 2)
                # Session start/uptime
                global current_session_date
                if run_id:
                    sess = sessions.get(run_id)
                    if sess and not sess.get("firstSeenUtc"):
                        sess["firstSeenUtc"] = datetime.now().isoformat()
                        connection_info["sessionStart"] = sess["firstSeenUtc"]
                    elif sess and sess.get("firstSeenUtc"):
                        connection_info["sessionStart"] = sess["firstSeenUtc"]
                    # uptime
                    try:
                        if connection_info["sessionStart"]:
                            start_dt = datetime.fromisoformat(connection_info["sessionStart"])
                            connection_info["uptimeSec"] = int((datetime.now() - start_dt).total_seconds())
                    except Exception:
                        pass

                # Track session
                if run_id:
                    now = datetime.now().isoformat()
                    if run_id not in sessions:
                        sessions[run_id] = {
                            "runId": run_id,
                            "testDate": test_date,
                            "strategy": headers.get("strategy"),
                            "env": headers.get("env"),
                            "firstSeen": now,
                            "lastSeen": now
                        }
                        print(f"[webapp] New session detected: {run_id} (testDate={test_date})", flush=True)
                    else:
                        sessions[run_id]["lastSeen"] = now

                    # Auto-follow latest session
                    global active_session_id, auto_follow_latest
                    if auto_follow_latest:
                        if active_session_id != run_id:
                            print(f"[webapp] Switching to session: {run_id} (testDate={test_date})", flush=True)
                            # Clear data when switching sessions
                            latest_prices.clear()
                            latest_positions.clear()
                            recent_trades.clear()
                            price_history.clear()  # Also clear historical bars
                            active_session_id = run_id

                    # Filter: Skip messages from inactive sessions
                    if run_id != active_session_id:
                        continue

                # Update connection info with publisher metadata
                connection_info["messageCount"] = message_count
                if headers:
                    for key in ["strategy", "env", "runId", "engine"]:
                        if key in headers:
                            connection_info["publisher"][key] = headers[key]
                # Update market time from any message with timestamp
                ts = data.get('tsET', '')
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        new_date = dt.strftime('%Y-%m-%d')
                        market_time.update({
                            'date': new_date,
                            'time': dt.strftime('%H:%M:%S'),
                            'timestamp': ts
                        })
                    except:
                        pass

                if topic == 'sentio.prices.minute.v1':
                    symbol = data.get('symbol')
                    if symbol:
                        entry = {
                            'symbol': symbol,
                            'open': data.get('open', 0),
                            'high': data.get('high', 0),
                            'low': data.get('low', 0),
                            'close': data.get('close', 0),
                            'volume': data.get('volume', 0),
                            'timestamp': data.get('tsET', '')
                        }
                        # Optional annotation field
                        ann = data.get('annotation')
                        if ann:
                            entry['annotation'] = ann
                        sig = data.get('signal') or {}
                        if sig:
                            entry['signal'] = {
                                'probability': sig.get('probability'),
                                'confidence': sig.get('confidence'),
                                'detectors': sig.get('detectors', {})
                            }
                        latest_prices[symbol] = entry

                        # Append to server-side history (keep last ~500 points per symbol)
                        hist = price_history.get(symbol, [])
                        hist.append({
                            'tsET': entry['timestamp'],
                            'o': entry['open'],
                            'h': entry['high'],
                            'l': entry['low'],
                            'c': entry['close']
                        })
                        if len(hist) > 500:
                            hist = hist[-500:]
                        price_history[symbol] = hist

                elif topic == 'sentio.portfolio.minute.v1':
                    latest_portfolio.update({
                        'equity': data.get('equity', 0),
                        'cash': data.get('cash', 0),
                        'positions': data.get('positions', 0),
                        'totalPnl': data.get('totalPnl', 0),
                        'totalPnlPct': data.get('totalPnlPct', 0),
                        'timestamp': data.get('tsET', '')
                    })

                elif topic == 'sentio.positions.state.v1':
                    symbol = data.get('symbol')
                    if symbol and data.get('hasPosition'):
                        latest_positions[symbol] = {
                            'symbol': symbol,
                            'hasPosition': True,  # Required by front-end
                            'shares': data.get('shares', 0),
                            'entryPrice': data.get('entryPrice', 0),
                            'marketPrice': data.get('marketPrice', 0),
                            'unrealizedPnl': data.get('unrealizedPnl', 0),
                            'unrealizedPnlPct': data.get('unrealizedPnlPct', 0),
                            'barsHeld': data.get('barsHeld', 0),
                            'timestamp': data.get('tsET', ''),
                            'annotation': data.get('annotation', '')
                        }
                    elif symbol and not data.get('hasPosition'):
                        # Position closed
                        latest_positions.pop(symbol, None)

                elif topic == 'sentio.trades.executed.v1':
                    tid = data.get('tradeId', '')
                    if tid and tid in recent_trade_ids:
                        # Ignore duplicates/updates; keep original annotation
                        pass
                    else:
                        recent_trade_ids.add(tid)
                        # Prefer event-specific annotation if provided by producer
                        event_annotation = data.get('eventAnnotation') or data.get('annotation') or data.get('reason', '')
                        # Insert newest at the front to maintain reverse-chronological order
                        recent_trades.insert(0, {
                            'tradeId': tid,
                            'symbol': data.get('symbol', ''),
                            'action': data.get('action', ''),
                            'price': data.get('price', 0),
                            'shares': data.get('shares', 0),
                            'value': data.get('value', 0),
                            'pnl': data.get('pnl', 0),
                            'pnlPct': data.get('pnlPct', 0),
                            'reason': data.get('reason', ''),
                            'annotation_fixed': event_annotation,
                            'barsHeld': data.get('barsHeld', 0),
                            'timestamp': data.get('tsET', '')
                        })

                elif topic == 'sentio.heartbeat.v1':
                    heartbeat_status.update({
                        'status': data.get('status', 'unknown'),
                        'lastUpdate': datetime.now().isoformat()
                    })

        except Exception as e:
            print(f"Error processing message: {e}")


def event_stream():
    """Server-Sent Events stream"""
    last_log_time = 0
    last_cleanup_time = 0
    SESSION_TIMEOUT_SECONDS = 300  # 5 minutes

    while True:
        with data_lock:
            # Log position state every 10 seconds for debugging
            current_time = time.time()
            if current_time - last_log_time > 10:
                print(f"[webapp] DEBUG: latest_positions has {len(latest_positions)} entries: {list(latest_positions.keys())}", flush=True)
                last_log_time = current_time

            # Cleanup stale sessions every 60 seconds
            if current_time - last_cleanup_time > 60:
                now = datetime.now()
                stale_sessions = []
                for run_id, session in sessions.items():
                    last_seen = datetime.fromisoformat(session["lastSeen"])
                    age_seconds = (now - last_seen).total_seconds()
                    if age_seconds > SESSION_TIMEOUT_SECONDS:
                        stale_sessions.append(run_id)

                for run_id in stale_sessions:
                    test_date = sessions[run_id].get("testDate", "unknown")
                    print(f"[webapp] Cleaning up stale session: {run_id} (testDate={test_date}, age={int(age_seconds/60)}min)", flush=True)
                    del sessions[run_id]

                if stale_sessions:
                    print(f"[webapp] Cleaned up {len(stale_sessions)} stale sessions. Active sessions: {len(sessions)}", flush=True)

                last_cleanup_time = current_time

            # Freeze trade annotations (map annotation_fixed -> annotation)
            frozen_trades = []
            for t in list(recent_trades):
                tt = dict(t)
                if 'annotation' not in tt:
                    tt['annotation'] = tt.get('annotation_fixed', '')
                # Provide alias expected by original template
                if 'pnl' in tt and 'realizedPnL' not in tt:
                    try:
                        tt['realizedPnL'] = float(tt.get('pnl', 0))
                    except Exception:
                        tt['realizedPnL'] = 0.0
                # Refine generic 'rotation' reasons into more meaningful
                tt['reason'] = classify_reason(tt)
                frozen_trades.append(tt)

            # Portfolio aliases for original template
            portfolio_out = dict(latest_portfolio)
            if 'equity' in latest_portfolio:
                portfolio_out['totalValue'] = latest_portfolio.get('equity', 0)
            if 'totalPnl' in latest_portfolio:
                portfolio_out['dailyPnL'] = latest_portfolio.get('totalPnl', 0)
            if 'totalPnlPct' in latest_portfolio:
                try:
                    portfolio_out['dailyPnLPercent'] = float(latest_portfolio.get('totalPnlPct', 0)) * 100.0
                except Exception:
                    portfolio_out['dailyPnLPercent'] = 0.0

            # Positions aliases for original template
            positions_out = []
            for p in list(latest_positions.values()):
                q = dict(p)
                if 'marketPrice' in p:
                    q['currentPrice'] = p.get('marketPrice', 0)
                if 'unrealizedPnl' in p and 'unrealizedPnL' not in p:
                    q['unrealizedPnL'] = p.get('unrealizedPnl', 0)
                if 'unrealizedPnlPct' in p and 'unrealizedPnLPercent' not in p:
                    try:
                        q['unrealizedPnLPercent'] = float(p.get('unrealizedPnlPct', 0)) * 100.0
                    except Exception:
                        q['unrealizedPnLPercent'] = 0.0
                positions_out.append(q)

            state = {
                'prices': list(latest_prices.values()),
                'series': price_history,
                'portfolio': portfolio_out,
                'positions': positions_out,
                'trades': frozen_trades,
                'heartbeat': heartbeat_status,
                'marketTime': market_time,
                'connection': connection_info,
                'sessions': list(sessions.values()),
                'activeSessionId': active_session_id,
                'autoFollowLatest': auto_follow_latest,
                'replayState': dict(replay_state)
            }

        yield f"data: {json.dumps(state)}\n\n"
        time.sleep(1)  # Update every second


def classify_reason(trade: dict) -> str:
    """Return a more meaningful reason label based on trade context.

    Heuristics to split generic 'rotation' into subtypes:
    - Entry rotations vs Exit rotations
    - Profit-taking vs Stop-loss (if pnl sign is available)
    - Append exact hold duration in minutes when available
    """
    base = (trade.get('reason') or '').strip().lower()
    action = (trade.get('action') or '').upper()
    pnl = trade.get('pnl')
    bars = trade.get('barsHeld')

    # If producer already provided a specific reason, keep it
    if base and base not in ('rotation', 'rot', 'rotate'):
        return trade.get('reason')

    # Build subclassification
    parts = []
    if action == 'BUY':
        parts.append('Rotation Entry')
    elif action == 'SELL':
        parts.append('Rotation Exit')
    else:
        parts.append('Rotation')

    if isinstance(pnl, (int, float)):
        parts.append('Profit-Taking' if pnl >= 0 else 'Stop-Loss')

    # Append exact duration if available (barsHeld == minutes in this UI)
    if isinstance(bars, int) and bars >= 0:
        parts.append(f'\u00B7 {bars}m hold')

    return ' — '.join(parts)


@app.route('/')
def index():
    response = app.make_response(render_template('kafka_monitor.html'))
    # Prevent browser caching
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/stream')
def stream():
    response = Response(event_stream(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@app.route('/api/switch-session/<session_id>', methods=['POST'])
def switch_session(session_id):
    """Switch to a specific session (disables auto-follow)"""
    global active_session_id, auto_follow_latest
    with data_lock:
        if session_id in sessions:
            print(f"[webapp] Manual switch to session: {session_id}", flush=True)
            active_session_id = session_id
            auto_follow_latest = False
            # Clear data when manually switching
            latest_prices.clear()
            latest_positions.clear()
            latest_portfolio.clear()
            recent_trades.clear()
            price_history.clear()  # Also clear historical bars
            return {"status": "success", "activeSessionId": active_session_id}
        else:
            return {"status": "error", "message": "Session not found"}, 404


@app.route('/api/auto-follow', methods=['POST'])
def enable_auto_follow():
    """Re-enable auto-follow latest session"""
    global auto_follow_latest
    with data_lock:
        auto_follow_latest = True
        print("[webapp] Auto-follow enabled", flush=True)
        return {"status": "success", "autoFollowLatest": True}


# ===== REPLAY CONTROL FUNCTIONS =====

def get_available_dates():
    """Get available trading dates from market_data_manager.py"""
    try:
        result = subprocess.run(
            ["python3", "tools/market_data_manager.py", "--list-dates"],
            cwd="/Volumes/MyBookDuo/Projects/sentio_lite",
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            print(f"[webapp] Error getting dates: {result.stderr}", flush=True)
            return []

        # Parse output - one date per line
        dates = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            # Skip non-date lines (like the "Existing data loaded" message)
            if line and line.startswith('2025-'):
                dates.append(line)

        # Skip first day (need previous day for warmup)
        return dates[1:] if dates else []
    except Exception as e:
        print(f"[webapp] Error getting dates: {e}", flush=True)
        return []


def start_replay_background(test_date):
    """Start replay for the selected test date (runs in background thread)"""
    global replay_process

    with data_lock:
        replay_state["status"] = "starting"
        replay_state["selectedDate"] = test_date
        replay_state["error"] = None

        # Clear previous data
        latest_prices.clear()
        latest_portfolio.clear()
        latest_positions.clear()
        recent_trades.clear()
        market_time.clear()

    print(f"[webapp] Starting replay for {test_date}", flush=True)

    try:
        env = os.environ.copy()
        env["MODE"] = "mock-live"
        env["TEST_DATE"] = test_date
        # 1000ms = 1 second per bar = 1 real second per market minute
        env["REPLAY_SPEED_MS"] = "1000"

        # Ensure Kafka is running (original behavior: up + fixed wait)
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "kafka"],
            cwd=PROJECT_ROOT,
            env=env,
            timeout=30
        )
        time.sleep(5)

        # Start sidecar for replay
        replay_process = subprocess.Popen(
            [
                "docker", "compose", "-f", COMPOSE_FILE,
                "up", "sidecar"
            ],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        with data_lock:
            replay_state["status"] = "running"

        # Monitor completion
        replay_process.wait()
        with data_lock:
            if replay_state["status"] == "running":
                replay_state["status"] = "completed"
                print(f"[webapp] Replay completed for {test_date}", flush=True)

    except Exception as e:
        with data_lock:
            replay_state["status"] = "error"
            replay_state["error"] = str(e)
        print(f"[webapp] Error in replay: {e}", flush=True)


def stop_replay_background():
    """Stop the current replay (runs in background thread)"""
    global replay_process

    with data_lock:
        replay_state["status"] = "stopped"

    print("[webapp] Stopping replay", flush=True)

    try:
        if replay_process:
            replay_process.terminate()
            replay_process.wait(timeout=10)
            replay_process = None

        # Original behavior: bring entire compose stack down
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "down"],
            cwd=PROJECT_ROOT,
            timeout=30
        )
    except Exception as e:
        print(f"[webapp] Error stopping replay: {e}", flush=True)


@app.route('/api/replay/dates')
def api_replay_dates():
    """Get available trading dates"""
    dates = get_available_dates()
    with data_lock:
        replay_state["availableDates"] = dates
    return jsonify({"dates": dates})


@app.route('/api/replay/start', methods=['POST'])
def api_replay_start():
    """Start replay for selected date"""
    data = request.json
    test_date = data.get('testDate')

    if not test_date:
        return jsonify({"error": "No test date provided"}), 400

    with data_lock:
        if replay_state["status"] in ["starting", "running"]:
            return jsonify({"error": "Replay already running"}), 400

    # Start replay in background thread
    Thread(target=start_replay_background, args=(test_date,), daemon=True).start()

    return jsonify({"status": "starting", "testDate": test_date})


@app.route('/api/replay/stop', methods=['POST'])
def api_replay_stop():
    """Stop current replay"""
    Thread(target=stop_replay_background, daemon=True).start()
    return jsonify({"status": "stopping"})


@app.route('/api/replay/state')
def api_replay_state():
    """Get current replay state"""
    with data_lock:
        return jsonify(dict(replay_state))


if __name__ == '__main__':
    # Start Kafka consumer in background thread
    consumer_thread = Thread(target=kafka_consumer_thread, daemon=True)
    consumer_thread.start()

    print("Starting Kafka Monitor WebApp...")
    print("Open http://localhost:5001 in your browser")

    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
