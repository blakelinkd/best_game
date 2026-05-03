#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout localhost-key.pem \
  -out localhost.pem \
  -days 825 \
  -subj "/CN=127.0.0.1" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"

chmod 600 localhost-key.pem
