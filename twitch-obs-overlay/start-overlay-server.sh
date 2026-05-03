#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f localhost.pem || ! -f localhost-key.pem ]]; then
  echo "Generating local HTTPS certificate..."
  ./make-cert.sh
fi

echo "Starting Twitch OBS overlay server..."
echo "Setup:   https://127.0.0.1:3757/setup"
echo "Overlay: https://127.0.0.1:3757/overlay"
echo
python3 server.py
