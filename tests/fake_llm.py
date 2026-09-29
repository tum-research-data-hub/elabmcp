"""Minimal OpenAI-compatible chat endpoint for the offline AI-tool tests.

Answers with canned JSON that depends on the system prompt, and keeps every
request so a test can assert what the tools actually sent.

    python tests/fake_llm.py --port 8097
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, Request

RECEIVED: list[dict[str, Any]] = []

REVIEW = {
    "summary": "TGA run with method reference; sample mass and pan type are documented.",
    "observations": ["method name given", "no ambient humidity recorded"],
    "risks": ["crucible material not stated"],
    "suggestions": ["add the TRIOS method id", "state the purge gas flow"],
    "completeness_score": 62,
}
TAGS = {"tags": ["tga", "calibration", "pan-type"]}
FIELDS = {"fields": [{"key": "Instrument", "value": "TGA-1", "type": "text"},
                     {"key": "Purge gas", "value": "N2 20 ml/min", "type": "text"}]}


def app() -> FastAPI:
    app = FastAPI(title="fake-llm")

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> dict[str, Any]:
        body = await request.json()
        system = " ".join(m.get("content", "") for m in body.get("messages", [])
                          if m.get("role") == "system").lower()
        RECEIVED.append({
            "ts": time.time(),
            "model": body.get("model"),
            "authorization": request.headers.get("authorization", ""),
            "json_mode": "response_format" in body,
            "temperature": body.get("temperature"),
            "prompt": "\n".join(m.get("content", "") for m in body.get("messages", [])),
        })
        if "suggest concise tags" in system:
            content: Any = TAGS
        elif "structured metadata" in system:
            content = FIELDS
        else:
            content = REVIEW
        return {"id": "chatcmpl-stub", "object": "chat.completion", "created": int(time.time()),
                "model": body.get("model") or "stub-model",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}

    @app.get("/requests")
    async def requests_() -> list[dict[str, Any]]:
        return RECEIVED

    @app.delete("/requests")
    async def clear() -> dict[str, bool]:
        RECEIVED.clear()
        return {"cleared": True}

    return app


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()
    uvicorn.run(app(), host="127.0.0.1", port=args.port, log_level="warning")
