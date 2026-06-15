# Smart-Cost Multi-LLM Orchestrator — Kong Gateway PoC

## Project Purpose
Demonstrate how Kong Gateway can automatically route LLM prompts to the most
cost-effective model based on prompt complexity, transparent to the frontend.

## Architecture

```
Browser (port 3000)
  └─ POST /v1/chat/completions
       └─ Kong Gateway (port 8000)          ← classifies prompt in Lua
            └─ smart-router (port 8080)     ← calls real or mock LLM API
                 ├─ gpt-4o-mini            (simple tasks)
                 └─ claude-3-5-sonnet      (complex tasks)
```

## Services

| Service       | Port | Image / Build       | Purpose                        |
|---------------|------|---------------------|--------------------------------|
| kong          | 8000 | `kong:3.9`          | Gateway + classifier plugin    |
| kong admin    | 8001 | same                | Inspect routes/plugins         |
| smart-router  | 8080 | `./smart-router`    | LLM backend proxy              |
| frontend      | 3000 | `nginx:alpine`      | Chat UI                        |

## Key Files

- `kong/kong.yml` — Declarative Kong config (DB-less). All routing logic lives here.
- `kong/kong.yml` → `pre-function` plugin — Lua classifier that sets `X-LLM-Tier` and `X-Model` headers.
- `smart-router/app.py` — FastAPI service; routes to OpenAI or Anthropic based on headers from Kong.
- `frontend/index.html` — Single-file chat UI; calls Kong at `http://localhost:8000/v1/chat/completions`.
- `.env` — Runtime config (MOCK_MODE, API keys). Never commit real keys.

## Running

```bash
# Quick start (mock mode, no API keys needed)
./setup.sh

# Or manually
docker compose up --build

# Open the UI
open -a "Google Chrome" http://localhost:3000
```

## Environment Variables (`.env`)

| Variable           | Default | Description                              |
|--------------------|---------|------------------------------------------|
| `MOCK_MODE`        | `true`  | Return simulated responses (no API keys) |
| `OPENAI_API_KEY`   | —       | Required when MOCK_MODE=false            |
| `ANTHROPIC_API_KEY`| —       | Required when MOCK_MODE=false            |

## Classifier Logic (Kong pre-function Lua plugin)

Located in `kong/kong.yml` under the `pre-function` plugin.

- Reads the request body via `ngx.req.read_body()`
- Matches against a keyword list (debug, code, algorithm, analyze, etc.)
- Prompts > 400 chars are also classified as complex
- Sets headers: `X-LLM-Tier` (simple|complex), `X-Model`, `X-Classifier-Reason`

## Cost Model (approximate, 2024 pricing)

| Model                      | Input / 1K tokens | Output / 1K tokens |
|----------------------------|-------------------|---------------------|
| gpt-4o-mini                | $0.00015          | $0.0006             |
| claude-3-5-sonnet-20241022 | $0.003            | $0.015              |

Routing simple tasks to gpt-4o-mini saves ~95% vs always using Claude.

## Common Commands

```bash
# View Kong logs (see classifier decisions)
docker compose logs kong -f

# Inspect effective Kong config
curl http://localhost:8001/services | jq

# Restart router after changing .env
docker compose restart smart-router

# Tear down
docker compose down
```

## Development Notes

- Kong runs in **DB-less mode** (`KONG_DATABASE=off`). All config is in `kong/kong.yml`.
- To change routing keywords, edit the `pre-function` access script in `kong/kong.yml` and run `docker compose restart kong`.
- The `smart-router` service exposes `/health` for Docker healthcheck.
- CORS is handled by the Kong `cors` plugin (all origins allowed for PoC).
