"""
Smart-Cost LLM Router
---------------------
Receives classified requests from Kong (with X-LLM-Tier header) and
forwards them to the appropriate LLM backend, or returns mock responses
when MOCK_MODE=true (default).

Cost estimates (approximate, as of 2024):
  gpt-4o-mini              : $0.00015 / 1K input,  $0.0006 / 1K output
  claude-3-5-sonnet-20241022: $0.003   / 1K input,  $0.015  / 1K output
"""

import os
import time
import math
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx

app = FastAPI(title="Smart-Cost LLM Router")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-LLM-Tier", "X-Model", "X-Prompt-Length"],
)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Cost table ────────────────────────────────────────────────────────────────
COSTS = {
    "gpt-4o-mini": {
        "label": "GPT-4o-mini",
        "input_per_1k": 0.00015,
        "output_per_1k": 0.0006,
        "tier": "simple",
    },
    "claude-3-5-sonnet-20241022": {
        "label": "Claude 3.5 Sonnet",
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
        "tier": "complex",
    },
}

MOCK_ANSWERS = {
    "simple": (
        "Sure! Here's a concise answer to your question. "
        "I'm GPT-4o-mini — the fast, cost-efficient model Kong routed you to "
        "because your request was classified as a lightweight task. "
        "Perfect for summaries, translations, and quick Q&A."
    ),
    "complex": (
        "Great question — let me walk you through this in detail. "
        "I'm Claude 3.5 Sonnet, the powerful model Kong routed you to because "
        "your request was classified as a complex reasoning or coding task. "
        "I'm optimised for debugging, architecture decisions, multi-step analysis, "
        "and any task that demands deep understanding.\n\n"
        "**Example complex response**: Here I would provide a thorough, step-by-step "
        "breakdown with code examples, trade-off analysis, and actionable recommendations."
    ),
}


def _tokens(text: str) -> int:
    """Rough token estimate: ~1.3 tokens per word."""
    return max(1, math.ceil(len(text.split()) * 1.3))


def _cost(model: str, in_tok: int, out_tok: int) -> float:
    c = COSTS.get(model, COSTS["gpt-4o-mini"])
    return in_tok / 1000 * c["input_per_1k"] + out_tok / 1000 * c["output_per_1k"]


def _cost_summary(used_model: str, alt_model: str, in_tok: int, out_tok: int) -> dict:
    used_cost = _cost(used_model, in_tok, out_tok)
    alt_cost = _cost(alt_model, in_tok, out_tok)
    saved = alt_cost - used_cost
    pct = round(saved / alt_cost * 100, 1) if alt_cost > 0 else 0.0
    return {
        "tier": COSTS[used_model]["tier"],
        "model_used": used_model,
        "model_used_label": COSTS[used_model]["label"],
        "cost_usd": round(used_cost, 8),
        "alternative_model": alt_model,
        "alternative_cost_usd": round(alt_cost, 8),
        "savings_usd": round(saved, 8),
        "savings_pct": pct,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def _openai_response(model: str, content: str, in_tok: int, out_tok: int, smart_cost: dict) -> dict:
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok, "total_tokens": in_tok + out_tok},
        "x_smart_cost": smart_cost,
    }


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "mock_mode": MOCK_MODE}


# ── Main endpoint ─────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    tier = request.headers.get("X-LLM-Tier", "simple")
    model = request.headers.get("X-Model", "gpt-4o-mini")
    reason = request.headers.get("X-Classifier-Reason", "unknown")

    body = await request.json()
    messages = body.get("messages", [])
    prompt_text = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")
    in_tok = _tokens(prompt_text)

    # ── Mock mode (no API keys required) ──────────────────────────────────────
    if MOCK_MODE:
        content = MOCK_ANSWERS.get(tier, MOCK_ANSWERS["simple"])
        out_tok = _tokens(content)
        alt = "claude-3-5-sonnet-20241022" if tier == "simple" else "gpt-4o-mini"
        smart_cost = _cost_summary(model, alt, in_tok, out_tok)
        smart_cost["classifier_reason"] = reason
        smart_cost["mock"] = True
        return JSONResponse(_openai_response(model, content, in_tok, out_tok, smart_cost))

    # ── Real mode ─────────────────────────────────────────────────────────────
    if tier == "complex" and ANTHROPIC_API_KEY:
        return await _call_anthropic(messages, in_tok, reason)

    if OPENAI_API_KEY:
        return await _call_openai(messages, model, in_tok, reason)

    # Fallback: no keys configured
    return JSONResponse(
        {"error": "No API keys configured. Set MOCK_MODE=true or add API keys to .env."},
        status_code=500,
    )


async def _call_openai(messages: list, model: str, in_tok: int, reason: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

    usage = data.get("usage", {})
    real_in = usage.get("prompt_tokens", in_tok)
    real_out = usage.get("completion_tokens", 0)
    alt = "claude-3-5-sonnet-20241022"
    smart_cost = _cost_summary(model, alt, real_in, real_out)
    smart_cost["classifier_reason"] = reason
    data["x_smart_cost"] = smart_cost
    return JSONResponse(data)


async def _call_anthropic(messages: list, in_tok: int, reason: str):
    model = "claude-3-5-sonnet-20241022"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={"model": model, "max_tokens": 1024, "messages": messages},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["content"][0]["text"] if data.get("content") else ""
    real_in = data.get("usage", {}).get("input_tokens", in_tok)
    real_out = data.get("usage", {}).get("output_tokens", 0)
    smart_cost = _cost_summary(model, "gpt-4o-mini", real_in, real_out)
    smart_cost["classifier_reason"] = reason

    return JSONResponse(_openai_response(model, content, real_in, real_out, smart_cost))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
