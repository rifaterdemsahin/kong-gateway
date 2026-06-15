#!/usr/bin/env bash
set -e

echo ""
echo "=== Smart-Cost Multi-LLM Orchestrator ==="
echo ""

# Copy .env if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[setup] Created .env from .env.example (MOCK_MODE=true)"
fi

# Build and start
echo "[setup] Starting stack with docker compose..."
docker compose up --build -d

echo ""
echo "[setup] Waiting for services to be healthy..."
docker compose wait kong 2>/dev/null || sleep 15

echo ""
echo "=== All services are up ==="
echo ""
echo "  Frontend   ->  http://localhost:3000"
echo "  Kong Proxy ->  http://localhost:8000"
echo "  Kong Admin ->  http://localhost:8001"
echo "  Router API ->  http://localhost:8080"
echo ""
echo "Open http://localhost:3000 in your browser to start chatting."
echo ""
echo "To use real LLM APIs, edit .env:"
echo "  MOCK_MODE=false"
echo "  OPENAI_API_KEY=sk-..."
echo "  ANTHROPIC_API_KEY=sk-ant-..."
echo "Then restart: docker compose restart smart-router"
echo ""
