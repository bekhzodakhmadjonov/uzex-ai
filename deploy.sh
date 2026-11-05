#!/usr/bin/env bash
# One-shot deploy to a fresh Ubuntu VM (Oracle/Azure). Usage: ./deploy.sh ubuntu@<ip>
# Requires: .env filled (GEMINI_API_KEY, DOMAIN), data/uzex.duckdb built locally.
set -euo pipefail
HOST="${1:?usage: ./deploy.sh user@host}"
test -f .env || { echo ".env missing"; exit 1; }
test -f data/uzex.duckdb || { echo "data/uzex.duckdb missing — run pipeline/build_db.py"; exit 1; }
grep -q '^DOMAIN=.\+' .env || { echo "DOMAIN not set in .env"; exit 1; }

echo "== 1/4 install docker on $HOST (idempotent)"
ssh -o StrictHostKeyChecking=accept-new "$HOST" 'command -v docker >/dev/null || (curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER)
sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
(command -v netfilter-persistent >/dev/null && sudo netfilter-persistent save) || true
mkdir -p ~/uzex-ai/data'

echo "== 2/4 sync code + database"
rsync -az --delete --exclude .venv --exclude .git --exclude uzex_spot_data --exclude analysis --exclude '__pycache__' --exclude 'data/logs' \
  ./ "$HOST":~/uzex-ai/

echo "== 3/4 build & start"
ssh "$HOST" 'cd ~/uzex-ai && sudo docker compose up -d --build && sleep 5 && sudo docker compose ps'

echo "== 4/4 health"
DOMAIN=$(grep '^DOMAIN=' .env | cut -d= -f2)
ssh "$HOST" 'curl -s http://127.0.0.1:80/api/health -H "Host: '"$DOMAIN"'" || true'; echo
echo "open: https://$DOMAIN  (TLS cert is issued on first request, give it ~30 s)"
