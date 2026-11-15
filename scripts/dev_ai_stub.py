"""Local stand-in for the AI services, to see Jobcraft's waiting and error screens.

For design work on a synthetic demo only: it answers with filler, never real advice.
It speaks the two contracts the app calls:
  * OpenAI-compatible: GET /v1/models, POST /v1/chat/completions (plain and streamed);
    a request with a JSON schema gets the smallest answer that fits the schema; one
    without (the vacancy insights) gets plain text and so shows its error state
  * Runware System One: POST /v1/decisions
Each request first reads control.json beside --dir, so the delay and mode change while
it runs: {"delay": 20, "mode": "ok" | "fail" | "slow-stream"}.

    uv run python scripts/dev_ai_stub.py --dir /tmp/ai-stub          # port 8099
    LLM_BASE_URL=http://127.0.0.1:8099/v1 LLM_API_KEY=stub \
    RUNWARE_API_KEY=stub SYSTEMONE_URL=http://127.0.0.1:8099/v1/decisions ...
    (from a container on Docker Desktop or OrbStack: host.docker.internal instead of
    127.0.0.1; on Linux Docker it listens on loopback only, so run the app on the host)
"""

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(".")
TEXT = "Voorbeeldtekst van de lokale stand-in. Deze zin is alleen bedoeld om het scherm te vullen."


def control() -> dict:
    try:
        return json.loads((HERE / "control.json").read_text())
    except (OSError, ValueError):
        return {"delay": 5, "mode": "ok"}


def fill(schema: dict, root: dict, depth: int = 0):
    """A small value that satisfies a strict JSON schema."""
    if depth > 12:
        return None
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node.get(part, {})
        return fill(node, root, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            options = [s for s in schema[key] if s.get("type") != "null"] or schema[key]
            return fill(options[0], root, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {name: fill(sub, root, depth + 1) for name, sub in props.items()}
    if kind == "array":
        count = max(2, int(schema.get("minItems", 0)))
        if "maxItems" in schema:
            count = min(count, int(schema["maxItems"]))
        return [fill(schema.get("items", {}), root, depth + 1) for _ in range(count)]
    if kind == "integer":
        return int(schema.get("minimum", 3)) if "minimum" in schema else 3
    if kind == "number":
        return float(schema.get("minimum", 0.5)) if "minimum" in schema else 0.5
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return TEXT[: int(schema["maxLength"])] if "maxLength" in schema else TEXT


def answer(question: dict):
    kind = question.get("type")
    criteria = question.get("criteria") or []
    names = list(criteria) if isinstance(criteria, (list, dict)) else []
    if kind == "score":
        return {
            "type": "score",
            "score": 2.0,
            "confidence": 0.5,
            "probabilities": {
                str(i): (0.5 if i == 2 else round(0.5 / max(1, len(names) - 1), 3))
                for i in range(len(names))
            },
        }
    if kind == "choice":
        return {
            "type": "choice",
            "choice": names[0] if names else "overig",
            "confidence": 0.6,
            "probabilities": {n: round(1 / len(names), 3) for n in names},
        }
    return {"type": "noul", "noul": 0.2, "confidence": 0.7}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(
                200, {"object": "list", "data": [{"id": "stub-model", "object": "model"}]}
            )
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        ctl = control()
        delay = float(ctl.get("delay", 5))
        mode = ctl.get("mode", "ok")
        print(time.strftime("%H:%M:%S"), self.path, "mode", mode, "delay", delay, flush=True)
        if mode != "slow-stream":
            time.sleep(delay)
        if mode == "fail":
            return self._send(500, {"error": {"message": "stand-in: deliberate failure"}})
        if self.path.endswith("/decisions"):
            questions = body.get("questions") or {}
            return self._send(
                200, {"answers": {k: answer(q) for k, q in questions.items()}, "usage": {"cost": 0}}
            )
        if not self.path.endswith("/chat/completions"):
            return self._send(404, {"error": "not found"})
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")
        text = json.dumps(fill(schema, schema), ensure_ascii=False) if schema else TEXT
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost": 0}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            step = delay / max(1, len(text) // 12) if mode == "slow-stream" else 0.03
            for i in range(0, len(text), 12):
                chunk = {"choices": [{"delta": {"content": text[i : i + 12]}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(step)
            self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(
            200,
            {
                "model": body.get("model", "stub-model"),
                "usage": usage,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                    }
                ],
            },
        )

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=".", help="folder for control.json and stub.pid")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    HERE = Path(args.dir)
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "stub.pid").write_text(str(os.getpid()))
    print("ai stub on", args.port, "control", HERE / "control.json", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
