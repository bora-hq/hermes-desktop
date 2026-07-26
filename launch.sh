#!/bin/bash
# Launch Hermes voice frontend locally
# Usage: ./launch.sh [port]

set -euo pipefail

PORT="${1:-8420}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
BRIDGE="$ROOT/bridge-server.py"
INDEX="$ROOT/web/index.html"

# Check bridge server exists
if [[ ! -f "$BRIDGE" ]]; then
    echo "❌ bridge-server.py not found at $BRIDGE"
    exit 1
fi

# Check Python deps
if ! python3 -c "import faster_whisper, edge_tts" 2>/dev/null; then
    echo "⚠️  Missing Python deps. Install with:"
    echo "   pip install faster-whisper edge-tts"
    echo "   (or use /usr/bin/python3.14 if system python has them)"
fi

# Start bridge server in background
echo "🚀 Starting Hermes bridge on port $PORT..."
python3 "$BRIDGE" "$PORT" > "$ROOT/bridge.log" 2>&1 &
BRIDGE_PID=$!

# Wait for bridge to be ready
sleep 2
for i in {1..10}; do
    if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null; then
        echo "✅ Bridge ready at http://127.0.0.1:$PORT"
        break
    fi
    sleep 1
done

# Serve frontend with a simple Python HTTP server
echo "🌐 Serving frontend at http://127.0.0.1:8080"
cd "$ROOT/web"
python3 -m http.server 8080 > "$ROOT/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo ""
echo "🎙️  Hermes Voice Frontend"
echo "   Frontend: http://127.0.0.1:8080"
echo "   Bridge:   http://127.0.0.1:$PORT/api/health"
echo ""
echo "Press Ctrl+C to stop both servers."

# Cleanup on exit
trap "kill $BRIDGE_PID $FRONTEND_PID 2>/dev/null; echo '👋 Stopped.'" EXIT
wait