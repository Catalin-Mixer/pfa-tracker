#!/bin/sh
# Starts the PFA tax tracker at http://127.0.0.1:8765 (does nothing if it is already running).
cd "$(dirname "$0")" || exit 1
PORT=8765
if python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT))==0 else 1)"; then
  echo "PFA tax tracker is already running at http://127.0.0.1:$PORT"
  exit 0
fi
setsid nohup python3 -m uvicorn app:app --host 127.0.0.1 --port $PORT > data/server.log 2>&1 < /dev/null &
echo "PFA tax tracker started at http://127.0.0.1:$PORT (log: data/server.log)"
